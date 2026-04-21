# config/settings.py
# ─────────────────────────────────────────────────
# Central config for the trading system.
# Edit these values to customise behaviour.
# ─────────────────────────────────────────────────

# ── Database ──────────────────────────────────────
DATABASE_PATH = "data/trading_system.db"

# ── Scraping schedule (24h format) ────────────────
SCREENER_SCRAPE_TIMES  = ["08:00", "12:00", "16:30"]   # market open, midday, close
INSIDER_SCRAPE_TIMES   = ["09:00", "17:00"]             # morning + after-hours sweep

# ── FinViz sectors to track (one at a time, Phase 1) ──
SECTORS = [
    "Technology",
    "Healthcare",
    "Financial",
    "Consumer Cyclical",
    "Industrials",
    "Energy",
    "Real Estate",
    "Utilities",
    "Communication Services",
    "Consumer Defensive",
    "Basic Materials",
]

# ── Screener columns to capture ──────────────────
# Full list: ticker, company, sector, industry, country, market cap,
# P/E, price, change, volume and key technicals
SCREENER_COLUMNS = [
    "Ticker", "Company", "Sector", "Industry", "Country",
    "Market Cap", "P/E", "Price", "Change", "Volume",
    "EPS growth this year", "EPS growth next year",
    "Sales growth past 5 years", "Return on Equity",
    "Insider Ownership", "Insider Transactions",
    "Short Interest", "Analyst Recom",
    "RSI (14)", "Rel Volume", "Avg Volume",
    "50-Day SMA", "200-Day SMA",
    "52-Week High", "52-Week Low",
    "Beta",
]

# ── Insider trading filters ───────────────────────
INSIDER_TRANSACTION_TYPES = [
    "Buy",          # open-market purchases (strongest signal)
    "Sale",         # open-market sales
    "Option Exercise",
]

# Flag a cluster buy signal when N insiders buy in X days
INSIDER_CLUSTER_BUY_COUNT = 3
INSIDER_CLUSTER_DAYS      = 10

# ── Logging ──────────────────────────────────────
LOG_DIR   = "logs"
LOG_LEVEL = "INFO"   # DEBUG | INFO | WARNING | ERROR

# ── Request headers (rotate to avoid blocks) ─────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

REQUEST_DELAY_SECONDS = 2.5   # polite crawl delay between requests
REQUEST_TIMEOUT       = 20
