import time, logging, random
import pandas as pd
from finvizfinance.screener.overview  import Overview
from finvizfinance.screener.financial import Financial
from finvizfinance.screener.technical import Technical

logger = logging.getLogger(__name__)

def _to_int(val):
    try: return int(str(val).replace(",","").strip())
    except: return None

def _to_float(val):
    try:
        v = str(val).replace(",","").replace("%","").replace("$","").strip()
        return float(v)
    except: return None

def _pct_field(val):
    # finviz returns decimals like 0.3256 for 32.56% on financial view
    # but strings like "-8.30%" on technical view - handle both
    if val is None: return None
    try:
        s = str(val).strip()
        if "%" in s: return float(s.replace("%","").replace(",",""))
        f = float(s)
        # if absolute value < 5, likely a decimal ratio - convert to pct
        if abs(f) < 5: return round(f * 100, 2)
        return f
    except: return None

def _normalise_overview(df):
    result = {}
    for _, row in df.iterrows():
        t = str(row.get("Ticker","")).strip()
        if not t: continue
        result[t] = {
            "ticker":     t,
            "company":    row.get("Company"),
            "sector":     row.get("Sector"),
            "industry":   row.get("Industry"),
            "country":    row.get("Country"),
            "market_cap": row.get("Market Cap"),
            "pe_ratio":   _to_float(row.get("P/E")),
            "price":      _to_float(row.get("Price")),
            "change_pct": _pct_field(row.get("Change")),
            "volume":     _to_int(row.get("Volume")),
        }
    return result

def _normalise_financial(df):
    result = {}
    for _, row in df.iterrows():
        t = str(row.get("Ticker","")).strip()
        if not t: continue
        result[t] = {
            "roe":                _pct_field(row.get("ROE")),
            "eps_growth_this_yr": _pct_field(row.get("EPS this Y")),
            "eps_growth_next_yr": _pct_field(row.get("EPS next Y")),
            "sales_growth_5yr":   _pct_field(row.get("Sales past 5Y")),
            "insider_own_pct":    _pct_field(row.get("Insider Own")),
            "insider_transactions": str(row.get("Insider Trans","") or ""),
            "short_interest_pct": _pct_field(row.get("Short Float")),
            "analyst_recom":      _to_float(row.get("Recom")),
        }
    return result

def _normalise_technical(df):
    result = {}
    for _, row in df.iterrows():
        t = str(row.get("Ticker","")).strip()
        if not t: continue
        result[t] = {
            "rsi_14":       _to_float(row.get("RSI")),
            "rel_volume":   _to_float(row.get("Rel Volume")),
            "avg_volume":   _to_int(row.get("Avg Volume")),
            "sma_50_pct":   _pct_field(row.get("SMA50")),
            "sma_200_pct":  _pct_field(row.get("SMA200")),
            "high_52w_pct": _pct_field(row.get("52W High")),
            "low_52w_pct":  _pct_field(row.get("52W Low")),
            "beta":         _to_float(row.get("Beta")),
        }
    return result

def _fetch_with_retry(view_obj, filters_dict, retries=3, delay=5.0, columns=None):
    for attempt in range(retries):
        try:
            view_obj.set_filter(filters_dict=filters_dict)
            df = view_obj.screener_view(columns=columns) if columns else view_obj.screener_view()
            return df
        except Exception as e:
            logger.warning(f"Attempt {attempt+1}/{retries} failed: {e}")
            if attempt < retries - 1:
                time.sleep(delay + random.uniform(0,2))
    return None

def scrape_sector(sector, delay=2.5):
    filters = {"Sector": sector}
    logger.info(f"Scraping sector: {sector}")

    ov_df = _fetch_with_retry(Overview(), filters)
    if ov_df is None or ov_df.empty:
        logger.error(f"No overview data for {sector}")
        return []
    overview = _normalise_overview(ov_df)
    logger.info(f"  {sector}: {len(overview)} tickers from overview")
    time.sleep(delay + random.uniform(0,1))

    fin_df = _fetch_with_retry(Financial(), filters)
    financial = _normalise_financial(fin_df) if fin_df is not None and not fin_df.empty else {}
    time.sleep(delay + random.uniform(0,1))

    tech_df = _fetch_with_retry(Technical(), filters)
    technical = _normalise_technical(tech_df) if tech_df is not None and not tech_df.empty else {}
    time.sleep(delay + random.uniform(0,1))

    # Fetch analyst recom via custom view (column 62)
    from finvizfinance.screener.custom import Custom
    recom_df = _fetch_with_retry(Custom(), filters, columns=[0, 62])
    recom_data = {}
    if recom_df is not None and not recom_df.empty:
        for _, row in recom_df.iterrows():
            t = str(row.get("Ticker","")).strip()
            if t:
                recom_data[t] = _to_float(row.get("Recom"))
    time.sleep(delay + random.uniform(0,1))

    rows = []
    defaults = {
        "eps_growth_this_yr":None,"eps_growth_next_yr":None,"sales_growth_5yr":None,
        "roe":None,"insider_own_pct":None,"insider_transactions":None,
        "short_interest_pct":None,"analyst_recom":None,"rsi_14":None,
        "rel_volume":None,"avg_volume":None,"sma_50_pct":None,"sma_200_pct":None,
        "high_52w_pct":None,"low_52w_pct":None,"beta":None,
    }
    for ticker, base in overview.items():
        row = {**base}
        row.update(financial.get(ticker, {}))
        row.update(technical.get(ticker, {}))
        if ticker in recom_data:
            row['analyst_recom'] = recom_data[ticker]
        for k, v in defaults.items():
            row.setdefault(k, v)
        rows.append(row)

    logger.info(f"  {sector}: {len(rows)} complete rows merged")
    return rows

def scrape_all_sectors(sectors, delay=2.5):
    results = {}
    for sector in sectors:
        try:
            results[sector] = scrape_sector(sector, delay=delay)
        except Exception as e:
            logger.error(f"Failed sector {sector}: {e}")
            results[sector] = []
        time.sleep(delay * 2)
    return results

def scrape_analyst_recom_priority(db_path):
    """Option B: scrape analyst recom for watchlist + top signal tickers."""
    from finvizfinance.quote import finvizfinance
    from database.db import get_connection
    import time

    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT ticker FROM watchlists")
    watchlist = [r[0] for r in cur.fetchall()]
    cur.execute("""SELECT DISTINCT ticker FROM top_signals_of_day 
                   WHERE signal_date = DATE('now') LIMIT 20""")
    top_signals = [r[0] for r in cur.fetchall()]
    conn.close()

    tickers = list(set(watchlist + top_signals))
    if not tickers:
        return

    results = {}
    for ticker in tickers:
        try:
            data = finvizfinance(ticker).ticker_fundament()
            recom = data.get('Recom', None)
            if recom and recom != '-':
                results[ticker] = float(recom)
            time.sleep(0.5)
        except Exception:
            pass

    if results:
        conn = get_connection(db_path)
        cur = conn.cursor()
        for ticker, recom in results.items():
            cur.execute("""UPDATE screener_snapshots SET analyst_recom = ?
                           WHERE ticker = ? AND scraped_at = (
                               SELECT MAX(scraped_at) FROM screener_snapshots WHERE ticker = ?)""",
                        (recom, ticker, ticker))
        conn.commit()
        conn.close()
        print(f"  Option B: updated {len(results)} analyst recoms")

