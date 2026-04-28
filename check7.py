from database.db import get_connection
from config.settings import DATABASE_PATH
conn = get_connection(DATABASE_PATH)
cur = conn.cursor()
cur.execute('SELECT id, username FROM users')
for r in cur.fetchall(): print(dict(r))
