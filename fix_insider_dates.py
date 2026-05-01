from database.db import get_connection
from config.settings import DATABASE_PATH
from datetime import datetime

conn = get_connection(DATABASE_PATH)
cur = conn.cursor()

# Find all non-ISO dates
cur.execute("SELECT id, transaction_date FROM insider_trades WHERE transaction_date NOT LIKE '20%'")
rows = cur.fetchall()
print(f"Found {len(rows)} dates to fix")

fixed = 0
for row in rows:
    id_, date_str = row[0], row[1]
    try:
        # Parse "Apr 21 '26" format
        dt = datetime.strptime(date_str.strip(), "%b %d '%y")
        iso = dt.strftime('%Y-%m-%d')
        cur.execute("UPDATE insider_trades SET transaction_date = ? WHERE id = ?", (iso, id_))
        fixed += 1
    except Exception as e:
        print(f"  Could not parse '{date_str}': {e}")

conn.commit()
conn.close()
print(f"Fixed {fixed} dates to ISO format")
