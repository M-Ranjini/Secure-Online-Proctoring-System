"""
admin_otp.py  — Email OTP for Admin Two-Factor Authentication
Place at:  src/auth/admin_otp.py

FUNCTION SIGNATURES — must match app.py exactly:

  app.py line 1452:  result = send_otp_email(email)
                     dev_mode = result.get("dev_mode", False)
                     → needs: send_otp_email(email) -> {"success": bool, "dev_mode": bool}

  app.py line 1475:  result = verify_otp(pending_email, submitted_otp)
                     if result["valid"]: ...
                     → needs: verify_otp(email, otp) -> {"valid": bool, "message": str}

  app.py line 71:    from auth.admin_otp import send_otp_email, verify_otp, clear_otp
                     → clear_otp is imported but not called in app.py, keep it anyway

SETUP (.env file — add these two lines):
  ADMIN_EMAIL_SENDER=yourgmail@gmail.com
  ADMIN_EMAIL_PASSWORD=xxxx xxxx xxxx xxxx

  Gmail App Password steps:
    1. myaccount.google.com → Security
    2. Enable 2-Step Verification
    3. Search "App passwords" → Mail → Generate
    4. Paste the 16-char password (keep the spaces, they are OK)

DEV MODE: if .env not set → OTP prints to Flask terminal. Login still works.
"""

import os, random, smtplib, string
from flask import session
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# In-memory store: { email: {"otp": str, "expiry": datetime, "attempts": int} }
_otp_store: dict = {}

OTP_EXPIRY_MINUTES = 5
OTP_MAX_ATTEMPTS   = 5

# Add these to src/auth/admin_otp.py

def generate_otp():
    """Generates a 6-digit numeric string."""
    import random, string
    return ''.join(random.choices(string.digits, k=6))

def store_otp_in_session(session, otp, email):
    """Stores OTP details in the Flask session for tracking."""
    from datetime import datetime, timedelta
    session["admin_otp"] = otp
    session["admin_pending_email"] = email
    # Set expiry for 5 minutes from now
    session["admin_otp_expiry"] = (datetime.now() + timedelta(minutes=5)).isoformat()
    session["admin_otp_attempts"] = 0
