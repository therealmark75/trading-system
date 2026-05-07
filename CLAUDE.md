# SignalIntel — Project Context for Claude Code

> **Before making changes, consult `docs/scoring_invariants.md`** for both data correctness rules (invariants 1–11) and development process rules (P1–P15). These rules apply to every change made in this project.
>
> **For any migration, refactor, or multi-surface change, apply P1.1 (inventory before edit), P1.2 (verify by absence), and P1.3 (audit table, not narrative). These are not optional.**
>
> **When writing tests, apply P15 — every test must articulate what it catches AND what it intentionally ignores. Both examples go in the test docstring.**

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

## Scoring Engine Versioning
- **`SCORING_ENGINE_VERSION`** lives in `config/settings.py`
- Every `signal_scores` row and every `rating_changes` row is stamped with the version that produced it
- The `/backtest` page filters all stats by version; a dropdown appears automatically when multiple versions exist in the data
- **Bump policy:**
  - `PATCH` (0.9.0 → 0.9.1): bug fixes that do NOT change scoring output
  - `MINOR` (0.9.0 → 0.10.0): new component added OR weight adjustment
  - `MAJOR` (0.9.x → 1.0.0): engine frozen for production launch
  - `MAJOR` (1.0.0 → 2.0.0): post-launch, breaking changes to scoring methodology
- **⚠ Bump the version BEFORE shipping any change that affects scoring output.** New data tagged with the old version is permanently mis-stamped and will pollute backtest comparisons.

### Before committing scoring changes
- [ ] Did this change affect signal scoring output? If yes, bump `SCORING_ENGINE_VERSION` in `config/settings.py` first.

## Signal Universe Constraints
- **`MIN_PRICE_FOR_SIGNAL = 1.00`** (defined in `config/settings.py`)
- Tickers below this price are excluded from new signal scoring. The filter lives in `signals/scorer.py` — tickers with `price < MIN_PRICE_FOR_SIGNAL` are skipped before any sub-score is computed.
- Existing watchlist entries that fall below threshold are **mark-and-hold**: visible on the watchlist with a greyed "BELOW $1" badge, no new signals generated.
- Rationale: sub-$1 percentage returns are mathematically distorting (penny-stock asymmetry). VEEE at $0.15 was producing +4,380% theoretical returns that are untradeable due to bid-ask spreads and liquidity.
- Threshold is provisional and may be raised. To change it: update `MIN_PRICE_FOR_SIGNAL` in `config/settings.py`, then re-run `scripts/purge_sub_threshold_rating_changes.py` to clean historical data, then re-run `scripts/rebuild_rating_changes.py` to regenerate transitions.

## Signal Terminology: Internal Codes vs Display Labels

Two separate vocabularies exist for the 7-tier signal system. **Never mix them.**

### Internal codes (storage layer)
```
STRONG_BUY  BUY  STRONG_HOLD  HOLD  WEAK_HOLD  SELL  STRONG_SELL
```
- Stored in the database: `signal_scores.rating`, `rating_changes.old_rating`, `rating_changes.new_rating`
- Used in scoring logic, SQL queries, theme/filter comparisons, test assertions
- Produced by `signals/scorer.py`

### Display labels (presentation layer)
```
Very Strong  Strong  Stable  Neutral  Soft  Bearish  Very Bearish
```
- User-facing only — appear in templates, Telegram alerts, and JSON responses to the frontend
- Translated from internal codes via `signals/signal_labels.py`:
  - `tier_label(rating)` → full label e.g. "Very Strong Signal"
  - `tier_short(rating)` → short label e.g. "Very Strong"

### The rule
- Internal codes never appear in user-visible output.
- Display labels never enter the database, queries, or scoring logic.
- When adding new code that touches signals, identify which layer you're in and use the appropriate vocabulary. If you're writing a query or scorer condition, use `STRONG_BUY`. If you're rendering a template or composing a message, call `tier_short()`.

## Notes for Claude Code Sessions
- Always activate the venv before running Python scripts
- SQLite DB path is relative: `data/signalintel.db` from project root
- Flask runs on port 5001
- When editing scrapers, be mindful of FinViz rate limits — add delays between requests
- `rating_changes` table should be populated via `detect_rating_changes()` called after every signal run, not as a standalone job
- Check `config/settings.py` before hardcoding any values
- **Ratings Guide has been removed.** The "Ratings Guide" modal and button no longer exist in the nav. `/ratings` (Rating Tiers page) is the single reference for all rating and scoring information. Do not re-add a Ratings Guide button or modal.
- **Nav bar order** (defined in `web/templates/_nav.html`): Dashboard · Rating Tiers · Screener · Earnings · Dividends · Events · Markets · Watchlist · Backtest · Sign out
