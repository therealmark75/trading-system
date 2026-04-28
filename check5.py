from database.db import get_connection
from config.settings import DATABASE_PATH
import datetime

conn = get_connection(DATABASE_PATH)
try:
    conn.execute("""
        INSERT OR IGNORE INTO watchlists (user_id, ticker, added_at, notes)
        VALUES (?, ?, ?, ?)
    """, (1, 'AAPL', datetime.datetime.now().isoformat(), ''))
    conn.commit()
    print('Success')
    cur = conn.cursor()
    cur.execute('SELECT * FROM watchlists')
    for r in cur.fetchall(): print(dict(r))
except Exception as e:
    print('Error:', e)
finally:
    conn.close()
