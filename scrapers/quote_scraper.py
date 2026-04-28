from __future__ import annotations
# scrapers/quote_scraper.py
# ─────────────────────────────────────────────────
# Scrapes individual FinViz ticker pages for data
# not available in bulk screener views (e.g. Recom).
# Used for high-priority tickers: watchlist + top signals.
# ─────────────────────────────────────────────────

import time
import logging
from finvizfinance.quote import finvizfinance

logger = logging.getLogger(__name__)


def scrape_analyst_recom(ticker: str) -> float | None:
    """
    Fetch the analyst recommendation score for a single ticker.
    Returns a float (e.g. 1.89) or None on failure.
    """
    try:
        stock = finvizfinance(ticker)
        fundamentals = stock.ticker_fundament()
        raw = fundamentals.get('Recom')
        if raw and raw not in ('-', 'N/A', ''):
            return float(raw)
        return None
    except Exception as e:
        logger.warning(f"quote_scraper: failed for {ticker}: {e}")
        return None


def scrape_recom_for_tickers(tickers: list[str], delay: float = 1.5) -> dict[str, float]:
    """
    Scrape analyst recom for a list of tickers with polite delay.
    Returns dict of {ticker: recom_value}.
    """
    results = {}
    total = len(tickers)
    for i, ticker in enumerate(tickers, 1):
        logger.info(f"  Recom scrape [{i}/{total}]: {ticker}")
        val = scrape_analyst_recom(ticker)
        if val is not None:
            results[ticker] = val
        time.sleep(delay)
    logger.info(f"Recom scrape complete: {len(results)}/{total} values retrieved")
    return results
