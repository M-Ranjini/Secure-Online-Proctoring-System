from flask import Blueprint, render_template, request, flash, redirect, url_for
from markupsafe import Markup  # Corrected import for newer Flask/MarkupSafe versions
from database.db_utils import get_db
import secrets
from datetime import datetime, timedelta

forgot_password_bp = Blueprint("forgot_password", __name__)

@forgot_password_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        token = secrets.token_urlsafe(32)
        expires_at = (datetime.utcnow() + timedelta(minutes=30)).strftime('%Y-%m-%d %H:%M:%S')

        db = get_db()
        cursor = db.cursor()

        # Check if user exists
        cursor.execute("SELECT id FROM students WHERE email = ?", (email,))
        user = cursor.fetchone()

        if user:
            # Delete any old tokens for this user
            cursor.execute("DELETE FROM password_resets WHERE email = ?", (email,))
            
            # Insert the new token
            cursor.execute(
                "INSERT INTO password_resets (email, token, expires_at) VALUES (?, ?, ?)",
                (email, token, expires_at)
            )
            db.commit()

            # Generate the URL
            reset_link = url_for("reset_password.reset_password", token=token, _external=True)
            
            # Create a clickable link inside the flash message
            # We use 'yellow' so it stands out against the green success bar
            link_html = Markup(
                f'Link Ready: <a href="{reset_link}" style="color: yellow; text-decoration: underline; font-weight: bold;">Click Here to Reset Password</a>'
            )
            flash(link_html, "success")
        else:
            # For security, we give a vague message if the email isn't found
            flash("If that email is registered, a reset link has been generated.", "info")

        # Redirect back to login so the user sees the flash message
        return redirect(url_for("login"))

    return render_template("forgot_password.html")