# ══════════════════════════════════════════════════════════════
# send_otp_email(email) → {"success": bool, "dev_mode": bool}
# Called by app.py line 1452:  result = send_otp_email(email)
# ══════════════════════════════════════════════════════════════
def send_otp_email(email: str, otp: str = None, admin_name: str = "Admin") -> dict:
    # If otp isn't passed from app.py, generate it here
    if otp is None:
        otp = ''.join(random.choices(string.digits, k=6))
    
    expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
    session["admin_otp_expiry"] = expiry.isoformat()
    
    # Store OTP in the global store
    _otp_store[email] = {"otp": otp, "expiry": expiry, "attempts": 0}

    sender   = "ranjinim421@gmail.com"
    password = "qygnsnhnvuusfwsq"


    # ── DEV MODE: no SMTP configured ──
    if not sender or not password:
        print(f"\n{'='*55}")
        print(f"  [DEV MODE] Admin OTP for {email}:  {otp}")
        print(f"  Valid for {OTP_EXPIRY_MINUTES} minutes.")
        print(f"  To get real emails, add to .env:")
        print(f"    ADMIN_EMAIL_SENDER=ranjinim421@gmail.com")
        print(f"    ADMIN_EMAIL_PASSWORD=qygnsnhnvuusfwsq")
        print(f"{'='*55}\n")
        return {"success": True, "dev_mode": True}

    # ── PRODUCTION MODE: send real email ──
    expiry_str = expiry.strftime("%I:%M %p")

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
body{{font-family:'Segoe UI',Arial,sans-serif;background:#040d1a;margin:0;padding:0}}
.w{{max-width:500px;margin:40px auto;background:#0a1628;border-radius:16px;border:1px solid rgba(168,85,247,0.2);overflow:hidden}}
.hdr{{background:linear-gradient(135deg,#7c3aed,#a855f7);padding:28px 36px}}
.hdr h1{{color:#fff;font-size:20px;margin:0;font-weight:800}}
.hdr p{{color:rgba(255,255,255,0.7);font-size:12px;margin:5px 0 0}}
.body{{padding:36px}}
.txt{{color:#94a3b8;font-size:14px;margin-bottom:20px;line-height:1.6}}
.box{{background:rgba(168,85,247,0.08);border:1px solid rgba(168,85,247,0.25);border-radius:12px;padding:28px;text-align:center;margin:20px 0}}
.lbl{{font-size:11px;font-weight:700;letter-spacing:3px;text-transform:uppercase;color:#a855f7;margin-bottom:14px}}
.code{{font-size:52px;font-weight:900;letter-spacing:12px;color:#f0f7ff;font-family:monospace}}
.exp{{font-size:12px;color:rgba(148,179,220,0.5);margin-top:10px}}
.warn{{background:rgba(239,68,68,0.06);border:1px solid rgba(239,68,68,0.15);border-radius:8px;padding:14px;margin-top:18px}}
.warn p{{color:rgba(239,68,68,0.7);font-size:12px;margin:0;line-height:1.6}}
.foot{{border-top:1px solid rgba(255,255,255,0.06);padding:16px 36px;font-size:11px;color:rgba(148,179,220,0.3);text-align:center}}
</style>
</head>
<body><div class="w">
<div class="hdr"><h1>&#128737; SecureExam AI</h1><p>Admin 2-Factor Authentication</p></div>
<div class="body">
<p class="txt">A login attempt was made to the Admin Console.<br>Use the OTP below to complete verification.</p>
<div class="box">
  <div class="lbl">Your One-Time Password</div>
  <div class="code">{otp}</div>
  <div class="exp">&#9200; Valid until {expiry_str} &middot; {OTP_EXPIRY_MINUTES} minutes only</div>
</div>
<div class="warn"><p>&#9888; <strong style="color:#ef4444">Never share this code.</strong> If you did not attempt this login, change your password immediately.</p></div>
</div>
<div class="foot">SecureExam AI &middot; Do not reply to this email</div>
</div></body></html>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[SecureExam AI] Admin OTP: {otp} — expires {expiry_str}"
        msg["From"]    = f"SecureExam AI <{sender}>"
        msg["To"]      = email
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(sender, password)
            s.sendmail(sender, email, msg.as_string())

        print(f"[OTP] Email sent successfully to {email}")
        return {"success": True, "dev_mode": False}

    except smtplib.SMTPAuthenticationError:
        # Gmail auth failed — fall back to terminal so login isn't blocked
        print(f"[OTP ERROR] Gmail auth failed. Check ADMIN_EMAIL_PASSWORD in .env")
        print(f"[OTP FALLBACK] OTP for {email}: {otp}")
        return {"success": True, "dev_mode": True}

    except Exception as e:
        print(f"[OTP ERROR] {e}")
        print(f"[OTP FALLBACK] OTP for {email}: {otp}")
        return {"success": True, "dev_mode": True}


# ══════════════════════════════════════════════════════════════
# verify_otp(email, entered_otp) → {"valid": bool, "message": str}
# Called by app.py line 1475:  result = verify_otp(pending_email, submitted_otp)
#                               if result["valid"]: ...
# ══════════════════════════════════════════════════════════════
def verify_otp(email: str, entered_otp: str) -> dict:
    record = _otp_store.get(email)

    if not record:
        return {"valid": False, "message": "No OTP found. Please log in again."}

    # FIX: Use timezone-aware comparison
    if datetime.now(timezone.utc) > record["expiry"]:
        _otp_store.pop(email, None)
        return {"valid": False, "message": "OTP has expired. Please log in again."}

    if record["attempts"] >= OTP_MAX_ATTEMPTS:
        _otp_store.pop(email, None)
        return {"valid": False, "message": "Too many incorrect attempts. Please log in again."}

    record["attempts"] += 1

    if entered_otp.strip() == record["otp"]:
        _otp_store.pop(email, None)   # one-time use — delete after success
        return {"valid": True, "message": "OTP verified successfully."}

    remaining = OTP_MAX_ATTEMPTS - record["attempts"]
    if remaining <= 0:
        _otp_store.pop(email, None)
        return {"valid": False, "message": "Too many incorrect attempts. Please log in again."}

    return {"valid": False, "message": f"Incorrect OTP. {remaining} attempt(s) remaining."}


# ══════════════════════════════════════════════════════════════
# clear_otp(email) — imported by app.py line 71, called on logout
# ══════════════════════════════════════════════════════════════
def clear_otp(email: str) -> None:
    _otp_store.pop(email, None)
