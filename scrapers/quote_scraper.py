from __future__ import annotations
# scrapers/quote_scraper.py
# ─────────────────────────────────────────────────
# Scrapes individual FinViz ticker pages for Recom.
# Two modes:
#   - Priority: watchlist + top signals, 1.5s delay (runs every scrape cycle)
#   - Bulk: all tickers, threaded, 0.5s delay (runs nightly at 02:00)
# ─────────────────────────────────────────────────

import time
import logging
import threading
from finvizfinance.quote import finvizfinance

logger = logging.getLogger(__name__)


def scrape_analyst_recom(ticker: str) -> float | None:
    """Fetch analyst recom for a single ticker. Returns float or None."""
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
    Scrape analyst recom sequentially with polite delay.
    Used for priority tickers (watchlist + top signals).
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


def scrape_recom_bulk(tickers: list[str], delay: float = 0.5, threads: int = 2) -> dict[str, float]:
    """
    Scrape analyst recom for a large list using a thread pool.
    Used for nightly bulk enrichment of all tickers.
    Each thread has its own delay to stay polite.
    """
    results = {}
    lock = threading.Lock()
    total = len(tickers)
    counter = [0]

    def worker(chunk: list[str]):
        for ticker in chunk:
            val = scrape_analyst_recom(ticker)
            with lock:
                counter[0] += 1
                if val is not None:
                    results[ticker] = val
                if counter[0] % 100 == 0:
                    logger.info(f"  Bulk recom: {counter[0]}/{total} processed, {len(results)} values so far")
            time.sleep(delay)

    # Split tickers evenly across threads
    chunk_size = max(1, len(tickers) // threads)
    chunks = [tickers[i:i+chunk_size] for i in range(0, len(tickers), chunk_size)]

    thread_list = []
    for chunk in chunks:
        t = threading.Thread(target=worker, args=(chunk,))
        t.start()
        thread_list.append(t)
        time.sleep(0.5)  # stagger thread starts

    for t in thread_list:
        t.join()

    logger.info(f"Bulk recom complete: {len(results)}/{total} values retrieved")
    return results
