# SignalIntel — Project Context for Claude Code

> **Before making changes, consult `docs/scoring_invariants.md`** for both data correctness rules (invariants 1–11) and development process rules (P1–P14). These rules apply to every change made in this project.
>
> **For any migration, refactor, or multi-surface change, apply P1.1 (inventory before edit), P1.2 (verify by absence), and P1.3 (audit table, not narrative). These are not optional.**

## What This Project Is
SignalIntel is a stock signal intelligence web application. Currently used as a personal backtesting and trading signal tool, with a roadmap to launch as a paid SaaS product for serious traders. The AI assistant working on this project is referred to as **Athena**.

## Server & Environment
- **Host:** Mac Mini (local server)
- **Port:** 5001
- **Virtual environment:** `~/Documents/trading-system/venv`
- **Activate venv:** `source ~/Documents/trading-system/venv/bin/activate`
- **Database:** `data/signalintel.db` (SQLite)
- **Run app:** `python web/app.py`
- **Run scheduler:** `python main.py`

## Project Root
```
~/Documents/trading-system/
```

## File Structure
```
trading-system/
├── main.py                          # Scheduler entry point, runs all jobs
├── web/
│   ├── app.py                       # Flask routes and API endpoints
│   └── templates/                   # Jinja2 HTML templates
├── scrapers/
│   └── screener_scraper.py          # FinViz data scraping
├── database/
│   └── db.py                        # SQLite helper functions
├── config/
│   └── settings.py                  # Constants and configuration (NEWS_SCRAPE_TIMES etc.)
└── data/
    └── signalintel.db               # SQLite database
```

## Tech Stack
- **Backend:** Python, Flask
- **Database:** SQLite
- **Templating:** Jinja2
- **Data source:** FinViz (screener + individual quote pages via `finvizfinance` library)
- **Scheduler:** APScheduler (jobs wired in main.py)
- **Frontend:** Vanilla JS + HTML/CSS in Jinja2 templates

## Database Tables
| Table | Purpose |
|---|---|
| `screener_snapshots` | Raw FinViz screener data per ticker, timestamped |
| `signal_scores` | Composite signal scores per ticker per run |
| `insider_trades` | Insider buying/selling activity |
| `rating_changes` | Historical log of rating tier changes with price at change |
| `top_signals_of_day` | Daily top-ranked signals |
| `watchlists` | User watchlist tickers |
| `legal_risk` | SEC EDGAR legal risk scores per ticker |

## Signal Rating System (7 Tiers)
| Rating | Meaning |
|---|---|
| 🟢 Strong Buy | Highest conviction long |
| 🔵 Buy | Positive signal, enter or hold |
| 🟡 Strong Hold | Good fundamentals, no new entry |
| ⚪ Hold | Neutral, watch closely |
| 🟠 Weak Hold | Deteriorating, reduce exposure |
| 🔴 Sell | Exit position |
| ⛔ Strong Sell | High conviction short, get out |

## Composite Score Components
The composite score is built from these sub-scores:
- **MOMENTUM** — price momentum, RSI, SMA signals
- **QUALITY** — fundamentals (P/E, EPS, sector comparison)
- **INSIDER** — insider trade signals
- **REVERSION** — mean reversion signals
- **LEGAL** — SEC EDGAR risk penalty (6-tier classification)

Rating changes are detected and logged immediately after every signal generation run (not as a separate job).

## Scheduler Jobs (main.py)
- Signal scoring runs on schedule and triggers `detect_rating_changes` after every run
- News scraping is configurable via `NEWS_SCRAPE_TIMES` in `config/settings.py`
- 11 jobs registered total

## Key Patterns & Principles
- **Configuration over hardcoding** — times, thresholds, and settings belong in `config/settings.py`
- **Automated fix scripts preferred** over manual edits
- **Commit working code to git** between feature sets as checkpoints
- **FinViz individual ticker page scraping** (`finviz.com/quote.ashx?t=TICKER`) is used for high-priority tickers (watchlist + top signals) for data not available in bulk screener views (e.g. analyst recommendations)

## Planned Integrations (not yet built)
- **SendGrid** — email alerts
- **Stripe** — paywall and subscription management
- **Unusual Whales** — options flow data
- **SEC EDGAR** — legal risk scoring (scraper built, wiring in progress)

## Business Model (Roadmap)
- Free 7-day trial → Starter / Pro / Elite tiers
- Annual billing at 20% discount
- Monthly paper trading tournaments with prize pool (% of subscription revenue)
- Referral programme (1 month free per referral)
- React Native mobile app post-web launch
- Future: UK stocks, crypto, forex, options flow, white label B2B

## Current Development Phase
**Phase 1 (active):**
- [ ] Wire SEC EDGAR legal penalty into composite_score (LEGAL score in breakdown block)
- [ ] Virtual portfolio system with margin calls and bust mechanic
- [ ] Email alerts via SendGrid

**Phase 2 (next):**
- [ ] Stripe paywall
- [ ] Earnings calendar
- [ ] Short squeeze signals
- [ ] Options flow (Unusual Whales)

**Phase 3:**
- [ ] Macro overlay, sector heatmap
- [ ] Monthly tournaments + referral programme
- [ ] Public signal performance record

**Phase 4:**
- [ ] Full launch
- [ ] React Native mobile app
- [ ] Elite API tier
- [ ] White label B2B

## Virtual Portfolio System (design spec)
Margin call mechanic:
- 1x = no margin
- 2x = 50% margin requirement
- 5x = 20% margin requirement
- 10x = 10% margin requirement
- 20x = 5% margin requirement (elite tier only)
- Margin call triggers at 50% of margin requirement
- Warning at 75%
- If user can't cover → position auto-liquidates → if cash goes negative → **BUST**
- Busted portfolios stay visible on leaderboard (💀 marker)

## Key Differentiators
1. Verified public performance record — all signals logged with date + price, wins AND losses visible
2. Monthly paper trading tournaments with real prizes
3. Short squeeze detector — high short interest + STRONG_BUY confluence
4. Legal risk scoring via SEC EDGAR feeds into composite score as penalty

## Notes for Claude Code Sessions
- Always activate the venv before running Python scripts
- SQLite DB path is relative: `data/signalintel.db` from project root
- Flask runs on port 5001
- When editing scrapers, be mindful of FinViz rate limits — add delays between requests
- `rating_changes` table should be populated via `detect_rating_changes()` called after every signal run, not as a standalone job
- Check `config/settings.py` before hardcoding any values
- **Ratings Guide has been removed.** The "Ratings Guide" modal and button no longer exist in the nav. `/ratings` (Rating Tiers page) is the single reference for all rating and scoring information. Do not re-add a Ratings Guide button or modal.
- **Nav bar order** (defined in `web/templates/_nav.html`): Dashboard · Rating Tiers · Screener · Earnings · Dividends · Events · Markets · Watchlist · Backtest · Sign out
