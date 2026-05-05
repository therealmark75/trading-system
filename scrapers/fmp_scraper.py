"""
Financial Modeling Prep (FMP) data scraper.
Covers: earnings calendar, dividends, analyst price targets.
Free tier: 250 calls/day, no real-time data.
"""
import sqlite3
import time
import logging
import json
from datetime import datetime, timedelta

import requests

logger = logging.getLogger(__name__)

# Lazy import so settings can be missing FMP_API_KEY without crashing
def _api_key():
    try:
        from config.settings import FMP_API_KEY
        return FMP_API_KEY or ""
    except ImportError:
        return ""

FMP_BASE = "https://financialmodelingprep.com/api/v3"
_HEADERS = {"User-Agent": "SignalIntel/1.0 marknicholson75@gmail.com"}


def _get(path: str, params: dict = None, timeout: int = 20):
    key = _api_key()
    if not key:
        return None
    p = dict(params or {})
    p["apikey"] = key
    url = FMP_BASE + path
    for attempt in range(3):
        try:
            r = requests.get(url, params=p, headers=_HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                logger.warning("[FMP] Rate limited – sleeping 10s")
                time.sleep(10)
            else:
                logger.warning(f"[FMP] HTTP {r.status_code}: {path}")
                return None
        except Exception as e:
            logger.warning(f"[FMP] Request attempt {attempt+1} failed: {e}")
            time.sleep(2)
    return None


# ── Schema helpers ────────────────────────────────────────────────────────────

def _ensure_tables(db_path: str):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS earnings_calendar (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker        TEXT NOT NULL,
            company       TEXT,
            earnings_date TEXT,
            timing        TEXT,
            period_ending TEXT,
            eps_estimate  REAL,
            eps_last_year REAL,
            revenue_estimate REAL,
            last_updated  TEXT,
            UNIQUE(ticker, earnings_date)
        );

        CREATE TABLE IF NOT EXISTS dividends (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker              TEXT NOT NULL UNIQUE,
            company             TEXT,
            sector              TEXT,
            dividend_yield      REAL,
            annual_dividend     REAL,
            payout_ratio        REAL,
            ex_dividend_date    TEXT,
            payment_date        TEXT,
            frequency           TEXT,
            dividend_growth_5yr REAL,
            consecutive_years   INTEGER,
            last_updated        TEXT
        );

        CREATE TABLE IF NOT EXISTS fmp_price_targets (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker         TEXT NOT NULL UNIQUE,
            price_target   REAL,
            analyst_count  INTEGER,
            last_updated   TEXT
        );
    """)
    conn.commit()
    conn.close()


# ── Earnings calendar ─────────────────────────────────────────────────────────

def fetch_earnings_calendar(from_date: str = None, to_date: str = None) -> list[dict]:
    """
    Fetch upcoming earnings from FMP.
    Default: today → 30 days out.
    """
    if not from_date:
        from_date = datetime.now().strftime("%Y-%m-%d")
    if not to_date:
        to_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

    data = _get("/earning_calendar", {"from": from_date, "to": to_date})
    if not data:
        return []

    rows = []
    for item in data:
        rows.append({
            "ticker":          item.get("symbol", ""),
            "company":         item.get("name", ""),
            "earnings_date":   item.get("date", ""),
            "timing":          _parse_timing(item.get("time", "")),
            "period_ending":   item.get("fiscalDateEnding", ""),
            "eps_estimate":    item.get("epsEstimated"),
            "eps_last_year":   item.get("eps"),
            "revenue_estimate": item.get("revenueEstimated"),
            "last_updated":    datetime.now().isoformat(),
        })
    return rows


def _parse_timing(raw: str) -> str:
    raw = (raw or "").lower()
    if "bmo" in raw or "before" in raw:
        return "BMO"
    if "amc" in raw or "after" in raw:
        return "AMC"
    return "TBA"


def save_earnings_calendar(db_path: str, rows: list[dict]):
    if not rows:
        return 0
    _ensure_tables(db_path)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    inserted = 0
    for r in rows:
        if not r.get("ticker") or not r.get("earnings_date"):
            continue
        try:
            c.execute("""
                INSERT OR REPLACE INTO earnings_calendar
                    (ticker, company, earnings_date, timing, period_ending,
                     eps_estimate, eps_last_year, revenue_estimate, last_updated)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (r["ticker"], r.get("company"), r["earnings_date"],
                  r.get("timing"), r.get("period_ending"),
                  r.get("eps_estimate"), r.get("eps_last_year"),
                  r.get("revenue_estimate"), r.get("last_updated")))
            inserted += 1
        except Exception as e:
            logger.warning(f"[FMP] earnings insert error {r.get('ticker')}: {e}")
    conn.commit()
    conn.close()
    return inserted


