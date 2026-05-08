# config/constants.py
# ─────────────────────────────────────────────────
# Non-secret shared constants for the trading system.
# Secrets (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, FMP_API_KEY)
# live in config/settings.py which is gitignored.
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

# ── Scoring engine version ────────────────────────
# Bump policy:
#   PATCH  (0.9.0 → 0.9.1)  : bug fixes that do NOT change scoring output
#   MINOR  (0.9.0 → 0.10.0) : new component added OR weight adjustment
#   MAJOR  (0.9.x → 1.0.0)  : engine frozen for production launch
#   MAJOR  (1.0.0 → 2.0.0)  : post-launch, breaking changes to scoring methodology
# ⚠  Bump BEFORE shipping any change that affects scoring output.
#    New data tagged with the old version is permanently mis-stamped.
SCORING_ENGINE_VERSION = "0.12.0"

# ── Signal universe constraints ───────────────────
# Floor for NEW signals only. Tickers below this price are not scored
# and will not generate new rating_changes entries. Existing watchlist
# entries that drop below threshold remain visible (mark-and-hold) but
# are flagged and receive no new signals.
# Adjust upward if backtest data shows continued distortion.
MIN_PRICE_FOR_SIGNAL = 1.00

# ── Alert thresholds ─────────────────────────────
# ALERTS_ENABLED and ALERT_CONFIG (smtp credentials) live in
# config/settings.py (gitignored) to prevent credential leakage.
# Alert thresholds - only alert when these conditions are met
ALERT_MIN_COMPOSITE_SCORE = 68.0    # minimum score for signal alert
ALERT_MIN_CLUSTER_INSIDERS = 5      # minimum insiders for cluster alert
ALERT_STRONG_BUY_ONLY = False       # True = only alert STRONG_BUY, False = BUY too

NEWS_SCRAPE_TIMES     = ["08:30", "17:30"]          # market open + close

# ── Telegram alerts ───────────────────────────────
# Max individual ticker alerts per scoring run; exceeded → summary message only
TELEGRAM_ALERT_MAX_PER_RUN = 20
