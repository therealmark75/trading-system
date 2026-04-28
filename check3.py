from database.db import get_connection
from config.settings import DATABASE_PATH
conn = get_connection(DATABASE_PATH)
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
for r in cur.fetchall(): print(dict(r))
