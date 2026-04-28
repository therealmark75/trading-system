from database.db import get_connection
from config.settings import DATABASE_PATH
conn = get_connection(DATABASE_PATH)
cur = conn.cursor()
cur.execute("SELECT sql FROM sqlite_master WHERE name='watchlists'")
print(cur.fetchone()[0])
