from database.db import get_connection
from config.settings import DATABASE_PATH

conn = get_connection(DATABASE_PATH)
cur = conn.cursor()

# 1. Clean ghost ratings - map old -> new
rating_map = {
    'AVOID':       'STRONG_SELL',
    'REVERSION':   'WEAK_HOLD',
    'SHORT_WATCH': 'WEAK_HOLD',
    'WATCH':       'STRONG_HOLD',
    'HOLD':        'STRONG_HOLD',
}

for old, new in rating_map.items():
    cur.execute("UPDATE signal_scores SET rating = ? WHERE rating = ?", (new, old))
    cur.execute("UPDATE rating_changes SET old_rating = ? WHERE old_rating = ?", (new, old))
    cur.execute("UPDATE rating_changes SET new_rating = ? WHERE new_rating = ?", (new, old))
    cur.execute("UPDATE top_signals_of_day SET rating = ? WHERE rating = ?", (new, old))
    print(f"Mapped {old} -> {new}: {cur.rowcount} rows")

conn.commit()

# 2. Check news for a sample ticker
cur.execute("SELECT ticker, avg_sentiment FROM ticker_sentiment LIMIT 5")
print("\nSample ticker_sentiment:")
for r in cur.fetchall(): print(dict(r))

# 3. Check analyst_recom for sample tickers
cur.execute("""SELECT ticker, analyst_recom FROM screener_snapshots 
               WHERE analyst_recom IS NOT NULL AND analyst_recom != ''
               ORDER BY scraped_at DESC LIMIT 5""")
print("\nSample analyst_recom:")
for r in cur.fetchall(): print(dict(r))

conn.close()
print("\nDone")
