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
            SELECT * FROM signal_scores
            WHERE scored_at = (SELECT MAX(scored_at) FROM signal_scores)
              AND rating = ?
            ORDER BY composite_score DESC
            LIMIT ?
        """, (rating, limit))
    else:
        cur.execute("""
            SELECT * FROM signal_scores
            WHERE scored_at = (SELECT MAX(scored_at) FROM signal_scores)
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
        SELECT rating, COUNT(*) as count,
               ROUND(AVG(composite_score),1) as avg_score
        FROM signal_scores
        WHERE scored_at = (SELECT MAX(scored_at) FROM signal_scores)
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
            WHERE scraped_at = (SELECT MAX(scraped_at) FROM screener_snapshots)
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
