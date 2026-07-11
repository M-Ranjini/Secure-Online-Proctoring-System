from database.db_utils import get_db

def log_event(user_id, status):
    try:
        #from database.db_utils import get_db

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO login_logs (user_id, status, timestamp)
            VALUES (?, ?, datetime('now','localtime'))
        """, (user_id, status))

        conn.commit()   # 🔥 THIS WAS YOUR MAIN MISSING PIECE

        print(f"🔥 LOGIN LOG SAVED → {status} for user_id={user_id}")

    except Exception as e:
        print("❌ LOG ERROR:", e)

    finally:
        conn.close()