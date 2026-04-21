# Trading System — Phase 1
## FinViz Screener + Insider Trading Data Layer

---

## What this does

Scrapes FinViz across two data streams:

1. **Screener** — all 11 sectors, three views per sector (overview, financial, technical),
   merged into a single unified snapshot per run. Captures price, RSI, SMA, EPS growth,
   ROE, short interest, analyst recommendations, and more.

2. **Insider Trades** — buy, sale, and option exercise transactions scraped from
   `/insidertrading`. Automatically detects **cluster buy/sell signals** when 3+
   different insiders trade the same ticker within 10 days.

Everything is stored in SQLite (`data/trading_system.db`) and viewable via a
Rich terminal dashboard.

---

## Setup

```bash
# 1. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Initialise the database
python main.py run-once --sector-only Technology   # quick first test
```

---

## Usage

### Run a full one-shot scrape (all sectors + insiders)
```bash
python main.py run-once
```

### Scrape a single sector only
```bash
python main.py run-once --sector-only Healthcare
```

### Run on automatic schedule (weekdays, market hours)
```bash
python main.py scheduler
```
Screener runs at: 08:00, 12:00, 16:30 London time
Insider runs at:  09:00, 17:00 London time
(Edit `config/settings.py` to change these)

### View the dashboard
```bash
# Full dashboard
python dashboard/dashboard.py

# Just signals
python dashboard/dashboard.py signals

# Just a sector
python dashboard/dashboard.py screener --sector Technology --sort rsi_14 --top 20

# Insider buys only, last 30 days
python dashboard/dashboard.py insiders --days 30 --type Buy
```

---

## Project structure

```
trading_system/
├── main.py                    # Entry point + scheduler
├── requirements.txt
├── config/
│   └── settings.py            # All configuration here
├── scrapers/
│   ├── screener_scraper.py    # FinViz screener (3 views → merged)
│   └── insider_scraper.py     # Insider trades + cluster signals
├── database/
│   └── db.py                  # SQLite schema, inserts, queries
├── dashboard/
│   └── dashboard.py           # Rich terminal dashboard
├── data/
│   └── trading_system.db      # Auto-created on first run
└── logs/
    └── trading_system.log     # Rolling log file
```

---

## Database tables

| Table                 | Description                                      |
|-----------------------|--------------------------------------------------|
| `screener_snapshots`  | One row per ticker per scrape run                |
| `insider_trades`      | Deduplicated insider buy/sale/OE events          |
| `insider_signals`     | Cluster signals auto-detected from insider data  |
| `run_log`             | Audit trail of every scrape job                  |

---

## Configuration (config/settings.py)

| Setting                    | Default            | Description                    |
|----------------------------|--------------------|--------------------------------|
| `SECTORS`                  | All 11             | Which sectors to scrape        |
| `SCREENER_SCRAPE_TIMES`    | 08:00, 12:00, 16:30| Scheduler times (24h, weekdays)|
| `INSIDER_SCRAPE_TIMES`     | 09:00, 17:00       | Insider scrape schedule        |
| `INSIDER_CLUSTER_BUY_COUNT`| 3                  | Min insiders for cluster signal|
| `INSIDER_CLUSTER_DAYS`     | 10                 | Lookback window for clustering |
| `REQUEST_DELAY_SECONDS`    | 2.5                | Polite crawl delay             |

---

## Phase 2 (coming next)

- Momentum + Quality signal generator
- Mean reversion scanner (RSI < 30 + near 52-week low)
- Event-driven overlay from economic calendar
- Insider conviction scoring (weight by title: CEO > VP)
- Yahoo Finance + FT news sentiment layer
- Backtest runner (Vectorbt)

---

## Notes

- FinViz free tier is rate-limited. The default 2.5s delay keeps you well within
  acceptable use. If you hit 429 errors, increase `REQUEST_DELAY_SECONDS`.
- For FinViz Elite subscribers: set your `FINVIZ_AUTH_COOKIE` in a `.env` file
  to unlock export endpoints and more filter columns.
- All times are London (Europe/London). Edit the scheduler timezone in `main.py`
  if you're running from a different timezone.
