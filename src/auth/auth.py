"""
auth.py
Place at: src/auth/auth.py

WHAT THIS FILE CONTAINS:
  - Student login  → /login
  - Student logout → /logout
  - Admin login    → /admin/login   (credentials check → sends OTP → OTP page)
  - Admin OTP      → /admin/verify-otp
  - Admin resend   → /admin/resend-otp

THE FIX:
  The old file had TWO @auth_bp.route("/admin/login") functions.
  Flask only registers the first one it finds — which was the OLD one
  with NO OTP, going straight to dashboard.
  This file has EXACTLY ONE /admin/login route — the correct OTP version.
"""

from flask import Blueprint, render_template, request, redirect, flash, session, jsonify
from werkzeug.security import check_password_hash
from database.db_utils import get_db
from auth.admin_otp import generate_otp, send_otp_email, store_otp_in_session, verify_otp, clear_otp
from auth.audit import log_event
from datetime import datetime, timezone
auth_bp = Blueprint("auth", __name__)


# ════════════════════════════════════════
# STUDENT LOGIN
# ════════════════════════════════════════
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    print("🚀 Student login route running")

    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        conn   = get_db()
        cursor = conn.cursor()
        user   = cursor.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        conn.close()

        if user and check_password_hash(user["password"], password):
            log_event(user["id"], "SUCCESS")

            session["user_id"]      = user["id"]
            session["email"]        = user["email"]
            session["full_name"]    = user["full_name"]
            session["role"]         = user["role"]
            session["face_verified"] = False

            # Reset identity mismatch counter
            try:
                from app import identity_mismatch_count
                identity_mismatch_count[user["email"]] = 0
            except Exception:
                pass

            # Students go to face verify; admins should NOT log in here
            # (admin login is at /admin/login in app.py with OTP)
            if user["role"] == "admin":
                # Safety: if an admin somehow hits /login, send them to admin login
                session.clear()
                flash("Please use the Admin login page.", "warning")
                return redirect("/admin/login")

            return redirect("/face/verify_face")

        else:
            if user:
                log_event(user["id"], "FAILED_LOGIN")
            flash("Invalid email or password.", "error")
            return redirect("/login")

    return render_template("login.html")


# ════════════════════════════════════════
# STUDENT LOGOUT
# ════════════════════════════════════════
@auth_bp.route("/logout")
def logout():
    if "user_id" in session:
        log_event(session["user_id"], "LOGOUT")
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect("/login")


# ════════════════════════════════════════
# ADMIN LOGIN — Step 1: credentials → OTP
# THIS IS THE ONLY /admin/login ROUTE.
# The old duplicate route has been removed.
# ════════════════════════════════════════
@auth_bp.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    # Already fully logged in → go straight to dashboard
    if session.get("role") == "admin":
        return redirect("/admin/dashboard")

    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        if not email or not password:
            flash("Please enter both email and password.", "error")
            return redirect("/admin/login")

        conn   = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE email=? AND role='admin'", (email,))
        user = cursor.fetchone()
        conn.close()

        if not user or not check_password_hash(user["password"], password):
            flash("Invalid admin credentials.", "error")
            return redirect("/admin/login")

        # ── Credentials correct → generate OTP ──
        otp = generate_otp()
        store_otp_in_session(session, otp, email)

        session["admin_pending_id"]   = user["id"]
        session["admin_pending_name"] = user["full_name"]

        # Send OTP email (falls back to terminal in dev mode)
        sent = send_otp_email(email, otp, admin_name=user["full_name"])

        if sent.get("success") :
            flash(f"A 6-digit OTP has been sent to {email}.", "success")
        else:
            flash("Could not send email. Check your SMTP settings.", "error")

        return redirect("/admin/otp-verify")

    return render_template("admin/admin_login.html")


