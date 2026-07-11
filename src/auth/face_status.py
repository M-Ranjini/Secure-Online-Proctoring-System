import sqlite3

DB_PATH = "src/database/proctoring.db"

def mark_face_registered(email):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE students
        SET face_registered = 1
        WHERE email = ?
    """, (email,))

    conn.commit()
    conn.close()
