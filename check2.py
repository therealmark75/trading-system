from database.db import get_connection
from config.settings import DATABASE_PATH
conn = get_connection(DATABASE_PATH)
cur = conn.cursor()
cur.execute('SELECT DATE(scored_at) as date, COUNT(*) as count FROM signal_scores GROUP BY DATE(scored_at) ORDER BY date DESC LIMIT 7')
for r in cur.fetchall(): print(dict(r))
