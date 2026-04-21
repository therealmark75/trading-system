# scrapers/insider_scraper.py
# ─────────────────────────────────────────────────
# FinViz insider trading scraper.
# Also detects cluster-buy signals and writes them.
# ─────────────────────────────────────────────────

import time
import logging
import random
from datetime import datetime, timedelta
from collections import defaultdict

import pandas as pd
from finvizfinance.insider import Insider

logger = logging.getLogger(__name__)

# Transaction type normalisation
TYPE_MAP = {
    "Buy":              "Buy",
    "Sale":             "Sale",
    "Sale+OE":          "Sale",
    "Option Exercise":  "Option Exercise",
    "OE":               "Option Exercise",
    "Gift":             "Gift",
}


def _to_int(val) -> int | None:
    try:
        return int(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _to_float(val) -> float | None:
    try:
        return float(str(val).replace(",", "").replace("$", "").strip())
    except (ValueError, TypeError):
        return None


def _parse_date(val) -> str | None:
    """Parse dates like 'Apr 21 24' → '2024-04-21'."""
    if not val or val == "-":
        return None
    for fmt in ("%b %d %y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(val).strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return str(val).strip()


def _normalise_insider_row(row: pd.Series) -> dict | None:
    """Map a raw finviz insider row to our DB schema."""
    ticker = str(row.get("Ticker", "")).strip()
    if not ticker:
        return None

    raw_type = str(row.get("Transaction", "")).strip()
    tx_type  = TYPE_MAP.get(raw_type, raw_type)

    shares = _to_int(row.get("Shares"))
    price  = _to_float(row.get("Value"))   # finviz calls it Value but it's $/share sometimes
    value  = _to_float(row.get("Value #")) # total dollar value

    return {
        "ticker":           ticker,
        "company":          row.get("Owner"),          # finviz field name quirk
        "insider_name":     row.get("Owner"),
        "insider_title":    row.get("Relationship"),
        "transaction_date": _parse_date(row.get("Date")),
        "transaction_type": tx_type,
        "shares":           shares,
        "price":            _to_float(row.get("Cost")),
        "value":            value,
        "shares_total":     _to_int(row.get("Shares Total")),
        "sec_form":         row.get("SEC Form 4"),
    }


def scrape_insider_trades(transaction_type: str = "ALL", retries: int = 3) -> list[dict]:
    """
    Scrape the FinViz insider trading page.

    transaction_type: 'ALL' | 'Buy' | 'Sale' | 'Option Exercise'
    Returns list of normalised row dicts.
    """
    logger.info(f"Scraping insider trades (type={transaction_type})")

    for attempt in range(retries):
        try:
            insider = Insider(option=transaction_type)
            df = insider.get_insider()

            if df is None or df.empty:
                logger.warning("Empty insider dataframe returned")
                return []

            logger.info(f"Raw insider rows: {len(df)}")
            rows = []
            for _, row in df.iterrows():
                normalised = _normalise_insider_row(row)
                if normalised:
                    rows.append(normalised)

            logger.info(f"Normalised insider rows: {len(rows)}")
            return rows

        except Exception as e:
            logger.warning(f"Insider scrape attempt {attempt+1}/{retries} failed: {e}")
            if attempt < retries - 1:
                time.sleep(5 + random.uniform(0, 3))

    return []


def detect_cluster_signals(
    trades: list[dict],
    window_days: int = 10,
    min_insiders: int = 3,
    signal_type: str = "Buy",
) -> list[dict]:
    """
    Scan a list of trade dicts and return cluster signal dicts
    wherever >= min_insiders insiders made the same transaction_type
    in the same ticker within window_days.

    Returns list of signal dicts ready for DB insertion.
    """
    # Filter to signal_type only
    relevant = [t for t in trades if t.get("transaction_type") == signal_type
                and t.get("transaction_date")]

    # Group by ticker
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for trade in relevant:
        by_ticker[trade["ticker"]].append(trade)

    cutoff = datetime.utcnow() - timedelta(days=window_days)
    signals = []

    for ticker, ticker_trades in by_ticker.items():
        # Filter to within window
        recent = []
        for t in ticker_trades:
            try:
                td = datetime.strptime(t["transaction_date"], "%Y-%m-%d")
                if td >= cutoff:
                    recent.append(t)
            except (ValueError, TypeError):
                continue

        # Deduplicate by insider name (one person shouldn't count twice)
        unique_insiders = {t["insider_name"] for t in recent if t.get("insider_name")}

        if len(unique_insiders) >= min_insiders:
            total_value = sum(
                t.get("value") or 0
                for t in recent
                if t.get("insider_name") in unique_insiders
            )
            signal = {
                "detected_at":  datetime.utcnow().isoformat(),
                "ticker":       ticker,
                "signal_type":  f"CLUSTER_{signal_type.upper()}",
                "insider_count":len(unique_insiders),
                "total_value":  total_value,
                "window_days":  window_days,
                "notes":        f"Insiders: {', '.join(sorted(unique_insiders))}",
            }
            signals.append(signal)
            logger.info(
                f"CLUSTER SIGNAL: {ticker} | {len(unique_insiders)} {signal_type}s | "
                f"${total_value:,.0f} total"
            )

    return signals


def scrape_all_insider_types(delay: float = 2.5) -> list[dict]:
    """
    Scrape Buy, Sale, and Option Exercise in one pass.
    Deduplicates and returns combined list.
    """
    all_rows = []
    seen     = set()

    for tx_type in ("Buy", "Sale", "Option Exercise"):
        rows = scrape_insider_trades(transaction_type=tx_type)
        for row in rows:
            key = (
                row.get("ticker"),
                row.get("insider_name"),
                row.get("transaction_date"),
                row.get("transaction_type"),
                row.get("shares"),
            )
            if key not in seen:
                seen.add(key)
                all_rows.append(row)
        time.sleep(delay + random.uniform(0, 1))

    logger.info(f"Total unique insider trades fetched: {len(all_rows)}")
    return all_rows
