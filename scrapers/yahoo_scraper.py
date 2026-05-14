"""
Yahoo Finance scraper (Phase 2a).
Covers: earnings history, financial statements, institutional holders, analyst changes.
Uses yfinance 1.2.0 (unauthenticated, free tier).

Rate-limit circuit breaker mirrors the FMP pattern in fmp_scraper.py:
module-level counter + threading.Lock, raises YahooRateLimitedError at threshold.
"""
from __future__ import annotations

import threading
import time
import logging
from datetime import datetime

import pandas as pd
import yfinance as yf

from config.constants import YAHOO_REQUEST_DELAY_SECONDS
from database.db import (
    insert_earnings_history,
    insert_financial_statements,
    insert_institutional_holders,
    insert_analyst_changes,
    upsert_external_scrape_log,
    get_active_tickers,
)

logger = logging.getLogger(__name__)

# Circuit breaker (mirrors FMP pattern in scrapers/fmp_scraper.py lines 31-37)
YAHOO_CIRCUIT_BREAKER_THRESHOLD = 10
_yahoo_429_lock = threading.Lock()
_yahoo_429_streak = 0


class YahooRateLimitedError(Exception):
    """Raised when yfinance hits consecutive rate-limit signals past the breaker threshold."""


# ── Rate-limit-aware fetch wrapper ───────────────────────────────────────────

def _safe_fetch(fetch_fn, ticker: str, data_type: str):
    """
    Wrap a yfinance fetch call. Returns fetched data or None.

    On success: resets _yahoo_429_streak to 0, returns data.
    On empty DataFrame/None: returns None without incrementing streak
      (legitimate "no data" tickers such as SPACEX are common).
    On exception containing rate-limit indicator strings: increments streak,
      sleeps 10s, raises YahooRateLimitedError at threshold.
    On other exception: logs warning, returns None without incrementing streak.
    """
    global _yahoo_429_streak
    try:
        data = fetch_fn()
        if data is None:
            return None
        if isinstance(data, pd.DataFrame) and data.empty:
            return None
        with _yahoo_429_lock:
            _yahoo_429_streak = 0
        return data
    except Exception as e:
        err_str = str(e).lower()
        if any(s in err_str for s in ("rate limit", "too many requests", "429", "try again")):
            with _yahoo_429_lock:
                _yahoo_429_streak += 1
                streak = _yahoo_429_streak
            if streak >= YAHOO_CIRCUIT_BREAKER_THRESHOLD:
                logger.error(
                    f"[Yahoo] Circuit breaker tripped: {streak} consecutive rate-limit failures. "
                    "Aborting job — re-enable once Yahoo rate limit clears."
                )
                raise YahooRateLimitedError(f"Yahoo rate limit: {streak} consecutive failures")
            logger.warning(
                f"[Yahoo] Rate limited ({streak}/{YAHOO_CIRCUIT_BREAKER_THRESHOLD}) "
                f"for {ticker} {data_type} – sleeping 10s"
            )
            time.sleep(10)
            return None
        else:
            logger.warning(f"[Yahoo] {data_type} fetch failed for {ticker}: {e}")
            return None


# ── Per-data-type fetcher functions ─────────────────────────────────────────

def fetch_earnings_history(t: yf.Ticker, ticker: str) -> list:
    """
    Fetch quarterly earnings history.
    DataFrame index: DatetimeIndex (quarter end). Columns: epsEstimate, epsActual,
    epsDifference, surprisePercent.
    Returns list of row dicts ready for insert_earnings_history.
    """
    df = _safe_fetch(lambda: t.get_earnings_history(), ticker, "EARNINGS")
    if df is None:
        return []

    rows = []
    for idx, row in df.iterrows():
        try:
            fiscal_quarter = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
        except Exception:
            fiscal_quarter = str(idx)
        rows.append({
            "ticker":           ticker,
            "fiscal_quarter":   fiscal_quarter,
            "eps_actual":       _to_float(row.get("epsActual")),
            "eps_estimate":     _to_float(row.get("epsEstimate")),
            "surprise_pct":     _to_float(row.get("surprisePercent")),
            "revenue_actual":   None,
            "revenue_estimate": None,
            "reported_at":      None,
            "source":           "yahoo",
        })
    return rows


