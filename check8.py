from database.db import get_connection, add_to_watchlist
from config.settings import DATABASE_PATH

result = add_to_watchlist(DATABASE_PATH, 2, 'AAPL', '')
print('Result:', result)

conn = get_connection(DATABASE_PATH)
cur = conn.cursor()
cur.execute('SELECT * FROM watchlists')
for r in cur.fetchall(): print(dict(r))
conn.close()
