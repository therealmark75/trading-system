from database.db import get_connection
from config.settings import DATABASE_PATH
conn = get_connection(DATABASE_PATH)
cur = conn.cursor()
cur.execute('''
SELECT rating, COUNT(*) as count
FROM signal_scores
WHERE DATE(scored_at) = DATE((SELECT MAX(scored_at) FROM signal_scores))
GROUP BY rating ORDER BY count DESC
''')
for r in cur.fetchall(): print(dict(r))
