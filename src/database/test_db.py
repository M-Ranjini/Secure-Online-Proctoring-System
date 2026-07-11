import sqlite3

# connect to your database
conn = sqlite3.connect("src/database/proctoring.db")

cursor = conn.cursor()

# check users table
cursor.execute("SELECT id, full_name, email, face_registered FROM users")

rows = cursor.fetchall()

for row in rows:
    print(row)

conn.close()