def get_earnings_calendar(db_path: str, from_date: str = None, to_date: str = None) -> list[dict]:
    """Read cached earnings from DB, joining with signal_scores for rating."""
    _ensure_tables(db_path)
    if not from_date:
        from_date = datetime.now().strftime("%Y-%m-%d")
    if not to_date:
        to_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT ec.*,
               sig.rating, sig.composite_score,
               ss.pe_ratio, ss.eps_growth_next_yr
        FROM earnings_calendar ec
        LEFT JOIN (
            SELECT ticker, rating, MAX(composite_score) as composite_score
            FROM signal_scores GROUP BY ticker
        ) sig ON ec.ticker = sig.ticker
        LEFT JOIN (
            SELECT ticker, pe_ratio, eps_growth_next_yr
            FROM screener_snapshots
            WHERE scraped_at >= datetime('now','-2 days')
            GROUP BY ticker
        ) ss ON ec.ticker = ss.ticker
        WHERE ec.earnings_date >= ? AND ec.earnings_date <= ?
        ORDER BY ec.earnings_date ASC, ec.ticker ASC
    """, (from_date, to_date))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


# ── Dividends ─────────────────────────────────────────────────────────────────

def fetch_dividend_profile(ticker: str) -> dict | None:
    """
    Fetch dividend data for a single ticker from FMP /profile.
    Returns normalised dict or None.
    """
    data = _get(f"/profile/{ticker}")
    if not data or not isinstance(data, list) or not data[0]:
        return None
    p = data[0]

    yield_pct     = p.get("lastDiv") and p.get("price") and (p["lastDiv"] / p["price"]) * 400  # quarterly→annual
    annual_div    = p.get("lastDiv") and p["lastDiv"] * 4
    payout        = p.get("payoutRatio")

    return {
        "ticker":            ticker,
        "company":           p.get("companyName"),
        "sector":            p.get("sector"),
        "dividend_yield":    round(float(p.get("dividendYield") or 0) * 100, 2),
        "annual_dividend":   round(float(p.get("lastDiv") or 0) * 4, 4) if p.get("lastDiv") else None,
        "payout_ratio":      round(float(payout) * 100, 1) if payout else None,
        "ex_dividend_date":  None,
        "payment_date":      None,
        "frequency":         None,
        "dividend_growth_5yr": None,
        "consecutive_years": None,
        "last_updated":      datetime.now().isoformat(),
    }


def fetch_dividend_history(ticker: str) -> dict:
    """
    Fetch dividend history from FMP to derive frequency, ex-date,
    payment date, growth rate, and consecutive years.
    """
    data = _get(f"/historical-price-full/stock_dividend/{ticker}")
    if not data or "historical" not in data:
        return {}

    hist = sorted(data["historical"], key=lambda x: x.get("date",""), reverse=True)
    if not hist:
        return {}

    latest     = hist[0]
    ex_date    = latest.get("date")
    pay_date   = latest.get("paymentDate") or latest.get("date")
    amount     = latest.get("adjDividend") or latest.get("dividend")

    # Determine frequency from spacing of last 4 payments
    freq = _infer_frequency(hist[:8])

    # 5-year CAGR
    growth_5yr = _calc_growth(hist, years=5)

    # Consecutive growth years
    consec = _count_consecutive_growth(hist)

    return {
        "ex_dividend_date":  ex_date,
        "payment_date":      pay_date,
        "frequency":         freq,
        "annual_dividend":   _annual_from_history(hist, freq, amount),
        "dividend_growth_5yr": growth_5yr,
        "consecutive_years": consec,
    }


def _infer_frequency(hist: list) -> str:
    if len(hist) < 2:
        return "Unknown"
    try:
        from datetime import date
        dates = [datetime.strptime(h["date"], "%Y-%m-%d").date() for h in hist[:6] if h.get("date")]
        if len(dates) < 2:
            return "Unknown"
        gaps = [(dates[i] - dates[i+1]).days for i in range(min(4, len(dates)-1))]
        avg_gap = sum(gaps) / len(gaps)
        if avg_gap < 40:    return "Monthly"
        if avg_gap < 100:   return "Quarterly"
        if avg_gap < 200:   return "Semi-Annual"
        return "Annual"
    except Exception:
        return "Unknown"


def _annual_from_history(hist: list, freq: str, latest_amt: float) -> float | None:
    if not latest_amt:
        return None
    mult = {"Monthly": 12, "Quarterly": 4, "Semi-Annual": 2, "Annual": 1}.get(freq, 4)
    return round(float(latest_amt) * mult, 4)


def _calc_growth(hist: list, years: int = 5) -> float | None:
    """CAGR of annual dividend over N years."""
    try:
        now_yr  = datetime.now().year
        past_yr = now_yr - years
        recent_divs = [h for h in hist if h.get("date","")[:4] == str(now_yr - 1)]
        past_divs   = [h for h in hist if h.get("date","")[:4] == str(past_yr)]
        if not recent_divs or not past_divs:
            return None
        r_sum = sum(float(h.get("adjDividend") or h.get("dividend") or 0) for h in recent_divs)
        p_sum = sum(float(h.get("adjDividend") or h.get("dividend") or 0) for h in past_divs)
        if p_sum <= 0 or r_sum <= 0:
            return None
        cagr = (r_sum / p_sum) ** (1.0 / years) - 1.0
        return round(cagr * 100, 1)
    except Exception:
        return None


def _count_consecutive_growth(hist: list) -> int:
    """Count consecutive years where annual dividend grew year-over-year."""
    try:
        by_year = {}
        for h in hist:
            yr = h.get("date","")[:4]
            if yr:
                amt = float(h.get("adjDividend") or h.get("dividend") or 0)
                by_year[yr] = by_year.get(yr, 0) + amt
        years = sorted(by_year.keys(), reverse=True)
        count = 0
        for i in range(len(years) - 1):
            if by_year[years[i]] > by_year[years[i+1]]:
                count += 1
            else:
                break
        return count
    except Exception:
        return 0


def save_dividend(db_path: str, row: dict):
    if not row.get("ticker"):
        return
    _ensure_tables(db_path)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO dividends
            (ticker, company, sector, dividend_yield, annual_dividend, payout_ratio,
             ex_dividend_date, payment_date, frequency, dividend_growth_5yr,
             consecutive_years, last_updated)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (row["ticker"], row.get("company"), row.get("sector"),
          row.get("dividend_yield"), row.get("annual_dividend"), row.get("payout_ratio"),
          row.get("ex_dividend_date"), row.get("payment_date"), row.get("frequency"),
          row.get("dividend_growth_5yr"), row.get("consecutive_years"), row.get("last_updated")))
    conn.commit()
    conn.close()


def get_dividends(db_path: str, min_yield: float = 0, sector: str = None,
                  rating: str = None, aristocrat: bool = False) -> list[dict]:
    """Read dividends from DB with optional filters, joined to signal_scores."""
    _ensure_tables(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    q = """
        SELECT d.*, sig.rating, sig.composite_score,
               wl.ticker IS NOT NULL as in_watchlist
        FROM dividends d
        LEFT JOIN (
            SELECT ticker, rating, MAX(composite_score) as composite_score
            FROM signal_scores GROUP BY ticker
        ) sig ON d.ticker = sig.ticker
        LEFT JOIN (SELECT DISTINCT ticker FROM watchlists) wl ON d.ticker = wl.ticker
        WHERE d.dividend_yield >= ?
    """
    params = [min_yield]
    if sector:
        q += " AND d.sector = ?"
        params.append(sector)
    if rating:
        q += " AND sig.rating = ?"
        params.append(rating)
    if aristocrat:
        q += " AND d.consecutive_years >= 25"
    q += " ORDER BY d.dividend_yield DESC"
    c.execute(q, params)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


# ── Analyst price targets ─────────────────────────────────────────────────────

def fetch_price_target(ticker: str) -> float | None:
    """Fetch consensus analyst price target from FMP."""
    data = _get(f"/price-target-consensus/{ticker}")
    if data and isinstance(data, list) and data[0]:
        t = data[0].get("targetConsensus") or data[0].get("targetMedian")
        return float(t) if t else None
    return None


def save_price_target(db_path: str, ticker: str, target: float, analyst_count: int = 0):
    _ensure_tables(db_path)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO fmp_price_targets (ticker, price_target, analyst_count, last_updated)
        VALUES (?,?,?,?)
    """, (ticker, target, analyst_count, datetime.now().isoformat()))
    conn.commit()
    conn.close()


