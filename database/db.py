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
            short_interest_pct  REAL,
            analyst_recom       REAL,
            rsi_14              REAL,
            rel_volume          REAL,
            avg_volume          INTEGER,
            sma_50_pct          REAL,    -- price vs 50-day SMA (%)
            sma_200_pct         REAL,    -- price vs 200-day SMA (%)
            high_52w_pct        REAL,    -- % from 52-week high
            low_52w_pct         REAL,    -- % from 52-week low
            beta                REAL
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
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            scored_at        TEXT    NOT NULL,
            ticker           TEXT    NOT NULL,
            composite_score  REAL,
            momentum_score   REAL,
            quality_score    REAL,
            insider_score    REAL,
            reversion_score  REAL,
            rating           TEXT,
            flags            TEXT
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
            roe, insider_own_pct, insider_transactions, short_interest_pct,
            analyst_recom, rsi_14, rel_volume, avg_volume,
            sma_50_pct, sma_200_pct, high_52w_pct, low_52w_pct, beta
        ) VALUES (
            :scraped_at, :ticker, :company, :sector, :industry, :country,
            :market_cap, :pe_ratio, :price, :change_pct, :volume,
            :eps_growth_this_yr, :eps_growth_next_yr, :sales_growth_5yr,
            :roe, :insider_own_pct, :insider_transactions, :short_interest_pct,
            :analyst_recom, :rsi_14, :rel_volume, :avg_volume,
            :sma_50_pct, :sma_200_pct, :high_52w_pct, :low_52w_pct, :beta
        )
    """
    for row in rows:
        row["scraped_at"] = now
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
    cur.executemany("""
        INSERT INTO signal_scores
            (scored_at, ticker, composite_score, momentum_score, quality_score,
             insider_score, reversion_score, rating, flags)
        VALUES
            (:scored_at, :ticker, :composite_score, :momentum_score, :quality_score,
             :insider_score, :reversion_score, :rating, :flags)
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
            is_active     INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS watchlists (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            ticker     TEXT    NOT NULL,
            added_at   TEXT    NOT NULL,
            notes      TEXT,
            UNIQUE(user_id, ticker)
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

        CREATE INDEX IF NOT EXISTS idx_top_signals_date
        ON top_signals_of_day (signal_date);
    """)
    conn.commit()
    conn.close()


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


def get_watchlist(db_path: str, user_id: int) -> list[dict]:
    conn = get_connection(db_path)
    cur  = conn.cursor()
    cur.execute("""
        SELECT w.ticker, w.added_at, w.notes,
               ss.composite_score, ss.rating, ss.momentum_score,
               ss.quality_score, ss.insider_score, ss.flags,
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
        WHERE w.user_id = ?
        ORDER BY ss.composite_score DESC NULLS LAST
    """, (user_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def add_to_watchlist(db_path: str, user_id: int, ticker: str, notes: str = "") -> bool:
    conn = get_connection(db_path)
    try:
        conn.execute("""
            INSERT OR IGNORE INTO watchlists (user_id, ticker, added_at, notes)
            VALUES (?, ?, ?, ?)
        """, (user_id, ticker.upper(), datetime.now().isoformat(), notes))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def remove_from_watchlist(db_path: str, user_id: int, ticker: str) -> bool:
    conn = get_connection(db_path)
    conn.execute("DELETE FROM watchlists WHERE user_id = ? AND ticker = ?",
                 (user_id, ticker.upper()))
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
