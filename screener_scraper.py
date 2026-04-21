# scrapers/screener_scraper.py
# ─────────────────────────────────────────────────
# FinViz screener scraper using finvizfinance.
# Scrapes all configured sectors and writes to DB.
# ─────────────────────────────────────────────────

import time
import logging
import random
from datetime import datetime

import pandas as pd
from finvizfinance.screener.overview  import Overview
from finvizfinance.screener.financial import Financial
from finvizfinance.screener.technical import Technical

logger = logging.getLogger(__name__)

# ── Column normalisation maps ─────────────────────────────────────
# finvizfinance returns slightly different column names across views;
# we unify them here into our DB schema keys.

OVERVIEW_MAP = {
    "Ticker":    "ticker",
    "Company":   "company",
    "Sector":    "sector",
    "Industry":  "industry",
    "Country":   "country",
    "Market Cap":"market_cap",
    "P/E":       "pe_ratio",
    "Price":     "price",
    "Change":    "change_pct",
    "Volume":    "volume",
}

FINANCIAL_MAP = {
    "Ticker":                    "ticker",
    "EPS growth this year":      "eps_growth_this_yr",
    "EPS growth next year":      "eps_growth_next_yr",
    "Sales growth past 5 years": "sales_growth_5yr",
    "Return on Equity":          "roe",
    "Insider Ownership":         "insider_own_pct",
    "Insider Transactions":      "insider_transactions",
    "Short Interest":            "short_interest_pct",
    "Analyst Recom":             "analyst_recom",
}

TECHNICAL_MAP = {
    "Ticker":      "ticker",
    "RSI (14)":    "rsi_14",
    "Rel Volume":  "rel_volume",
    "Avg Volume":  "avg_volume",
    "20-Day SMA":  None,          # not stored separately
    "50-Day SMA":  "sma_50_pct",
    "200-Day SMA": "sma_200_pct",
    "52W High":    "high_52w_pct",
    "52W Low":     "low_52w_pct",
    "Beta":        "beta",
}