def get_price_targets_map(db_path: str) -> dict:
    """Return {ticker: price_target} from cached fmp_price_targets."""
    _ensure_tables(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT ticker, price_target FROM fmp_price_targets
        WHERE last_updated >= datetime('now','-7 days')
    """)
    result = {r["ticker"]: r["price_target"] for r in c.fetchall()}
    conn.close()
    return result


# ── Batch jobs ────────────────────────────────────────────────────────────────

def job_refresh_earnings(db_path: str, days_ahead: int = 30) -> int:
    """Refresh the earnings calendar for the next N days. Returns rows saved."""
    from_d = datetime.now().strftime("%Y-%m-%d")
    to_d   = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    logger.info(f"[FMP] Fetching earnings calendar {from_d} → {to_d}")
    rows = fetch_earnings_calendar(from_d, to_d)
    if rows:
        n = save_earnings_calendar(db_path, rows)
        logger.info(f"[FMP] Saved {n} earnings records")
        return n
    logger.info("[FMP] No earnings data returned (check API key?)")
    return 0


def job_refresh_dividends(db_path: str, tickers: list[str] = None) -> int:
    """
    Refresh dividend data for a list of tickers.
    If tickers is None, uses all tickers in screener_snapshots that
    have a non-zero analyst_recom (proxy for well-followed stocks).
    """
    if tickers is None:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
            SELECT DISTINCT ticker FROM screener_snapshots
            WHERE scraped_at >= datetime('now','-2 days')
            ORDER BY ticker
        """)
        tickers = [r[0] for r in c.fetchall()]
        conn.close()

    logger.info(f"[FMP] Refreshing dividends for {len(tickers)} tickers")
    saved = 0
    for ticker in tickers:
        try:
            profile = fetch_dividend_profile(ticker)
            if not profile:
                time.sleep(0.3)
                continue
            hist = fetch_dividend_history(ticker)
            if hist:
                profile.update(hist)
            # Only save if there's actually a dividend
            if profile.get("dividend_yield", 0) and profile["dividend_yield"] > 0:
                save_dividend(db_path, profile)
                saved += 1
            time.sleep(0.3)
        except Exception as e:
            logger.warning(f"[FMP] Dividend fetch failed for {ticker}: {e}")
    logger.info(f"[FMP] Saved {saved} dividend records")
    return saved