def fetch_financial_statements(t: yf.Ticker, ticker: str) -> list:
    """
    Fetch income statement, balance sheet, and cash flow — three separate HTTP calls.
    Each DataFrame is wide-format (columns=fiscal years, rows=line items); melts to
    one row per (ticker, fiscal_year, statement_type, line_item_key).
    Returns list of row dicts ready for insert_financial_statements.
    """
    statement_map = [
        ("INCOME",   lambda: t.get_financials()),
        ("BALANCE",  lambda: t.get_balance_sheet()),
        ("CASHFLOW", lambda: t.get_cash_flow()),
    ]

    rows = []
    for stmt_type, fetch_fn in statement_map:
        df = _safe_fetch(fetch_fn, ticker, stmt_type)
        if df is None:
            continue
        for col in df.columns:
            try:
                fiscal_year = col.strftime("%Y-%m-%d") if hasattr(col, "strftime") else str(col)
            except Exception:
                fiscal_year = str(col)
            for line_item in df.index:
                try:
                    val = df.loc[line_item, col]
                    value = None if pd.isna(val) else float(val)
                except Exception:
                    value = None
                rows.append({
                    "ticker":         ticker,
                    "fiscal_year":    fiscal_year,
                    "statement_type": stmt_type,
                    "line_item_key":  str(line_item),
                    "value":          value,
                    "source":         "yahoo",
                })
    return rows


def fetch_institutional_holders(t: yf.Ticker, ticker: str) -> list:
    """
    Fetch institutional holders DataFrame.
    Columns (yfinance 1.2.0): Date Reported, Holder, Shares, % Out, Value.
    Returns list of row dicts ready for insert_institutional_holders.
    """
    df = _safe_fetch(lambda: t.get_institutional_holders(), ticker, "HOLDERS")
    if df is None:
        return []

    rows = []
    for _, row in df.iterrows():
        # Column names vary slightly; try multiple alternatives
        filing_date = _coerce_date(
            row.get("Date Reported") or row.get("dateReported") or row.get("date")
        )
        holder_name = str(row.get("Holder") or row.get("holder") or "")
        if not holder_name:
            continue
        rows.append({
            "ticker":       ticker,
            "filing_date":  filing_date or "",
            "holder_name":  holder_name,
            "shares":       _to_int(row.get("Shares") or row.get("shares")),
            "pct_out":      _to_float(row.get("% Out") or row.get("pctOut")),
            "value":        _to_float(row.get("Value") or row.get("value")),
            "source":       "yahoo",
        })
    return rows


def fetch_analyst_changes(t: yf.Ticker, ticker: str) -> list:
    """
    Fetch analyst upgrades/downgrades.
    DataFrame index: DatetimeIndex (event date). Columns: firm, toGrade, fromGrade, action.
    Returns list of row dicts ready for insert_analyst_changes.
    """
    df = _safe_fetch(lambda: t.get_upgrades_downgrades(), ticker, "ANALYST")
    if df is None:
        return []

    rows = []
    for idx, row in df.iterrows():
        try:
            event_date = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
        except Exception:
            event_date = str(idx)
        firm = str(row.get("Firm") or row.get("firm") or "")
        if not firm:
            continue
        rows.append({
            "ticker":      ticker,
            "event_date":  event_date,
            "firm":        firm,
            "from_grade":  str(row.get("fromGrade") or row.get("FromGrade") or "") or None,
            "to_grade":    str(row.get("toGrade")   or row.get("ToGrade")   or "") or None,
            "action":      str(row.get("action")    or row.get("Action")    or "") or None,
            "source":      "yahoo",
        })
    return rows


# ── Helpers ──────────────────────────────────────────────────────────────────

def _to_float(val) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return None if pd.isna(f) else f
    except Exception:
        return None


def _to_int(val) -> int | None:
    if val is None:
        return None
    try:
        return int(float(str(val).replace(",", "").strip()))
    except Exception:
        return None


def _coerce_date(val) -> str | None:
    if val is None:
        return None
    if hasattr(val, "strftime"):
        try:
            return val.strftime("%Y-%m-%d")
        except Exception:
            pass
    s = str(val).strip()
    if not s or s.lower() in ("nan", "nat", "none"):
        return None
    return s[:10] if len(s) >= 10 else s


# ── Priority ticker helper ────────────────────────────────────────────────────

def get_priority_tickers(db_path: str) -> list:
    """Return watchlist tickers + top signals tickers (same set as priority recom scrape)."""
    from database.db import get_connection
    conn = get_connection(db_path)
    cur  = conn.cursor()
    cur.execute("SELECT DISTINCT ticker FROM watchlists")
    watchlist = [r[0] for r in cur.fetchall()]
    cur.execute("""
        SELECT DISTINCT ticker FROM top_signals_of_day
        WHERE signal_date = DATE('now') LIMIT 20
    """)
    top_signals = [r[0] for r in cur.fetchall()]
    conn.close()
    return list(set(watchlist + top_signals))


def get_upcoming_earnings_tickers(db_path: str, days: int = 7) -> list:
    """Return tickers with earnings_date within the next `days` days."""
    from database.db import get_connection
    conn = get_connection(db_path)
    cur  = conn.cursor()
    cur.execute("""
        SELECT DISTINCT ticker FROM earnings_calendar
        WHERE earnings_date BETWEEN DATE('now') AND DATE('now', ?)
    """, (f"+{days} days",))
    tickers = [r[0] for r in cur.fetchall()]
    conn.close()
    return tickers
