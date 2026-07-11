from werkzeug.security import check_password_hash
from database.db_utils import get_db
import requests
import os

RECAPTCHA_SECRET_KEY = os.getenv("RECAPTCHA_SECRET_KEY")

def verify_user(email, password, recaptcha_response=None, login_attempts=0):
    try:
        # 1. reCAPTCHA Check
        if login_attempts >= 3:
            if not recaptcha_response:
                return None
            recaptcha_verify_url = "https://www.google.com/recaptcha/api/siteverify"
            payload = {"secret": RECAPTCHA_SECRET_KEY, "response": recaptcha_response}
            response = requests.post(recaptcha_verify_url, data=payload)
            if not response.json().get("success", False):
                return None

        # 2. Database Check
        conn = get_db()
        cursor = conn.cursor()
        
        # CHANGED: Table name is 'users', not 'students'
        cursor.execute("SELECT * FROM users WHERE email=?", (email,))
        user = cursor.fetchone()
        conn.close()

        # 3. Credential Check
        # CHANGED: Column name is 'password', not 'password_hash'
        if user and check_password_hash(user["password"], password):
            return dict(user)

        return None

    except Exception as e:
        print("Login error:", e)
        return None

def get_user_by_email(email):
    try:
        conn = get_db()
        cursor = conn.cursor()
        # CHANGED: Table name is 'users'
        cursor.execute("SELECT * FROM users WHERE email=?", (email,))
        user = cursor.fetchone()
        conn.close()
        return user
    except Exception as e:
        print("DB error:", e)
        return None