# scrapers/news_scraper.py
# ─────────────────────────────────────────────────
# Phase 3: News sentiment scraper.
# Pulls headlines from FinViz and Yahoo Finance,
# scores sentiment per ticker, stores in DB.
# ─────────────────────────────────────────────────

import time
import random
import logging
import requests
from datetime import datetime, timezone
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://finviz.com/",
}

# ── Sentiment word lists ──────────────────────────
BULLISH_WORDS = {
    "beat", "beats", "record", "surge", "surges", "soar", "soars", "rally",
    "rallies", "upgrade", "upgraded", "outperform", "buy", "bullish",
    "growth", "profit", "revenue", "strong", "positive", "exceed", "exceeds",
    "raises", "raise", "guidance", "expansion", "partnership", "breakthrough",
    "launch", "wins", "award", "contract", "acquisition", "dividend",
    "buyback", "repurchase", "approval", "approved", "fda", "deal",
}

BEARISH_WORDS = {
    "miss", "misses", "missed", "decline", "declines", "fall", "falls",
    "drop", "drops", "downgrade", "downgraded", "underperform", "sell",
    "bearish", "loss", "losses", "weak", "negative", "cut", "cuts",
    "lowers", "lower", "guidance", "layoff", "layoffs", "recall",
    "investigation", "lawsuit", "fine", "penalty", "fraud", "delay",
    "delays", "warning", "risk", "concern", "concerns", "disappoints",
    "disappointing", "restructuring", "bankruptcy",
}


def score_headline(headline: str) -> float:
    """
    Score a single headline from -1.0 (very bearish) to +1.0 (very bullish).
    Simple keyword approach - fast and transparent.
    """
    if not headline:
        return 0.0

    words  = set(headline.lower().split())
    bull   = len(words & BULLISH_WORDS)
    bear   = len(words & BEARISH_WORDS)
    total  = bull + bear

    if total == 0:
        return 0.0

    return round((bull - bear) / total, 3)


def scrape_finviz_news(ticker: str) -> list[dict]:
    """Scrape news headlines for a single ticker from FinViz."""
    url = f"https://finviz.com/quote.ashx?t={ticker}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # FinViz news table
        news_table = soup.find("table", {"id": "news-table"})
        if not news_table:
            return []

        rows     = news_table.find_all("tr")
        articles = []
        last_date = None

        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            date_cell = cells[0].get_text(strip=True)
            headline  = cells[1].get_text(strip=True)
            link_tag  = cells[1].find("a")
            url_str   = link_tag["href"] if link_tag else ""

            # Date cell is sometimes just time (HH:MM) on same-day rows
            if len(date_cell) > 8:
                last_date = date_cell.split()[0]

            articles.append({
                "ticker":    ticker,
                "headline":  headline,
                "url":       url_str,
                "source":    "finviz",
                "published": last_date or "",
                "sentiment": score_headline(headline),
            })

        return articles[:20]   # cap at 20 most recent

    except Exception as e:
        logger.warning(f"FinViz news scrape failed for {ticker}: {e}")
        return []


def scrape_yahoo_news(ticker: str) -> list[dict]:
    """Scrape news headlines from Yahoo Finance RSS feed."""
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
    try:
        resp = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/rss+xml,application/xml,text/xml",
        }, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "xml")

        articles = []
        for item in soup.find_all("item")[:15]:
            headline = item.find("title")
            pub_date = item.find("pubDate")
            link     = item.find("link")

            if not headline:
                continue

            hl_text = headline.get_text(strip=True)
            articles.append({
                "ticker":    ticker,
                "headline":  hl_text,
                "url":       link.get_text(strip=True) if link else "",
                "source":    "yahoo",
                "published": pub_date.get_text(strip=True) if pub_date else "",
                "sentiment": score_headline(hl_text),
            })

        return articles

    except Exception as e:
        logger.warning(f"Yahoo news scrape failed for {ticker}: {e}")
        return []


def scrape_ticker_news(ticker: str, delay: float = 1.5) -> list[dict]:
    """Fetch news from both sources and combine."""
    articles = scrape_finviz_news(ticker)
    time.sleep(delay + random.uniform(0, 0.5))

    yahoo = scrape_yahoo_news(ticker)
    time.sleep(delay * 0.5)

    # Deduplicate by headline similarity
    seen_headlines = {a["headline"][:40] for a in articles}
    for a in yahoo:
        if a["headline"][:40] not in seen_headlines:
            articles.append(a)
            seen_headlines.add(a["headline"][:40])

    return articles


def compute_ticker_sentiment(articles: list[dict]) -> dict:
    """
    Aggregate sentiment across all articles for a ticker.
    Returns summary dict with avg_sentiment, bullish_count, bearish_count.
    """
    if not articles:
        return {"avg_sentiment": 0.0, "bullish_count": 0,
                "bearish_count": 0, "neutral_count": 0, "article_count": 0}

    scores     = [a["sentiment"] for a in articles]
    avg        = round(sum(scores) / len(scores), 3)
    bullish    = sum(1 for s in scores if s > 0.1)
    bearish    = sum(1 for s in scores if s < -0.1)
    neutral    = len(scores) - bullish - bearish

    return {
        "avg_sentiment":  avg,
        "bullish_count":  bullish,
        "bearish_count":  bearish,
        "neutral_count":  neutral,
        "article_count":  len(articles),
    }


def scrape_news_for_tickers(tickers: list[str],
                             delay: float = 2.0) -> dict[str, dict]:
    """
    Scrape news + sentiment for a list of tickers.
    Returns dict: {ticker: sentiment_summary}
    Designed to run on your top signal tickers, not all 777.
    """
    results = {}
    for i, ticker in enumerate(tickers):
        logger.info(f"  News [{i+1}/{len(tickers)}]: {ticker}")
        articles  = scrape_ticker_news(ticker, delay=delay)
        sentiment = compute_ticker_sentiment(articles)
        results[ticker] = {**sentiment, "articles": articles}
        time.sleep(delay + random.uniform(0, 1))

    return results
