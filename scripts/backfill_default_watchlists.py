"""
scripts/backfill_default_watchlists.py
──────────────────────────────────────
One-time idempotent backfill: ensure every user has at least one watchlist
flagged is_default=1.

For each user, exactly one of three branches fires:
  A. has >=1 watchlist with is_default=1 → skip (no-op)
  B. has >=1 watchlist but none flagged default → flag the oldest (lowest id)
  C. has 0 watchlists → INSERT 'My Watchlist' with alerts_enabled=1, is_default=1

Idempotent: a second run hits branch A for every user and changes nothing.

Effects:
  Reads:
    - users (id list)
    - watchlists_meta (per-user existence and is_default flags)
  Writes:
    - watchlists_meta (UPDATE is_default=1 in branch B, INSERT row in branch C)
  Side effects:
    - Prints summary line to stdout

Usage:
  python scripts/backfill_default_watchlists.py
  python scripts/backfill_default_watchlists.py --db path/to/trading_system.db
"""
import sys
import argparse
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.constants import DATABASE_PATH
from database.db import initialise_user_schema


def backfill(db_path: str) -> dict:
    """Run the backfill against db_path. Returns counts dict for testing."""
    initialise_user_schema(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    user_ids = [r[0] for r in cur.execute("SELECT id FROM users WHERE is_active = 1").fetchall()]

    skipped = 0       # branch A
    flagged = 0       # branch B
    created = 0       # branch C

    for uid in user_ids:
        rows = cur.execute(
            "SELECT id, is_default FROM watchlists_meta WHERE user_id=? ORDER BY id ASC",
            (uid,)
        ).fetchall()

        if any(r["is_default"] == 1 for r in rows):
            skipped += 1
            continue

        if rows:
            oldest_id = rows[0]["id"]
            cur.execute(
                "UPDATE watchlists_meta SET is_default=1 WHERE id=?",
                (oldest_id,)
            )
            flagged += 1
            continue

        cur.execute(
            "INSERT INTO watchlists_meta (user_id, name, sort_order, alerts_enabled, is_default) "
            "VALUES (?, 'My Watchlist', 0, 1, 1)",
            (uid,)
        )
        created += 1

    conn.commit()
    conn.close()

    counts = {
        "users_processed": len(user_ids),
        "created": created,
        "flagged": flagged,
        "skipped": skipped,
    }
    print(
        f"{counts['users_processed']} users processed, "
        f"{counts['created']} default watchlists created, "
        f"{counts['flagged']} existing watchlists flagged as default, "
        f"{counts['skipped']} users skipped (already had a default)."
    )
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DATABASE_PATH, help="Path to SQLite DB")
    args = parser.parse_args()
    backfill(args.db)


if __name__ == "__main__":
    main()
