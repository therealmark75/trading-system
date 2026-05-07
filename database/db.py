# database/db.py
# ─────────────────────────────────────────────────
# SQLite database layer using SQLAlchemy core.
# Handles schema creation, inserts, and queries.
# ─────────────────────────────────────────────────

import sqlite3
import os
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def get_connection(db_path: str) -> sqlite3.Connection:
    """Return a sqlite3 connection, creating the DB file if needed."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # faster concurrent writes
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def initialise_schema(db_path: str) -> None:
    """Create all tables if they don't already exist."""
    conn = get_connection(db_path)
    cur  = conn.cursor()

    # ── Screener snapshots ────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS screener_snapshots (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            scraped_at          TEXT    NOT NULL,
            ticker              TEXT    NOT NULL,
            company             TEXT,
            sector              TEXT,
            industry            TEXT,
            country             TEXT,
            market_cap          TEXT,
            pe_ratio            REAL,
            price               REAL,
            change_pct          REAL,
            volume              INTEGER,
            eps_growth_this_yr  REAL,
            eps_growth_next_yr  REAL,
            sales_growth_5yr    REAL,
            roe                 REAL,
            insider_own_pct     REAL,
            insider_transactions TEXT,
            inst_own_pct        REAL,    -- institutional ownership %
            short_interest_pct  REAL,
            short_ratio         REAL,    -- days to cover
            analyst_recom       REAL,
            rsi_14              REAL,
            rel_volume          REAL,
            avg_volume          INTEGER,
            sma_50_pct          REAL,    -- price vs 50-day SMA (%)
            sma_200_pct         REAL,    -- price vs 200-day SMA (%)
            high_52w_pct        REAL,    -- % from 52-week high
            low_52w_pct         REAL,    -- % from 52-week low
            beta                REAL,
            forward_pe          REAL,
            peg_ratio           REAL,
            price_to_sales      REAL,
            price_to_book       REAL
        )
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_screener_ticker_date
        ON screener_snapshots (ticker, scraped_at)
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_screener_sector
        ON screener_snapshots (sector, scraped_at)
    """)

    # ── Insider trades ────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS insider_trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            scraped_at      TEXT    NOT NULL,
            ticker          TEXT    NOT NULL,
            company         TEXT,
            insider_name    TEXT,
            insider_title   TEXT,
            transaction_date TEXT,
            transaction_type TEXT,
            shares          INTEGER,
            price           REAL,
            value           REAL,
            shares_total    INTEGER,
            sec_form        TEXT,
            UNIQUE (ticker, insider_name, transaction_date, transaction_type, shares)
        )
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_insider_ticker
        ON insider_trades (ticker, transaction_date)
    """)

    # ── Insider cluster signals ───────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS insider_signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            detected_at     TEXT    NOT NULL,
            ticker          TEXT    NOT NULL,
            signal_type     TEXT    NOT NULL,   -- CLUSTER_BUY | CLUSTER_SELL
            insider_count   INTEGER,
            total_value     REAL,
            window_days     INTEGER,
            notes           TEXT
        )
    """)

    # ── News sentiment (Phase 3) ─────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS news_sentiment (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            scraped_at      TEXT    NOT NULL,
            ticker          TEXT    NOT NULL,
            headline        TEXT,
            source          TEXT,
            published       TEXT,
            sentiment       REAL,
            url             TEXT
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_news_ticker
        ON news_sentiment (ticker, scraped_at)
    """)

    # ── Ticker sentiment summary (Phase 3) ───────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ticker_sentiment (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            scored_at       TEXT    NOT NULL,
            ticker          TEXT    NOT NULL,
            avg_sentiment   REAL,
            bullish_count   INTEGER,
            bearish_count   INTEGER,
            neutral_count   INTEGER,
            article_count   INTEGER,
            UNIQUE (ticker, scored_at)
        )
    """)

    # ── Economic calendar (Phase 3) ───────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS economic_calendar (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            scraped_at       TEXT    NOT NULL,
            event_date       TEXT    NOT NULL,
            event_name       TEXT,
            impact           TEXT,
            affected_sectors TEXT,
            forecast         TEXT,
            previous         TEXT
        )
    """)

    # ── Signal scores (Phase 2) ───────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS signal_scores (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            scored_at               TEXT    NOT NULL,
            ticker                  TEXT    NOT NULL,
            composite_score         REAL,
            composite_score_raw     REAL,
            momentum_score          REAL,
            quality_score           REAL,
            insider_score           REAL,
            reversion_score         REAL,
            sector_strength_score   REAL,
            sector_modifier_applied REAL,
            rating                  TEXT,
            flags                   TEXT,
            target_price            REAL,
            target_upside           REAL,
            target_calculated_at    TEXT
        )
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_signal_ticker_date
        ON signal_scores (ticker, scored_at)
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_signal_rating
        ON signal_scores (rating, scored_at)
    """)

    # ── Run log (track each scrape job) ──────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS run_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at      TEXT    NOT NULL,
            job_name    TEXT    NOT NULL,
            status      TEXT    NOT NULL,   -- SUCCESS | PARTIAL | FAILED
            rows_added  INTEGER DEFAULT 0,
            error_msg   TEXT,
            duration_s  REAL
        )
    """)

    # ── Scheduler meta (watermarks, idempotency guards) ──────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS scheduler_meta (
            key    TEXT PRIMARY KEY,
            value  TEXT
        )
    """)

    conn.commit()
    conn.close()
    logger.info(f"Database schema ready at: {db_path}")


# ── Write helpers ─────────────────────────────────────────────────

def insert_screener_rows(db_path: str, rows: list[dict]) -> int:
    """Bulk insert screener snapshot rows. Returns count inserted."""
    if not rows:
        return 0

    conn = get_connection(db_path)
    cur  = conn.cursor()
    now  = datetime.utcnow().isoformat()

    insert_sql = """
        INSERT INTO screener_snapshots (
            scraped_at, ticker, company, sector, industry, country,
            market_cap, pe_ratio, price, change_pct, volume,
            eps_growth_this_yr, eps_growth_next_yr, sales_growth_5yr,
            roe, insider_own_pct, insider_transactions, inst_own_pct,
            short_interest_pct, short_ratio,
            analyst_recom, rsi_14, rel_volume, avg_volume,
            sma_50_pct, sma_200_pct, high_52w_pct, low_52w_pct, beta, exchange,
            forward_pe, peg_ratio, price_to_sales, price_to_book
        ) VALUES (
            :scraped_at, :ticker, :company, :sector, :industry, :country,
            :market_cap, :pe_ratio, :price, :change_pct, :volume,
            :eps_growth_this_yr, :eps_growth_next_yr, :sales_growth_5yr,
            :roe, :insider_own_pct, :insider_transactions, :inst_own_pct,
            :short_interest_pct, :short_ratio,
            :analyst_recom, :rsi_14, :rel_volume, :avg_volume,
            :sma_50_pct, :sma_200_pct, :high_52w_pct, :low_52w_pct, :beta,
            :exchange, :forward_pe, :peg_ratio, :price_to_sales, :price_to_book
        )
    """
    for row in rows:
        row["scraped_at"] = now
        row.setdefault("exchange", None)
        for col in ("inst_own_pct","short_ratio","forward_pe","peg_ratio","price_to_sales","price_to_book"):
            row.setdefault(col, None)
    cur.executemany(insert_sql, rows)
    conn.commit()
    inserted = cur.rowcount
    conn.close()
    return inserted


def insert_insider_trades(db_path: str, rows: list[dict]) -> int:
    """Insert insider trade rows, ignoring duplicates. Returns new count."""
    if not rows:
        return 0

    conn = get_connection(db_path)
    cur  = conn.cursor()
    now  = datetime.utcnow().isoformat()

    insert_sql = """
        INSERT OR IGNORE INTO insider_trades (
            scraped_at, ticker, company, insider_name, insider_title,
            transaction_date, transaction_type, shares, price, value,
            shares_total, sec_form
        ) VALUES (
            :scraped_at, :ticker, :company, :insider_name, :insider_title,
            :transaction_date, :transaction_type, :shares, :price, :value,
            :shares_total, :sec_form
        )
    """
    for row in rows:
        row["scraped_at"] = now
    cur.executemany(insert_sql, rows)
    conn.commit()
    inserted = cur.rowcount
    conn.close()
    return inserted


def insert_insider_signal(db_path: str, signal: dict) -> None:
    conn = get_connection(db_path)
    conn.execute("""
        INSERT INTO insider_signals
            (detected_at, ticker, signal_type, insider_count, total_value, window_days, notes)
        VALUES
            (:detected_at, :ticker, :signal_type, :insider_count, :total_value, :window_days, :notes)
    """, signal)
    conn.commit()
    conn.close()


def insert_signal_scores(db_path: str, rows: list[dict]) -> int:
    """Insert signal score rows. Returns count inserted."""
    if not rows:
        return 0
    conn = get_connection(db_path)
    cur  = conn.cursor()
    # Ensure new columns exist (idempotent; SQLite ignores duplicate ADD COLUMN)
    for col, typ in [
        ("composite_score_raw",     "REAL"),
        ("sector_strength_score",   "REAL"),
        ("sector_modifier_applied", "REAL"),
        ("scoring_version",         "TEXT NOT NULL DEFAULT '0.9.0'"),
    ]:
        try:
            cur.execute(f"ALTER TABLE signal_scores ADD COLUMN {col} {typ}")
        except Exception:
            pass
    conn.commit()
    # Delete today's existing scores before inserting fresh ones
    cur.execute(
        "DELETE FROM signal_scores WHERE DATE(scored_at) = DATE('now')"
    )
    cur.executemany("""
        INSERT INTO signal_scores
            (scored_at, ticker, composite_score, composite_score_raw,
             momentum_score, quality_score, insider_score, reversion_score,
             rating, flags, sector_strength_score, sector_modifier_applied,
             scoring_version)
        VALUES
            (:scored_at, :ticker, :composite_score, :composite_score_raw,
             :momentum_score, :quality_score, :insider_score, :reversion_score,
             :rating, :flags, :sector_strength_score, :sector_modifier_applied,
             :scoring_version)
    """, rows)
    conn.commit()
    inserted = cur.rowcount
    conn.close()
    return inserted


def get_top_signals(db_path: str, rating: str = None, limit: int = 50) -> list[dict]:
    """Return latest signal scores, optionally filtered by rating."""
    conn = get_connection(db_path)
    cur  = conn.cursor()
    if rating:
        cur.execute("""
            SELECT ticker, rating, MAX(composite_score) as composite_score,
                   momentum_score, quality_score, insider_score,
                   reversion_score, flags, MAX(scored_at) as scored_at
            FROM signal_scores
            WHERE DATE(scored_at) = DATE((SELECT MAX(scored_at) FROM signal_scores))
              AND rating = ?
            GROUP BY ticker
            ORDER BY composite_score DESC
            LIMIT ?
        """, (rating, limit))
    else:
        cur.execute("""
            SELECT ticker, rating, MAX(composite_score) as composite_score,
                   momentum_score, quality_score, insider_score,
                   reversion_score, flags, MAX(scored_at) as scored_at
            FROM signal_scores
            WHERE DATE(scored_at) = DATE((SELECT MAX(scored_at) FROM signal_scores))
            GROUP BY ticker
            ORDER BY composite_score DESC
            LIMIT ?
        """, (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_signal_summary(db_path: str) -> list[dict]:
    """Rating distribution from latest signal run."""
    conn = get_connection(db_path)
    cur  = conn.cursor()
    cur.execute("""
        SELECT rating, COUNT(DISTINCT ticker) as count,
               ROUND(AVG(composite_score),1) as avg_score
        FROM signal_scores
        WHERE DATE(scored_at) = DATE((SELECT MAX(scored_at) FROM signal_scores))
        GROUP BY rating
        ORDER BY avg_score DESC
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def log_run(db_path: str, job_name: str, status: str,
            rows_added: int = 0, error_msg: str = None, duration_s: float = 0.0) -> None:
    conn = get_connection(db_path)
    conn.execute("""
        INSERT INTO run_log (run_at, job_name, status, rows_added, error_msg, duration_s)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (datetime.utcnow().isoformat(), job_name, status, rows_added, error_msg, duration_s))
    conn.commit()
    conn.close()


# ── Query helpers ─────────────────────────────────────────────────

def get_latest_screener(db_path: str, sector: str = None) -> list[dict]:
    """Return the most recent screener snapshot, optionally filtered by sector."""
    conn = get_connection(db_path)
    cur  = conn.cursor()

    if sector:
        cur.execute("""
            SELECT * FROM screener_snapshots
            WHERE sector = ?
              AND scraped_at = (SELECT MAX(scraped_at) FROM screener_snapshots WHERE sector = ?)
            ORDER BY ticker
        """, (sector, sector))
    else:
        cur.execute("""
            SELECT * FROM screener_snapshots
            WHERE (ticker, scraped_at) IN (
                SELECT ticker, MAX(scraped_at)
                FROM screener_snapshots
                GROUP BY ticker
            )
            ORDER BY sector, ticker
        """)

    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_recent_insiders(db_path: str, days: int = 30, transaction_type: str = None) -> list[dict]:
    conn = get_connection(db_path)
    cur  = conn.cursor()

    if transaction_type:
        cur.execute("""
            SELECT * FROM insider_trades
            WHERE transaction_date >= date('now', ?)
              AND transaction_type = ?
            ORDER BY value DESC
        """, (f"-{days} days", transaction_type))
    else:
        cur.execute("""
            SELECT * FROM insider_trades
            WHERE transaction_date >= date('now', ?)
            ORDER BY value DESC
        """, (f"-{days} days",))

    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_cluster_signals(db_path: str, days: int = 14) -> list[dict]:
    conn = get_connection(db_path)
    cur  = conn.cursor()
    cur.execute("""
        SELECT * FROM insider_signals
        WHERE detected_at >= datetime('now', ?)
        ORDER BY total_value DESC
    """, (f"-{days} days",))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# ── Phase 3: News & Calendar helpers ─────────────────────────────

def insert_news_articles(db_path: str, articles: list[dict]) -> int:
    if not articles: return 0
    conn = get_connection(db_path)
    now  = datetime.now().isoformat()
    conn.executemany("""
        INSERT INTO news_sentiment
            (scraped_at, ticker, headline, source, published, sentiment, url)
        VALUES (:scraped_at,:ticker,:headline,:source,:published,:sentiment,:url)
    """, [{**a, "scraped_at": now} for a in articles])
    conn.commit()
    inserted = conn.total_changes
    conn.close()
    return inserted


def insert_ticker_sentiment(db_path: str, rows: list[dict]) -> int:
    if not rows: return 0
    conn = get_connection(db_path)
    now  = datetime.now().isoformat()
    conn.executemany("""
        INSERT OR REPLACE INTO ticker_sentiment
            (scored_at, ticker, avg_sentiment, bullish_count,
             bearish_count, neutral_count, article_count)
        VALUES (:scored_at,:ticker,:avg_sentiment,:bullish_count,
                :bearish_count,:neutral_count,:article_count)
    """, [{**r, "scored_at": now} for r in rows])
    conn.commit()
    conn.close()
    return len(rows)


def insert_calendar_events(db_path: str, events: list[dict]) -> int:
    if not events: return 0
    conn = get_connection(db_path)
    now  = datetime.now().isoformat()
    conn.executemany("""
        INSERT INTO economic_calendar
            (scraped_at, event_date, event_name, impact, affected_sectors, forecast, previous)
        VALUES (:scraped_at,:event_date,:event_name,:impact,:affected_sectors,:forecast,:previous)
    """, [{**e, "scraped_at": now,
           "affected_sectors": ",".join(e.get("affected_sectors",[]))} for e in events])
    conn.commit()
    conn.close()
    return len(events)


def get_ticker_sentiment(db_path: str, tickers: list[str] = None) -> list[dict]:
    conn = get_connection(db_path)
    cur  = conn.cursor()
    if tickers:
        placeholders = ",".join("?" * len(tickers))
        cur.execute(f"""
            SELECT * FROM ticker_sentiment
            WHERE scored_at = (SELECT MAX(scored_at) FROM ticker_sentiment)
              AND ticker IN ({placeholders})
            ORDER BY avg_sentiment DESC
        """, tickers)
    else:
        cur.execute("""
            SELECT * FROM ticker_sentiment
            WHERE scored_at = (SELECT MAX(scored_at) FROM ticker_sentiment)
            ORDER BY avg_sentiment DESC
        """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_upcoming_events(db_path: str, days: int = 7) -> list[dict]:
    conn = get_connection(db_path)
    cur  = conn.cursor()
    cur.execute("""
        SELECT * FROM economic_calendar
        WHERE event_date BETWEEN date('now') AND date('now', ?)
          AND scraped_at = (SELECT MAX(scraped_at) FROM economic_calendar)
        ORDER BY event_date, impact DESC
    """, (f"+{days} days",))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# ── Phase 5: User auth & watchlists ──────────────────────────────

def initialise_user_schema(db_path: str) -> None:
    conn = get_connection(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT    NOT NULL UNIQUE,
            email         TEXT    NOT NULL UNIQUE,
            password_hash TEXT    NOT NULL,
            created_at    TEXT    NOT NULL,
            is_active     INTEGER DEFAULT 1,
            tier          TEXT    DEFAULT 'free'
        );

        CREATE TABLE IF NOT EXISTS watchlists_meta (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            name       TEXT    NOT NULL,
            sort_order INTEGER DEFAULT 0,
            created_at TEXT    DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            UNIQUE(user_id, name)
        );

        CREATE TABLE IF NOT EXISTS watchlists (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL REFERENCES users(id),
            watchlist_id INTEGER NOT NULL REFERENCES watchlists_meta(id),
            ticker       TEXT    NOT NULL,
            added_at     TEXT    NOT NULL,
            notes        TEXT,
            UNIQUE(user_id, watchlist_id, ticker)
        );

        CREATE TABLE IF NOT EXISTS top_signals_of_day (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at   TEXT NOT NULL,
            signal_date    TEXT NOT NULL,
            ticker         TEXT NOT NULL,
            signal_type    TEXT NOT NULL,
            composite_score REAL,
            rating         TEXT,
            reason         TEXT,
            rank           INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_watchlist_user
        ON watchlists (user_id);

        CREATE INDEX IF NOT EXISTS idx_watchlist_meta_user
        ON watchlists_meta (user_id);

        CREATE INDEX IF NOT EXISTS idx_top_signals_date
        ON top_signals_of_day (signal_date);
    """)
    conn.commit()

    # Migration: add tier column to users if missing
    cur = conn.cursor()
    user_cols = {r[1] for r in cur.execute("PRAGMA table_info(users)").fetchall()}
    if 'tier' not in user_cols:
        cur.execute("ALTER TABLE users ADD COLUMN tier TEXT DEFAULT 'free'")
        conn.commit()

    # Migration: upgrade old single-watchlist schema if watchlist_id column is missing
    wl_cols = {r[1] for r in cur.execute("PRAGMA table_info(watchlists)").fetchall()}
    if 'watchlist_id' not in wl_cols:
        _migrate_watchlists_to_multi(conn)

    # Migration: add alerts_enabled column to watchlists_meta if missing
    wm_cols = {r[1] for r in cur.execute("PRAGMA table_info(watchlists_meta)").fetchall()}
    if 'alerts_enabled' not in wm_cols:
        cur.execute("ALTER TABLE watchlists_meta ADD COLUMN alerts_enabled INTEGER NOT NULL DEFAULT 1")
        conn.commit()

    # Migration: add is_default column to watchlists_meta if missing
    wm_cols = {r[1] for r in cur.execute("PRAGMA table_info(watchlists_meta)").fetchall()}
    if 'is_default' not in wm_cols:
        cur.execute("ALTER TABLE watchlists_meta ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0")
        conn.commit()

    # Partial UNIQUE index: at most one is_default=1 per user.
    # SQLite enforces this on INSERT/UPDATE; rows with is_default=0 are unrestricted.
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_watchlists_meta_one_default_per_user
        ON watchlists_meta(user_id) WHERE is_default = 1
    """)
    conn.commit()

    conn.close()


def _migrate_watchlists_to_multi(conn: sqlite3.Connection) -> None:
    """Migrate watchlists from UNIQUE(user_id,ticker) to multi-watchlist schema."""
    cur = conn.cursor()
    user_ids = [r[0] for r in cur.execute(
        "SELECT DISTINCT user_id FROM watchlists"
    ).fetchall()]
    wl_map = {}
    for uid in user_ids:
        cur.execute(
            "INSERT OR IGNORE INTO watchlists_meta (user_id, name, sort_order) "
            "VALUES (?, 'My Watchlist', 0)",
            (uid,)
        )
        row = cur.execute(
            "SELECT id FROM watchlists_meta WHERE user_id=? AND name='My Watchlist'",
            (uid,)
        ).fetchone()
        wl_map[uid] = row[0]
    cur.execute("""
        CREATE TABLE watchlists_new (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL REFERENCES users(id),
            watchlist_id INTEGER NOT NULL REFERENCES watchlists_meta(id),
            ticker       TEXT    NOT NULL,
            added_at     TEXT    NOT NULL,
            notes        TEXT,
            UNIQUE(user_id, watchlist_id, ticker)
        )
    """)
    for uid, wid in wl_map.items():
        cur.execute("""
            INSERT INTO watchlists_new (user_id, watchlist_id, ticker, added_at, notes)
            SELECT user_id, ?, ticker, added_at, notes FROM watchlists WHERE user_id = ?
        """, (wid, uid))
    cur.execute("DROP TABLE watchlists")
    cur.execute("ALTER TABLE watchlists_new RENAME TO watchlists")
    conn.commit()


def create_user(db_path: str, username: str, email: str, password_hash: str) -> int:
    conn = get_connection(db_path)
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO users (username, email, password_hash, created_at)
        VALUES (?, ?, ?, ?)
    """, (username, email, password_hash, datetime.now().isoformat()))
    conn.commit()
    user_id = cur.lastrowid
    conn.close()
    return user_id


