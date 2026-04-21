# scrapers/calendar_scraper.py
# ─────────────────────────────────────────────────
# Phase 3: Economic calendar scraper.
# Pulls upcoming high-impact events from FinViz,
# flags which sectors/tickers are affected.
# ─────────────────────────────────────────────────

import logging
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://finviz.com/",
}

# Map event keywords to affected sectors
EVENT_SECTOR_MAP = {
    "fed":          ["Financial", "Real Estate", "Utilities"],
    "fomc":         ["Financial", "Real Estate", "Utilities", "Technology"],
    "interest rate":["Financial", "Real Estate"],
    "inflation":    ["Consumer Cyclical", "Consumer Defensive", "Financial"],
    "cpi":          ["Consumer Cyclical", "Consumer Defensive"],
    "ppi":          ["Industrials", "Basic Materials", "Energy"],
    "gdp":          ["all"],
    "unemployment": ["Consumer Cyclical", "Financial"],
    "jobs":         ["Consumer Cyclical", "Financial"],
    "nfp":          ["Consumer Cyclical", "Financial"],
    "oil":          ["Energy"],
    "crude":        ["Energy"],
    "retail sales": ["Consumer Cyclical", "Consumer Defensive"],
    "housing":      ["Real Estate", "Financial"],
    "earnings":     ["all"],
}

# Impact level colours from FinViz
IMPACT_MAP = {
    "red":    "HIGH",
    "orange": "MEDIUM",
    "yellow": "LOW",
    "gray":   "NONE",
}


def scrape_economic_calendar(days_ahead: int = 7) -> list[dict]:
    """
    Scrape FinViz economic calendar for upcoming events.
    Returns list of event dicts.
    """
    url = "https://finviz.com/calendar.ashx"
    events = []

    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # Find calendar table
        cal_table = soup.find("table", {"class": "calendar"})
        if not cal_table:
            # Try alternative selector
            tables = soup.find_all("table")
            for t in tables:
                if "Date" in t.get_text() and "Event" in t.get_text():
                    cal_table = t
                    break

        if not cal_table:
            logger.warning("Could not find calendar table")
            return []

        rows     = cal_table.find_all("tr")
        cur_date = None

        for row in rows:
            cells = row.find_all("td")
            if not cells:
                continue

            # Date rows have fewer cells
            if len(cells) >= 2:
                date_text = cells[0].get_text(strip=True)
                if date_text and len(date_text) > 4:
                    try:
                        cur_date = datetime.strptime(date_text, "%b %d").replace(
                            year=datetime.now().year
                        ).strftime("%Y-%m-%d")
                    except ValueError:
                        pass

            if len(cells) >= 3 and cur_date:
                event_name = cells[1].get_text(strip=True) if len(cells) > 1 else ""
                if not event_name:
                    continue

                # Impact from row background colour
                impact = "MEDIUM"
                row_class = row.get("class", [])
                for cls in row_class:
                    if cls in IMPACT_MAP:
                        impact = IMPACT_MAP[cls]
                        break

                # Determine affected sectors
                affected = set()
                event_lower = event_name.lower()
                for keyword, sectors in EVENT_SECTOR_MAP.items():
                    if keyword in event_lower:
                        if "all" in sectors:
                            affected = {"all"}
                            break
                        affected.update(sectors)

                events.append({
                    "event_date":       cur_date,
                    "event_name":       event_name,
                    "impact":           impact,
                    "affected_sectors": list(affected) if affected else [],
                    "forecast":         cells[2].get_text(strip=True) if len(cells) > 2 else "",
                    "previous":         cells[3].get_text(strip=True) if len(cells) > 3 else "",
                })

    except Exception as e:
        logger.error(f"Calendar scrape failed: {e}")

    # Filter to days_ahead window
    cutoff = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    today  = datetime.now().strftime("%Y-%m-%d")
    events = [e for e in events if today <= e.get("event_date","") <= cutoff]

    logger.info(f"Economic calendar: {len(events)} events in next {days_ahead} days")
    return events


def get_earnings_calendar(days_ahead: int = 7) -> list[dict]:
    """
    Scrape upcoming earnings from FinViz earnings calendar.
    Returns list of {ticker, company, date, time (before/after market)}
    """
    url = "https://finviz.com/calendar.ashx?v=3"
    earnings = []

    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        tables = soup.find_all("table")
        for table in tables:
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue
                ticker = cells[0].get_text(strip=True)
                if not ticker or len(ticker) > 6:
                    continue
                earnings.append({
                    "ticker":  ticker,
                    "company": cells[1].get_text(strip=True) if len(cells) > 1 else "",
                    "date":    cells[2].get_text(strip=True) if len(cells) > 2 else "",
                    "timing":  cells[3].get_text(strip=True) if len(cells) > 3 else "",
                })

    except Exception as e:
        logger.warning(f"Earnings calendar scrape failed: {e}")

    return earnings


def flag_tickers_near_events(
    tickers: list[str],
    events:  list[dict],
    earnings:list[dict],
) -> dict[str, list[str]]:
    """
    For each ticker, return list of upcoming event warnings.
    Used to add context to signal output.
    """
    warnings = {}
    earnings_tickers = {e["ticker"]: e for e in earnings}

    for ticker in tickers:
        ticker_warnings = []

        # Check earnings
        if ticker in earnings_tickers:
            e = earnings_tickers[ticker]
            ticker_warnings.append(
                f"⚡ Earnings {e.get('date','')} {e.get('timing','')}"
            )

        warnings[ticker] = ticker_warnings

    return warnings
