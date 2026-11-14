import sqlite3

conn = sqlite3.connect("papers.db")

cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS papers(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT,
    summary TEXT,
    keywords TEXT
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    email TEXT,
    password TEXT
)
""")

conn.commit()
conn.close()

print("Database Created Successfully")