def get_user_by_email(db_path: str, email: str):
    conn = get_connection(db_path)
    cur  = conn.cursor()
    cur.execute("SELECT * FROM users WHERE email = ? AND is_active = 1", (email,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_username(db_path: str, username: str):
    conn = get_connection(db_path)
    cur  = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = ? AND is_active = 1", (username,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(db_path: str, user_id: int):
    conn = get_connection(db_path)
    cur  = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_watchlists_meta(db_path: str, user_id: int) -> list[dict]:
    """Return all watchlists for a user, with ticker counts."""
    conn = get_connection(db_path)
    rows = conn.execute("""
        SELECT wm.id, wm.name, wm.sort_order, wm.created_at,
               wm.alerts_enabled, wm.is_default,
               COUNT(w.ticker) AS ticker_count
        FROM watchlists_meta wm
        LEFT JOIN watchlists w ON w.watchlist_id = wm.id
        WHERE wm.user_id = ?
        GROUP BY wm.id
        ORDER BY wm.sort_order, wm.id
    """, (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_or_create_default_watchlist(db_path: str, user_id: int) -> int:
    """Return the id of the user's first watchlist, creating 'My Watchlist' if none exists.
    A newly created watchlist via this helper is flagged is_default=1.
    """
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT id FROM watchlists_meta WHERE user_id = ? ORDER BY sort_order, id LIMIT 1",
        (user_id,)
    ).fetchone()
    if row:
        conn.close()
        return row[0]
    cur = conn.execute(
        "INSERT INTO watchlists_meta (user_id, name, sort_order, alerts_enabled, is_default) "
        "VALUES (?, 'My Watchlist', 0, 1, 1)",
        (user_id,)
    )
    conn.commit()
    wid = cur.lastrowid
    conn.close()
    return wid


def create_default_watchlist(db_path: str, user_id: int) -> int:
    """Create the user's default watchlist ('My Watchlist', alerts_enabled=1, is_default=1).
    Intended for use immediately after create_user() during signup, or by the
    backfill script for users with zero watchlists.

    Raises sqlite3.IntegrityError if the user already has a watchlist with
    is_default=1 (the partial UNIQUE index enforces at-most-one-default-per-user).
    """
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO watchlists_meta (user_id, name, sort_order, alerts_enabled, is_default) "
            "VALUES (?, 'My Watchlist', 0, 1, 1)",
            (user_id,)
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def is_default_watchlist(db_path: str, user_id: int, wl_id: int) -> bool:
    """Return True if the given watchlist (owned by user_id) is flagged is_default=1."""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT is_default FROM watchlists_meta WHERE id=? AND user_id=?",
            (wl_id, user_id)
        ).fetchone()
        return bool(row and row[0])
    finally:
        conn.close()


def create_watchlist(db_path: str, user_id: int, name: str) -> dict:
    """Create a new named watchlist. Returns {'id': int, 'name': str} or raises ValueError."""
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO watchlists_meta (user_id, name, sort_order) VALUES (?, ?, "
            "(SELECT COALESCE(MAX(sort_order),0)+1 FROM watchlists_meta WHERE user_id=?))",
            (user_id, name.strip(), user_id)
        )
        conn.commit()
        return {"id": cur.lastrowid, "name": name.strip()}
    except Exception as e:
        if "UNIQUE" in str(e):
            raise ValueError(f"A watchlist named '{name}' already exists")
        raise
    finally:
        conn.close()


def rename_watchlist(db_path: str, user_id: int, watchlist_id: int, new_name: str) -> bool:
    """Rename a watchlist. Returns False if watchlist not owned by user."""
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "UPDATE watchlists_meta SET name=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND user_id=?",
            (new_name.strip(), watchlist_id, user_id)
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        if "UNIQUE" in str(e):
            raise ValueError(f"A watchlist named '{new_name}' already exists")
        raise
    finally:
        conn.close()


def delete_watchlist(db_path: str, user_id: int, watchlist_id: int) -> bool:
    """Delete a watchlist and all its tickers. Returns False if not owned by user."""
    conn = get_connection(db_path)
    # Verify ownership
    row = conn.execute(
        "SELECT id FROM watchlists_meta WHERE id=? AND user_id=?",
        (watchlist_id, user_id)
    ).fetchone()
    if not row:
        conn.close()
        return False
    conn.execute("DELETE FROM watchlists WHERE watchlist_id=?", (watchlist_id,))
    conn.execute("DELETE FROM watchlists_meta WHERE id=?", (watchlist_id,))
    conn.commit()
    conn.close()
    return True


def get_watchlist(db_path: str, user_id: int, watchlist_id: int = None) -> list[dict]:
    if watchlist_id is None:
        watchlist_id = get_or_create_default_watchlist(db_path, user_id)
    conn = get_connection(db_path)
    cur  = conn.cursor()
    cur.execute("""
        SELECT w.ticker, w.added_at, w.notes,
               ss.composite_score, ss.rating, ss.momentum_score,
               ss.quality_score, ss.insider_score, ss.flags,
               ss.target_price, ss.target_upside, ss.sector_strength_score,
               sn.price, sn.change_pct, sn.rsi_14
        FROM watchlists w
        LEFT JOIN (
            SELECT ticker, MAX(scored_at) as max_ts
            FROM signal_scores GROUP BY ticker
        ) latest ON latest.ticker = w.ticker
        LEFT JOIN signal_scores ss ON ss.ticker = w.ticker AND ss.scored_at = latest.max_ts
        LEFT JOIN (
            SELECT ticker, MAX(scraped_at) as max_ts
            FROM screener_snapshots GROUP BY ticker
        ) lsnap ON lsnap.ticker = w.ticker
        LEFT JOIN screener_snapshots sn ON sn.ticker = w.ticker AND sn.scraped_at = lsnap.max_ts
        WHERE w.user_id = ? AND w.watchlist_id = ?
        ORDER BY ss.composite_score DESC NULLS LAST
    """, (user_id, watchlist_id))
    rows = [dict(r) for r in cur.fetchall()]

    # Compute pct_since_add: price closest to added_at vs current price
    for row in rows:
        pct = None
        current_price = row.get("price")
        added_at = row.get("added_at")
        if current_price and added_at:
            snap = cur.execute("""
                SELECT price FROM screener_snapshots
                WHERE ticker = ?
                ORDER BY ABS(julianday(scraped_at) - julianday(?))
                LIMIT 1
            """, (row["ticker"], added_at)).fetchone()
            if snap and snap[0] and snap[0] > 0:
                pct = round((current_price - snap[0]) / snap[0] * 100.0, 2)
        row["pct_since_add"] = pct

    conn.close()
    return rows


def add_to_watchlist(db_path: str, user_id: int, ticker: str, notes: str = "",
                     watchlist_id: int = None) -> bool:
    if watchlist_id is None:
        watchlist_id = get_or_create_default_watchlist(db_path, user_id)
    conn = get_connection(db_path)
    try:
        conn.execute("""
            INSERT OR IGNORE INTO watchlists (user_id, watchlist_id, ticker, added_at, notes)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, watchlist_id, ticker.upper(), datetime.now().isoformat(), notes))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def remove_from_watchlist(db_path: str, user_id: int, ticker: str,
                          watchlist_id: int = None) -> bool:
    if watchlist_id is None:
        watchlist_id = get_or_create_default_watchlist(db_path, user_id)
    conn = get_connection(db_path)
    conn.execute(
        "DELETE FROM watchlists WHERE user_id = ? AND watchlist_id = ? AND ticker = ?",
        (user_id, watchlist_id, ticker.upper())
    )
    conn.commit()
    conn.close()
    return True


def get_top_signals_of_day(db_path: str, date: str = None) -> list[dict]:
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    conn = get_connection(db_path)
    cur  = conn.cursor()
    cur.execute("""
        SELECT * FROM top_signals_of_day
        WHERE signal_date = ?
        ORDER BY rank
    """, (date,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def generate_top_signals_of_day(db_path: str) -> list[dict]:
    """Auto-generate today's top 10 signals: 5 buy + 5 short."""
    conn  = get_connection(db_path)
    cur   = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")

    # Top 5 BUY signals (highest composite score with good insider + momentum)
    cur.execute("""
        SELECT ss.ticker, ss.composite_score, ss.rating,
               ss.momentum_score, ss.insider_score, ss.flags,
               sn.change_pct, sn.rsi_14, sn.short_interest_pct
        FROM signal_scores ss
        LEFT JOIN (
            SELECT ticker, MAX(scraped_at) as max_ts FROM screener_snapshots GROUP BY ticker
        ) lsnap ON lsnap.ticker = ss.ticker
        LEFT JOIN screener_snapshots sn ON sn.ticker = ss.ticker AND sn.scraped_at = lsnap.max_ts
        WHERE DATE(ss.scored_at) = DATE((SELECT MAX(scored_at) FROM signal_scores))
          AND ss.rating IN ('BUY','STRONG_BUY')
          AND ss.composite_score >= 62
        GROUP BY ss.ticker
        ORDER BY ss.composite_score DESC
        LIMIT 5
    """)
    buy_signals = [dict(r) for r in cur.fetchall()]

    # Top 5 SHORT signals (cluster sell + overbought + high short interest)
    cur.execute("""
        SELECT ss.ticker, ss.composite_score, ss.rating,
               ss.momentum_score, ss.insider_score, ss.flags,
               sn.change_pct, sn.rsi_14, sn.short_interest_pct
        FROM signal_scores ss
        LEFT JOIN (
            SELECT ticker, MAX(scraped_at) as max_ts FROM screener_snapshots GROUP BY ticker
        ) lsnap ON lsnap.ticker = ss.ticker
        LEFT JOIN screener_snapshots sn ON sn.ticker = ss.ticker AND sn.scraped_at = lsnap.max_ts
        WHERE DATE(ss.scored_at) = DATE((SELECT MAX(scored_at) FROM signal_scores))
          AND ss.rating IN ('STRONG_SELL','SELL','WEAK_HOLD')
        GROUP BY ss.ticker
        ORDER BY CASE ss.rating WHEN 'STRONG_SELL' THEN 1 WHEN 'SELL' THEN 2 WHEN 'WEAK_HOLD' THEN 3 ELSE 4 END ASC, ss.composite_score ASC
        LIMIT 5
    """)
    short_signals = [dict(r) for r in cur.fetchall()]

    # Delete today's existing top signals
    conn.execute("DELETE FROM top_signals_of_day WHERE signal_date = ?", (today,))

    results = []
    now = datetime.now().isoformat()

    for i, s in enumerate(buy_signals):
        flags = (s.get("flags") or "").split("|")
        reasons = []
        if s.get("momentum_score", 0) >= 90: reasons.append("Strong momentum")
        if s.get("insider_score", 0) >= 70:  reasons.append("Insider buying")
        if s.get("rsi_14") and s["rsi_14"] < 50: reasons.append("Room to run")

        row = {
            "generated_at":   now,
            "signal_date":    today,
            "ticker":         s["ticker"],
            "signal_type":    "BUY",
            "composite_score":s["composite_score"],
            "rating":         s["rating"],
            "reason":         " · ".join(reasons) if reasons else "Multi-factor BUY signal",
            "rank":           i + 1,
        }
        conn.execute("""
            INSERT INTO top_signals_of_day
                (generated_at, signal_date, ticker, signal_type,
                 composite_score, rating, reason, rank)
            VALUES (:generated_at,:signal_date,:ticker,:signal_type,
                    :composite_score,:rating,:reason,:rank)
        """, row)
        results.append(row)

    for i, s in enumerate(short_signals):
        reasons = []
        if s.get("rsi_14") and s["rsi_14"] > 65: reasons.append("Overbought RSI")
        if s.get("short_interest_pct") and s["short_interest_pct"] > 15: reasons.append("High short interest")
        if s.get("insider_score", 50) < 35: reasons.append("Insider selling")

        row = {
            "generated_at":   now,
            "signal_date":    today,
            "ticker":         s["ticker"],
            "signal_type":    "SHORT",
            "composite_score":s["composite_score"],
            "rating":         s["rating"],
            "reason":         " · ".join(reasons) if reasons else "Multi-factor SHORT signal",
            "rank":           i + 6,
        }
        conn.execute("""
            INSERT INTO top_signals_of_day
                (generated_at, signal_date, ticker, signal_type,
                 composite_score, rating, reason, rank)
            VALUES (:generated_at,:signal_date,:ticker,:signal_type,
                    :composite_score,:rating,:reason,:rank)
        """, row)
        results.append(row)

    conn.commit()
    conn.close()
    return results

def prune_old_snapshots(db_path: str, days: int = 90) -> int:
    """Delete screener snapshots older than `days` days. Returns rows deleted."""
    conn = get_connection(db_path)
    cur  = conn.cursor()
    cur.execute(
        "DELETE FROM screener_snapshots WHERE scraped_at < datetime('now', ? || ' days')",
        (f'-{days}',)
    )
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    if deleted:
        logger.info(f"Pruned {deleted} screener snapshots older than {days} days")
    return deleted



def update_analyst_recom(db_path: str, recom_map: dict) -> int:
    """
    Update analyst_recom in screener_snapshots for a dict of {ticker: recom}.
    Updates only the most recent snapshot row per ticker.
    Returns number of rows updated.
    """
    if not recom_map:
        return 0
    conn = get_connection(db_path)
    cur = conn.cursor()
    updated = 0
    for ticker, recom in recom_map.items():
        cur.execute("""
            UPDATE screener_snapshots
            SET analyst_recom = ?
            WHERE ticker = ?
            AND scraped_at = (
                SELECT MAX(scraped_at) FROM screener_snapshots WHERE ticker = ?
            )
        """, (recom, ticker, ticker))
        updated += cur.rowcount
    conn.commit()
    conn.close()
    return updated

def detect_rating_changes(db_path: str) -> list:
    """After each signal run, check for new rating changes and log them.
    Returns list of change dicts: ticker, old_rating, new_rating, price, composite_score.

    Idempotency: a watermark in scheduler_meta tracks the last signal_scores batch
    (by MAX scored_at) that was processed. Calling this function multiple times with
    the same signal_scores data returns [] on every call after the first.
    """
    conn = get_connection(db_path)
    cur = conn.cursor()
    changes = []
    try:
        # Ensure scoring_version column exists on rating_changes
        try:
            cur.execute(
                "ALTER TABLE rating_changes ADD COLUMN "
                "scoring_version TEXT NOT NULL DEFAULT '0.9.0'"
            )
            conn.commit()
        except Exception:
            pass

        # Watermark guard: skip if this signal_scores batch was already processed
        cur.execute("SELECT MAX(scored_at) as max_ts FROM signal_scores")
        row = cur.fetchone()
        current_max_ts = row['max_ts'] if row else None
        if not current_max_ts:
            logger.info("detect_rating_changes: no signal_scores rows, skipping")
            return []

        cur.execute(
            "SELECT value FROM scheduler_meta WHERE key = 'rating_changes_watermark'"
        )
        wm_row = cur.fetchone()
        watermark = wm_row['value'] if wm_row else None

        if watermark == current_max_ts:
            logger.info(
                "detect_rating_changes: no new signal_scores batch since %s, skipping",
                watermark,
            )
            return []

        # Get the latest signal for each ticker (one row per ticker after dedup)
        cur.execute("""
            SELECT ss.ticker, ss.rating, ss.composite_score, DATE(ss.scored_at) as day,
                   COALESCE(ss.scoring_version, '0.9.0') as scoring_version
            FROM signal_scores ss
            WHERE ss.scored_at = (
                SELECT MAX(s2.scored_at) FROM signal_scores s2 WHERE s2.ticker = ss.ticker
            )
        """)
        today_signals = cur.fetchall()

        for sig in today_signals:
            ticker = sig['ticker']
            new_rating = sig['rating']
            day = sig['day']
            scoring_version = sig['scoring_version']

            # Get last recorded rating for this ticker
            cur.execute("""
                SELECT new_rating FROM rating_changes
                WHERE ticker = ?
                ORDER BY change_date DESC LIMIT 1
            """, (ticker,))
            last = cur.fetchone()
            old_rating = last['new_rating'] if last else None

            if old_rating != new_rating:
                # Get current price
                cur.execute("""
                    SELECT price FROM screener_snapshots
                    WHERE ticker = ? ORDER BY scraped_at DESC LIMIT 1
                """, (ticker,))
                price_row = cur.fetchone()
                price = price_row['price'] if price_row else None

                cur.execute("""
                    INSERT INTO rating_changes
                    (ticker, old_rating, new_rating, price_at_change, change_date,
                     composite_score, scoring_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (ticker, old_rating, new_rating, price, day,
                      sig['composite_score'], scoring_version))

                changes.append({
                    "ticker": ticker,
                    "old_rating": old_rating,
                    "new_rating": new_rating,
                    "price": price,
                    "composite_score": sig['composite_score'],
                    "scoring_version": scoring_version,
                })

        # Advance watermark so re-runs of the same batch are no-ops
        cur.execute("""
            INSERT INTO scheduler_meta (key, value) VALUES ('rating_changes_watermark', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (current_max_ts,))

        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
    return changes


def update_target_prices(db_path: str, rows: list[dict]) -> int:
    """
    Update target_price, target_upside, and target_calculated_at on today's
    signal_scores rows.
    rows: list of {ticker, target_price, target_upside}
    """
    if not rows:
        return 0
    conn = get_connection(db_path)
    cur  = conn.cursor()
    # Ensure column exists (idempotent migration)
    try:
        cur.execute("ALTER TABLE signal_scores ADD COLUMN target_calculated_at TEXT")
        conn.commit()
    except Exception:
        pass
    now = datetime.utcnow().isoformat()
    updated = 0
    for r in rows:
        if r.get("target_price") is None:
            continue
        cur.execute("""
            UPDATE signal_scores
            SET target_price = ?, target_upside = ?, target_calculated_at = ?
            WHERE ticker = ?
              AND DATE(scored_at) = DATE((SELECT MAX(scored_at) FROM signal_scores))
        """, (r["target_price"], r.get("target_upside"), now, r["ticker"]))
        updated += cur.rowcount
    conn.commit()
    conn.close()
    return updated


def get_price_history_map(db_path: str, days: int = 180) -> dict:
    """
    Return {ticker: [(days_ago, price), ...]} from screener_snapshots.
    Used for 12-month target price linear regression (technical component).
    One price point per ticker per calendar day (most recent intraday snapshot).
    """
    conn = get_connection(db_path)
    cur  = conn.cursor()
    cur.execute(f"""
        SELECT ticker,
               CAST(julianday('now') - julianday(MAX(scraped_at)) AS INTEGER) AS days_ago,
               price
        FROM screener_snapshots
        WHERE scraped_at >= datetime('now', '-{days} days')
          AND price > 0
        GROUP BY ticker, DATE(scraped_at)
        ORDER BY ticker, scraped_at ASC
    """)
    rows = cur.fetchall()
    conn.close()
    result = {}
    for r in rows:
        ticker = r["ticker"]
        if ticker not in result:
            result[ticker] = []
        result[ticker].append((r["days_ago"], r["price"]))
    return result


def get_legal_risk_map(db_path: str) -> dict:
    """Return {ticker: {penalty, risk_level, risk_label, risk_color}} for all rows in legal_risk."""
    conn = get_connection(db_path)
    cur  = conn.cursor()
    cur.execute("SELECT ticker, penalty, risk_level, risk_label, risk_color FROM legal_risk")
    rows = cur.fetchall()
    conn.close()
    return {r["ticker"]: {
        "penalty":    r["penalty"],
        "risk_level": r["risk_level"],
        "risk_label": r["risk_label"],
        "risk_color": r["risk_color"],
    } for r in rows}


def get_watchlist_tickers(db_path: str, alerts_only: bool = False) -> set:
    """Return set of all distinct tickers present in any watchlist (any user).

    When alerts_only=True, only tickers from watchlists where alerts_enabled=1
    are returned. OR semantics: a ticker qualifies if ANY of its containing
    watchlists has alerts on.
    """
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        if alerts_only:
            cur.execute("""
                SELECT DISTINCT w.ticker
                FROM watchlists w
                JOIN watchlists_meta wm ON wm.id = w.watchlist_id
                WHERE wm.alerts_enabled = 1
            """)
        else:
            cur.execute("SELECT DISTINCT ticker FROM watchlists")
        return {row[0] for row in cur.fetchall()}
    finally:
        conn.close()


def toggle_watchlist_alerts(db_path: str, user_id: int, wl_id: int):
    """Flip alerts_enabled for the given watchlist. Returns updated row or None if not found."""
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, alerts_enabled FROM watchlists_meta WHERE id = ? AND user_id = ?",
            (wl_id, user_id)
        )
        row = cur.fetchone()
        if not row:
            return None
        new_val = 0 if row["alerts_enabled"] else 1
        cur.execute(
            "UPDATE watchlists_meta SET alerts_enabled = ? WHERE id = ?",
            (new_val, wl_id)
        )
        conn.commit()
        return {"watchlist_id": wl_id, "alerts_enabled": bool(new_val)}
    finally:
        conn.close()


def is_below_signal_threshold(current_price) -> bool:
    """Return True if current_price is below MIN_PRICE_FOR_SIGNAL.
    Used by templates and routes to flag mark-and-hold watchlist entries.
    """
    from config.settings import MIN_PRICE_FOR_SIGNAL
    if current_price is None:
        return False
    return float(current_price) < MIN_PRICE_FOR_SIGNAL
