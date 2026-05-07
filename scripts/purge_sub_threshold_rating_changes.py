"""
scripts/purge_sub_threshold_rating_changes.py
──────────────────────────────────────────────
Remove historical rows for tickers that were below MIN_PRICE_FOR_SIGNAL
at the time of the record.

Tables cleaned:
  rating_changes    — rows where price_at_change < MIN_PRICE_FOR_SIGNAL
  signal_scores     — all rows for tickers whose latest price < threshold
  top_signals_of_day — all rows for tickers whose latest price < threshold

Idempotent: running twice leaves the same final state.
Wraps all deletes in a single transaction; rolls back on any error.

Usage:
  python scripts/purge_sub_threshold_rating_changes.py
  python scripts/purge_sub_threshold_rating_changes.py --db path/to/trading_system.db
"""

import sys
import sqlite3
import argparse
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.constants import MIN_PRICE_FOR_SIGNAL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("purge_sub_threshold")


def purge(db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    stats = {}

    try:
        # ── Pre-purge counts ─────────────────────────────────────────
        cur.execute("SELECT COUNT(*) AS n FROM rating_changes")
        stats["rc_before"] = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM signal_scores")
        stats["ss_before"] = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM top_signals_of_day")
        stats["tsd_before"] = cur.fetchone()["n"]

        # ── Build sub-threshold ticker set (latest price per ticker) ──
        cur.execute("""
            SELECT ticker, price
            FROM screener_snapshots
            WHERE (ticker, scraped_at) IN (
                SELECT ticker, MAX(scraped_at)
                FROM screener_snapshots
                GROUP BY ticker
            )
              AND price IS NOT NULL
        """)
        price_map = {r["ticker"]: r["price"] for r in cur.fetchall()}
        sub_threshold_tickers = {
            t for t, p in price_map.items() if p < MIN_PRICE_FOR_SIGNAL
        }
        logger.info(
            "%d tickers currently below $%.2f threshold",
            len(sub_threshold_tickers),
            MIN_PRICE_FOR_SIGNAL,
        )

        # ── rating_changes: price_at_change is known and below threshold ─
        cur.execute("""
            DELETE FROM rating_changes
            WHERE price_at_change IS NOT NULL
              AND price_at_change < ?
        """, (MIN_PRICE_FOR_SIGNAL,))
        stats["rc_deleted"] = cur.rowcount
        logger.info("rating_changes: deleted %d sub-threshold rows", stats["rc_deleted"])

        # ── signal_scores + top_signals_of_day: by ticker set ────────
        if sub_threshold_tickers:
            placeholders = ",".join("?" * len(sub_threshold_tickers))
            ticker_list = list(sub_threshold_tickers)

            cur.execute(
                f"DELETE FROM signal_scores WHERE ticker IN ({placeholders})",
                ticker_list,
            )
            stats["ss_deleted"] = cur.rowcount

            cur.execute(
                f"DELETE FROM top_signals_of_day WHERE ticker IN ({placeholders})",
                ticker_list,
            )
            stats["tsd_deleted"] = cur.rowcount
        else:
            stats["ss_deleted"] = 0
            stats["tsd_deleted"] = 0

        logger.info("signal_scores: deleted %d sub-threshold rows", stats["ss_deleted"])
        logger.info(
            "top_signals_of_day: deleted %d sub-threshold rows", stats["tsd_deleted"]
        )

        conn.commit()

        # ── Post-purge counts ────────────────────────────────────────
        cur.execute("SELECT COUNT(*) AS n FROM rating_changes")
        stats["rc_after"] = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM signal_scores")
        stats["ss_after"] = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM top_signals_of_day")
        stats["tsd_after"] = cur.fetchone()["n"]

        logger.info("Purge committed successfully")

    except Exception as e:
        conn.rollback()
        logger.error("Purge FAILED — rolled back: %s", e)
        raise
    finally:
        conn.close()

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Purge sub-threshold rows from rating_changes, signal_scores, top_signals_of_day"
    )
    parser.add_argument("--db", default="data/trading_system.db", help="Path to SQLite DB")
    args = parser.parse_args()

    logger.info(
        "Purging rows below $%.2f from: %s", MIN_PRICE_FOR_SIGNAL, args.db
    )
    stats = purge(args.db)

    print("\n" + "=" * 55)
    print("PURGE REPORT")
    print("=" * 55)
    print(f"  MIN_PRICE_FOR_SIGNAL        : ${MIN_PRICE_FOR_SIGNAL:.2f}")
    print(f"  rating_changes before       : {stats['rc_before']:,}")
    print(f"  rating_changes deleted      : {stats['rc_deleted']:,}")
    print(f"  rating_changes after        : {stats['rc_after']:,}")
    print(f"  signal_scores before        : {stats['ss_before']:,}")
    print(f"  signal_scores deleted       : {stats['ss_deleted']:,}")
    print(f"  signal_scores after         : {stats['ss_after']:,}")
    print(f"  top_signals_of_day before   : {stats['tsd_before']:,}")
    print(f"  top_signals_of_day deleted  : {stats['tsd_deleted']:,}")
    print(f"  top_signals_of_day after    : {stats['tsd_after']:,}")
    print("=" * 55)


if __name__ == "__main__":
    main()
