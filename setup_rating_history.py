from database.db import get_connection
from config.settings import DATABASE_PATH
from collections import defaultdict

conn = get_connection(DATABASE_PATH)
cur = conn.cursor()

# STEP 1: Create rating_changes table
cur.executescript("""
DROP TABLE IF EXISTS rating_changes;
CREATE TABLE rating_changes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT NOT NULL,
    old_rating      TEXT,
    new_rating      TEXT NOT NULL,
    price_at_change REAL,
    change_date     TEXT NOT NULL,
    composite_score REAL
);
CREATE INDEX IF NOT EXISTS idx_rc_ticker ON rating_changes(ticker);
CREATE INDEX IF NOT EXISTS idx_rc_date ON rating_changes(change_date);
""")
conn.commit()
print("Step 1 done: rating_changes table created")

# STEP 2: Backfill from signal_scores
cur.execute("""
    SELECT ticker, DATE(scored_at) as day, rating, composite_score
    FROM signal_scores
    ORDER BY ticker, scored_at ASC
""")
rows = cur.fetchall()

by_ticker = defaultdict(list)
for r in rows:
    by_ticker[r['ticker']].append(r)

changes_inserted = 0
for ticker, entries in by_ticker.items():
    prev_rating = None
    for entry in entries:
        current_rating = entry['rating']
        if current_rating != prev_rating:
            cur.execute("""
                SELECT price FROM screener_snapshots
                WHERE ticker = ? AND DATE(scraped_at) = ?
                ORDER BY scraped_at DESC LIMIT 1
            """, (ticker, entry['day']))
            price_row = cur.fetchone()
            price = price_row['price'] if price_row else None

            cur.execute("""
                INSERT INTO rating_changes 
                (ticker, old_rating, new_rating, price_at_change, change_date, composite_score)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (ticker, prev_rating, current_rating, price, entry['day'], entry['composite_score']))
            changes_inserted += 1
            prev_rating = current_rating

conn.commit()
print(f"Step 2 done: {changes_inserted} rating changes backfilled")

# STEP 3: Backtesting stats
cur.execute("""
    SELECT 
        rc1.new_rating,
        ROUND(((rc2.price_at_change - rc1.price_at_change) / rc1.price_at_change) * 100, 2) as return_pct
    FROM rating_changes rc1
    JOIN rating_changes rc2 
        ON rc1.ticker = rc2.ticker 
        AND rc2.change_date > rc1.change_date
        AND NOT EXISTS (
            SELECT 1 FROM rating_changes rc3
            WHERE rc3.ticker = rc1.ticker
            AND rc3.change_date > rc1.change_date
            AND rc3.change_date < rc2.change_date
        )
    WHERE rc1.price_at_change IS NOT NULL 
    AND rc2.price_at_change IS NOT NULL
    AND rc1.price_at_change > 0
""")
periods = cur.fetchall()

stats = defaultdict(lambda: {'returns': [], 'wins': 0, 'total': 0})
for p in periods:
    r = p['return_pct']
    rating = p['new_rating']
    if r is None: continue
    stats[rating]['returns'].append(r)
    stats[rating]['total'] += 1
    if rating in ('STRONG_BUY','BUY') and r > 0:
        stats[rating]['wins'] += 1
    elif rating in ('STRONG_SELL','SELL') and r < 0:
        stats[rating]['wins'] += 1

print("\nStep 3: Backtesting Summary")
print(f"{'Rating':<15} {'Avg Return':>12} {'Win Rate':>10} {'Samples':>8}")
print("-" * 50)
for rating in ['STRONG_BUY','BUY','STRONG_HOLD','HOLD','WEAK_HOLD','SELL','STRONG_SELL']:
    s = stats.get(rating)
    if not s or not s['returns']: continue
    avg = sum(s['returns']) / len(s['returns'])
    win_rate = (s['wins'] / s['total'] * 100) if s['total'] > 0 else 0
    print(f"{rating:<15} {avg:>+11.2f}% {win_rate:>9.1f}% {s['total']:>8}")

conn.close()
print("\nAll done.")
