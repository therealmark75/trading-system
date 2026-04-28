from database.db import get_connection, add_to_watchlist
from config.settings import DATABASE_PATH

# Test add directly
result = add_to_watchlist(DATABASE_PATH, 1, 'AAPL', '')
print('add_to_watchlist result:', result)

# Check what's in watchlists
conn = get_connection(DATABASE_PATH)
cur = conn.cursor()
cur.execute('SELECT * FROM watchlists LIMIT 5')
for r in cur.fetchall(): print(dict(r))
