#!/usr/bin/env python3
"""
Re-run the legal risk classifier against all tickers stored in legal_risk table.
Prioritises tickers currently classified at SEC_ENFORCEMENT or CRIMINAL so that
false positives are corrected first.

Usage:
    source venv/bin/activate
    python scripts/reclassify_legal_risk.py [--dry-run] [--ticker AAPL]
"""
import sys
import time
import argparse
import sqlite3
import os

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers.legal_risk_scraper import fetch_legal_risk, save_legal_risk, RISK_ORDER
from config.settings import DATABASE_PATH


def get_all_tickers(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    # Ensure filing_type column exists
    try:
        c.execute("ALTER TABLE legal_risk ADD COLUMN filing_type TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    c.execute("SELECT ticker, risk_level FROM legal_risk ORDER BY ticker")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def sort_priority(rows):
    """High-risk tickers first so false positives are fixed immediately."""
    def key(r):
        lvl = r["risk_level"]
        return RISK_ORDER.index(lvl) if lvl in RISK_ORDER else 0
    return sorted(rows, key=key, reverse=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Classify but do not write to DB")
    parser.add_argument("--ticker", metavar="T",
                        help="Only reclassify a single ticker")
    args = parser.parse_args()

    if args.ticker:
        rows = [{"ticker": args.ticker.upper(), "risk_level": "UNKNOWN"}]
    else:
        rows = get_all_tickers(DATABASE_PATH)

    rows = sort_priority(rows)
    total = len(rows)
    print(f"Reclassifying {total} ticker(s)  [dry_run={args.dry_run}]")
    print("-" * 60)

    changed = 0
    for i, row in enumerate(rows, 1):
        ticker    = row["ticker"]
        old_level = row["risk_level"]
        print(f"[{i:3}/{total}] {ticker:6}  was={old_level:<20}", end=" ", flush=True)

        try:
            result    = fetch_legal_risk(ticker)
            new_level = result["risk_level"]
            delta     = ""
            if new_level != old_level:
                delta = f"  *** CHANGED: {old_level} -> {new_level} (penalty {result['penalty']:+d})"
                changed += 1
            print(f"now={new_level:<20} penalty={result['penalty']:+3}{delta}")
            if not args.dry_run:
                save_legal_risk(ticker, result)
        except Exception as e:
            print(f"ERROR: {e}")

        # Respect SEC EDGAR rate limits
        time.sleep(1.5)

    print("-" * 60)
    print(f"Done. {changed}/{total} records changed.")
    if args.dry_run:
        print("(Dry run — no DB changes written)")


if __name__ == "__main__":
    main()