# ════════════════════════════════════════
# ADMIN OTP VERIFY — Step 2: enter OTP
# ════════════════════════════════════════
@auth_bp.route("/admin/otp-verify", methods=["GET", "POST"])
def admin_otp_verify():
    # No pending OTP → send back to login
    if "admin_otp" not in session:
        flash("Please log in first.", "error")
        return redirect("/admin/login")

    email       = session.get("admin_pending_email", "")
    expiry_str  = session.get("admin_otp_expiry", "")
    seconds_left = 300  # default 5 min

    try:
        expiry = datetime.fromisoformat(expiry_str)
        # Ensure this comparison matches your admin_otp.py timezone (UTC)
        delta = expiry - datetime.now(timezone.utc) 
        seconds_left = max(0, int(delta.total_seconds()))
    except Exception:
        pass

    if request.method == "POST":
        entered = request.form.get("otp", "").strip()
        result  = verify_otp(email, entered)

        if result.get("valid"):
            clear_otp(email)

            session["email"]     = email
            session["role"]      = "admin"
            session["user_id"]   = session.pop("admin_pending_id", None)
            session["full_name"] = session.pop("admin_pending_name", "Admin")

            # Audit log
            try:
                conn   = get_db()
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO login_logs (user_id, status, timestamp) VALUES (?,?,datetime('now','localtime'))",
                    (session["user_id"], "admin_otp_success")
                )
                conn.commit()
                conn.close()
            except Exception:
                pass

            flash(f"Welcome back, {session['full_name']}!", "success")
            return redirect("/admin/dashboard")

        else:
            # FIX 2: Use "message" instead of "reason"
            msg = result.get("message", "Invalid OTP")
            if "log in again" in msg.lower():
                clear_otp(email)
                session.pop("admin_pending_id", None)
                session.pop("admin_pending_name", None)
                session.pop("admin_pending_email", None)
                flash(msg, "error")
                return redirect("/admin/login")
            else:
             flash(result.get("message"), "error")

    masked = _mask_email(email)
    return render_template(
        "admin/admin_otp_verify.html",
        admin_email=email,
        masked_email=masked,
        seconds_left=seconds_left
    )


# ════════════════════════════════════════
# RESEND OTP — rate limited to 60s
# ════════════════════════════════════════
@auth_bp.route("/admin/resend-otp", methods=["GET", "POST"])
def admin_resend_otp():
    if "admin_pending_email" not in session:
        if request.method == "POST":
            return jsonify({"success": False, "message": "Session expired."})
        return redirect("/admin/login")

    # Rate limit
    last_sent = session.get("admin_otp_last_sent")
    if last_sent:
        try:
            delta = datetime.now() - datetime.fromisoformat(last_sent)
            if delta.total_seconds() < 60:
                remaining = int(60 - delta.total_seconds())
                if request.method == "POST":
                    return jsonify({"success": False, "message": f"Wait {remaining}s before resending."})
                flash(f"Please wait {remaining} seconds before requesting a new OTP.", "warning")
                return redirect("/admin/otp-verify")
        except Exception:
            pass

    email      = session["admin_pending_email"]
    admin_name = session.get("admin_pending_name", "Admin")
    otp        = generate_otp()
    store_otp_in_session(session, otp, email)
    session["admin_otp_last_sent"] = datetime.now().isoformat()

    sent = send_otp_email(email, otp, admin_name=admin_name)

    if request.method == "POST":
        return jsonify({"success": sent, "message": "OTP sent." if sent else "Send failed — check terminal."})

    flash(f"New OTP sent to {email}." if sent else "OTP send failed — check terminal.", "info" if sent else "warning")
    return redirect("/admin/otp-verify")


# ════════════════════════════════════════
# HELPER
# ════════════════════════════════════════
def _mask_email(email: str) -> str:
    try:
        local, domain = email.split("@")
        masked = local[:2] + "***" if len(local) > 2 else "***"
        return f"{masked}@{domain}"
    except Exception:
        return email