def _pct_to_float(val) -> float | None:
    """Convert '12.34%' or '-5.6%' strings to float. Returns None if unparseable."""
    if val is None or val == "-" or val == "":
        return None
    try:
        return float(str(val).replace("%", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _to_int(val) -> int | None:
    try:
        return int(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _to_float(val) -> float | None:
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _normalise_overview(df: pd.DataFrame) -> dict[str, dict]:
    """Return dict keyed by ticker with overview fields."""
    result = {}
    for _, row in df.iterrows():
        t = str(row.get("Ticker", "")).strip()
        if not t:
            continue
        result[t] = {
            "ticker":     t,
            "company":    row.get("Company"),
            "sector":     row.get("Sector"),
            "industry":   row.get("Industry"),
            "country":    row.get("Country"),
            "market_cap": row.get("Market Cap"),
            "pe_ratio":   _to_float(row.get("P/E")),
            "price":      _to_float(row.get("Price")),
            "change_pct": _pct_to_float(row.get("Change")),
            "volume":     _to_int(row.get("Volume")),
        }
    return result


def _normalise_financial(df: pd.DataFrame) -> dict[str, dict]:
    result = {}
    for _, row in df.iterrows():
        t = str(row.get("Ticker", "")).strip()
        if not t:
            continue
        result[t] = {
            "eps_growth_this_yr":  _pct_to_float(row.get("EPS growth this year")),
            "eps_growth_next_yr":  _pct_to_float(row.get("EPS growth next year")),
            "sales_growth_5yr":    _pct_to_float(row.get("Sales growth past 5 years")),
            "roe":                 _pct_to_float(row.get("Return on Equity")),
            "insider_own_pct":     _pct_to_float(row.get("Insider Ownership")),
            "insider_transactions":row.get("Insider Transactions"),
            "short_interest_pct":  _pct_to_float(row.get("Short Interest")),
            "analyst_recom":       _to_float(row.get("Analyst Recom")),
        }
    return result


def _normalise_technical(df: pd.DataFrame) -> dict[str, dict]:
    result = {}
    for _, row in df.iterrows():
        t = str(row.get("Ticker", "")).strip()
        if not t:
            continue
        result[t] = {
            "rsi_14":      _to_float(row.get("RSI (14)")),
            "rel_volume":  _to_float(row.get("Rel Volume")),
            "avg_volume":  _to_int(row.get("Avg Volume")),
            "sma_50_pct":  _pct_to_float(row.get("50-Day SMA")),
            "sma_200_pct": _pct_to_float(row.get("200-Day SMA")),
            "high_52w_pct":_pct_to_float(row.get("52W High")),
            "low_52w_pct": _pct_to_float(row.get("52W Low")),
            "beta":        _to_float(row.get("Beta")),
        }
    return result


def _fetch_with_retry(view_obj, filters_dict: dict, retries: int = 3, delay: float = 5.0):
    """Fetch a finviz view with retry on failure."""
    for attempt in range(retries):
        try:
            view_obj.set_filter(filters_dict=filters_dict)
            df = view_obj.screener_view()
            return df
        except Exception as e:
            logger.warning(f"Attempt {attempt+1}/{retries} failed: {e}")
            if attempt < retries - 1:
                time.sleep(delay + random.uniform(0, 2))
    return None


def scrape_sector(sector: str, delay: float = 2.5) -> list[dict]:
    """
    Scrape a single sector across three views (overview, financial, technical)
    and merge into a unified list of row dicts ready for DB insertion.

    Returns list of dicts, one per ticker.
    """
    filters = {"Sector": sector}
    logger.info(f"Scraping sector: {sector}")

    # ── Overview ──────────────────────────────────
    ov_df = _fetch_with_retry(Overview(), filters)
    if ov_df is None or ov_df.empty:
        logger.error(f"No overview data for {sector}")
        return []
    overview = _normalise_overview(ov_df)
    logger.info(f"  {sector}: {len(overview)} tickers from overview")
    time.sleep(delay + random.uniform(0, 1))

    # ── Financial ─────────────────────────────────
    fin_df = _fetch_with_retry(Financial(), filters)
    financial = _normalise_financial(fin_df) if fin_df is not None and not fin_df.empty else {}
    time.sleep(delay + random.uniform(0, 1))

    # ── Technical ─────────────────────────────────
    tech_df = _fetch_with_retry(Technical(), filters)
    technical = _normalise_technical(tech_df) if tech_df is not None and not tech_df.empty else {}
    time.sleep(delay + random.uniform(0, 1))

    # ── Merge all three views on ticker ───────────
    rows = []
    for ticker, base in overview.items():
        row = {**base}
        row.update(financial.get(ticker, {}))
        row.update(technical.get(ticker, {}))

        # Fill any missing fields with None so DB insert doesn't fail
        defaults = {
            "eps_growth_this_yr": None, "eps_growth_next_yr": None,
            "sales_growth_5yr": None, "roe": None,
            "insider_own_pct": None, "insider_transactions": None,
            "short_interest_pct": None, "analyst_recom": None,
            "rsi_14": None, "rel_volume": None, "avg_volume": None,
            "sma_50_pct": None, "sma_200_pct": None,
            "high_52w_pct": None, "low_52w_pct": None, "beta": None,
        }
        for k, v in defaults.items():
            row.setdefault(k, v)

        rows.append(row)

    logger.info(f"  {sector}: {len(rows)} complete rows merged")
    return rows


def scrape_all_sectors(sectors: list[str], delay: float = 2.5) -> dict[str, list[dict]]:
    """Scrape all sectors and return dict {sector: [rows]}."""
    results = {}
    for sector in sectors:
        try:
            rows = scrape_sector(sector, delay=delay)
            results[sector] = rows
        except Exception as e:
            logger.error(f"Failed to scrape sector {sector}: {e}")
            results[sector] = []
        # Extra pause between sectors
        time.sleep(delay * 2)
    return results
