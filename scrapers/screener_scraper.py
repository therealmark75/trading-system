import time, logging, random
import pandas as pd
from finvizfinance.screener.overview  import Overview
from finvizfinance.screener.financial import Financial
from finvizfinance.screener.technical import Technical

logger = logging.getLogger(__name__)

# ── Exchange normalisation ─────────────────────────────────────────────────
# FinViz returns short codes (NASD, NYSE, AMEX) or full names. Map to the
# canonical set used throughout the codebase: NASDAQ, NYSE, AMEX, OTC, Other.
_EXCHANGE_MAP = {
    'NASD':    'NASDAQ',
    'NASDAQ':  'NASDAQ',
    'NYSE':    'NYSE',
    'NYSE MKT': 'AMEX',
    'AMEX':    'AMEX',
    'OTC':     'OTC',
    'PINK':    'OTC',
}

def _normalise_exchange(raw):
    if not raw:
        return None
    canon = _EXCHANGE_MAP.get(raw.strip().upper())
    if canon:
        return canon
    logger.warning(f"Unknown exchange value: {raw!r}")
    return 'Other'

def _scrape_exchange(soup):
    """Extract listing exchange from a FinViz quote page BeautifulSoup object.

    finvizfinance/quote.py hardcodes links[3] for Exchange, but FinViz added a
    market cap tier link at index 3 (circa 2024), pushing the exchange link to
    index 4. Searching by href pattern (f=exch_) is robust to future shifts.
    """
    try:
        ql = soup.find('div', class_='quote-links')
        if not ql:
            return None
        exch_link = next(
            (a for a in ql.find_all('a') if 'f=exch_' in (a.get('href') or '')),
            None
        )
        if not exch_link:
            return None
        return _normalise_exchange(exch_link.text.strip())
    except Exception:
        return None

def _to_int(val):
    try: return int(float(str(val).replace(",","").strip()))
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
            "exchange":   row.get("Exchange"),
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
            "short_interest_pct": _pct_field(row.get("Float Short")),
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
            # Custom.screener_view defaults limit=-1 (one page); pass explicit limit to
            # fetch all pages when column overrides are in use.
            if columns:
                df = view_obj.screener_view(columns=columns, limit=100000)
            else:
                df = view_obj.screener_view()
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

    # Fetch analyst recom + insider/short + rel_volume via custom view
    # Columns: 1=Ticker, 26=Insider Own, 27=Insider Trans, 30=Float Short, 62=Recom, 64=Rel Volume
    from finvizfinance.screener.custom import Custom
    custom_df = _fetch_with_retry(Custom(), filters, columns=[1, 26, 27, 30, 62, 64])
    custom_data = {}
    if custom_df is not None and not custom_df.empty:
        for _, row in custom_df.iterrows():
            t = str(row.get("Ticker","")).strip()
            if t:
                custom_data[t] = {
                    "analyst_recom":       _to_float(row.get("Recom")),
                    "insider_own_pct":     _pct_field(row.get("Insider Own")),
                    "insider_transactions": str(row.get("Insider Trans") or ""),
                    "short_interest_pct":  _pct_field(row.get("Short Float")),
                    "rel_volume":          _to_float(row.get("Rel Volume")),
                }
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
        if ticker in custom_data:
            row.update(custom_data[ticker])
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
    """Scrape individual FinViz ticker pages for watchlist + top signal tickers.

    Captures fields the bulk screener views can't provide (Short Ratio,
    Inst Own, Forward P/E, PEG, P/S, P/B) plus analyst recommendations.
    """
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
            fv   = finvizfinance(ticker)
            data = fv.ticker_fundament()
            row  = {}
            recom = data.get('Recom')
            if recom and recom != '-':
                try: row['analyst_recom'] = float(recom)
                except: pass
            for src_key, dst_col, parser in [
                ('Insider Own',   'insider_own_pct',   _pct_field),
                ('Insider Trans', 'insider_transactions', lambda v: str(v) if v else None),
                ('Inst Own',      'inst_own_pct',      _pct_field),
                ('Short Float',   'short_interest_pct', _pct_field),
                ('Short Ratio',   'short_ratio',       _to_float),
                ('Forward P/E',   'forward_pe',        _to_float),
                ('PEG',           'peg_ratio',         _to_float),
                ('P/S',           'price_to_sales',    _to_float),
                ('P/B',           'price_to_book',     _to_float),
            ]:
                v = data.get(src_key)
                if v and v not in ('-', '', 'N/A'):
                    parsed = parser(v)
                    if parsed is not None:
                        row[dst_col] = parsed
            # Exchange: reuse the already-fetched page; finvizfinance maps
            # links[3] to "Exchange" but FinViz now puts exchange at links[4].
            exch = _scrape_exchange(fv.soup)
            if exch:
                row['exchange'] = exch
            if row:
                results[ticker] = row
            time.sleep(0.5)
        except Exception as exc:
            logger.warning(f"[PriorityRecom] {ticker}: {exc}")

    if results:
        conn = get_connection(db_path)
        cur = conn.cursor()
        now = __import__('datetime').datetime.utcnow().isoformat()
        for ticker, row in results.items():
            # Exchange is metadata — write to ticker_metadata, not screener_snapshots.
            exch = row.pop('exchange', None)
            if exch:
                cur.execute("""
                    INSERT INTO ticker_metadata (ticker, exchange, first_seen_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(ticker) DO UPDATE SET
                        exchange   = excluded.exchange,
                        updated_at = excluded.updated_at
                """, (ticker, exch, now, now))
            # Remaining fields still go to the latest screener_snapshots row.
            if row:
                set_clause = ", ".join(f"{k} = ?" for k in row)
                vals = list(row.values()) + [ticker, ticker]
                cur.execute(f"""UPDATE screener_snapshots SET {set_clause}
                               WHERE ticker = ? AND scraped_at = (
                                   SELECT MAX(scraped_at) FROM screener_snapshots WHERE ticker = ?)""",
                            vals)
        conn.commit()
        conn.close()
        logger.info(f"  Priority scrape: updated {len(results)} tickers with short/insider/valuation data")

