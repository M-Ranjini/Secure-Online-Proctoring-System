from flask import Blueprint, request, render_template, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash
from database.db_utils import get_db
import re

signup_bp = Blueprint("signup", __name__)

EMAIL_REGEX = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'

@signup_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":

        full_name = request.form.get("full_name")
        email = request.form.get("email").strip().lower()
        password = request.form.get("password")

        if not all([full_name, email, password]):
            flash("Required fields missing")
            return redirect("/signup")

        if not re.match(EMAIL_REGEX, email):
            flash("Invalid email")
            return redirect("/signup")

        hashed_pw = generate_password_hash(password)

        conn = get_db()
        cur = conn.cursor()

        cur.execute("SELECT id FROM users WHERE email=?", (email,))
        if cur.fetchone():
            flash("Email already registered")
            conn.close()
            return redirect("/signup")

        cur.execute("""
        INSERT INTO users(full_name,email,password,role,face_registered)
        VALUES(?,?,?,?,?)
        """,(full_name,email,hashed_pw,"student",0))

        conn.commit()
        conn.close()

        # store email for face registration
        session["temp_user_email"] = email

        return redirect("/face/register-face")

    return render_template("signup.html")