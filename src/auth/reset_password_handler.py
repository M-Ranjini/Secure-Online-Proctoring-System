from flask import Blueprint, render_template, request, flash, redirect, url_for
from database.db_utils import get_db
from werkzeug.security import generate_password_hash
from datetime import datetime

reset_password_bp = Blueprint("reset_password", __name__)

@reset_password_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    db = get_db()
    cursor = db.cursor()

    # 1. Verify Token
    cursor.execute(
        "SELECT email, expires_at FROM password_resets WHERE token = ?",
        (token,)
    )
    record = cursor.fetchone()

    if not record:
        flash("Invalid or expired reset link", "error")
        return redirect(url_for("login"))

    email, expires_at = record
    
    # 2. Check Expiration
    try:
        expiry_dt = datetime.strptime(expires_at, '%Y-%m-%d %H:%M:%S')
    except:
        expiry_dt = datetime.fromisoformat(expires_at)
    
    if datetime.utcnow() > expiry_dt:
        flash("Reset link expired", "error")
        return redirect(url_for("login"))

    # 3. Handle Form Submission
    if request.method == "POST":
        new_password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")

        if not new_password or new_password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("reset_password.html", token=token)

        try:
            hashed = generate_password_hash(new_password)

            # UPDATE: We use 'password' as the column name. 
            # If your DB uses 'hashed_password', change 'password = ?' to 'hashed_password = ?' below.
            cursor.execute(
                "UPDATE students SET password = ? WHERE email = ?",
                (hashed, email)
            )
            
            # Delete used token
            cursor.execute("DELETE FROM password_resets WHERE token = ?", (token,))
            db.commit()

            flash("Success! Your password has been updated.", "success")
            return redirect(url_for("login"))

        except Exception as e:
            db.rollback()
            flash(f"Database Error: {str(e)}", "error")
            # If there's an error, we stay on the reset page so you can see the message
            return render_template("reset_password.html", token=token)

    return render_template("reset_password.html", token=token)