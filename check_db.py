import sqlite3

DB_PATH = "/app/data/yoga_bot.sqlite3"

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

print("=== DATABASE CHECK ===")

tables = cur.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
).fetchall()

print("TABLES:")
for row in tables:
    print("-", row[0])

users = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
payments = cur.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
succeeded = cur.execute(
    "SELECT COUNT(*) FROM payments WHERE status='succeeded'"
).fetchone()[0]

print("USERS:", users)
print("PAYMENTS:", payments)
print("SUCCEEDED:", succeeded)

conn.close()

print("=== DONE ===")