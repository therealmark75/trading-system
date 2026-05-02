import sqlite3, sys, os
sys.path.insert(0, os.path.expanduser("~/Documents/trading-system"))
from config.settings import DATABASE_PATH as DB_PATH

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

c.execute("""
    CREATE TABLE IF NOT EXISTS legal_risk (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker       TEXT UNIQUE NOT NULL,
        risk_level   TEXT NOT NULL DEFAULT 'NONE',
        risk_label   TEXT NOT NULL DEFAULT 'None',
        risk_color   TEXT NOT NULL DEFAULT '#22c55e',
        penalty      INTEGER NOT NULL DEFAULT 0,
        findings_json TEXT,
        scraped_at   TEXT
    )
""")
conn.commit()
conn.close()
print("✅ legal_risk table created (or already exists)")
