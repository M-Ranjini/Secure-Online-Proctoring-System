import os
from dotenv import load_dotenv
load_dotenv()  # This manually loads the .env file into your environment
import sys
import csv
import io
import json
import base64
import glob
import sqlite3
import numpy as np
from datetime import datetime
from flask import (
    Flask, render_template, request, session,
    redirect, flash, url_for, jsonify, make_response, send_from_directory
)
import face_recognition
import threading
import time as _time
import queue as _queue
import threading as _threading

# ── Suppress TF warnings ──
os.environ['TF_CPP_MIN_LOG_LEVEL']  = '3'
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'

# ── Paths ──
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# ── Global State ──
identity_mismatch_count = {}
terminated_students     = set()

# ── Email notification helper (reuses OTP smtp setup) ──
def _send_notification_email(to_email: str, subject: str, body_html: str):
    """Send admin notification emails using same SMTP config as OTP."""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    sender  = os.environ.get("ADMIN_EMAIL_SENDER", "")
    passwd  = os.environ.get("ADMIN_EMAIL_PASSWORD", "")
    if not sender or not passwd:
        print(f"[Email Notify] DEV MODE — {subject}")
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"SecureExam AI <{sender}>"
        msg["To"]      = to_email
        msg.attach(MIMEText(body_html, "html"))
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=8) as s:
            s.starttls(); s.login(sender, passwd)
            s.sendmail(sender, to_email, msg.as_string())
    except Exception as e:
        print(f"[Email Notify] Failed: {e}")

# ── Custom Module Imports ──
from database.db_utils            import get_db
from utils.captcha                 import verify_captcha
from auth.signup_handler           import signup_bp
from auth.login_handler            import verify_user
from auth.forgot_password_handler  import forgot_password_bp
from auth.reset_password_handler   import reset_password_bp
from auth.audit                    import log_event
from auth.auth                     import auth_bp
from face.register_face            import face_bp
from face.verify_face              import face_verify_bp
from exam.exam_instructions        import instructions_bp
from exam.exam_page                import exam_bp
from exam_mgmt.routes              import exam_mgmt_bp
from exam_monitor                  import get_ai_status, analyze_frame, ai_status_data
from auth.admin_otp                import send_otp_email, verify_otp, clear_otp
from auth.staff_analytics import staff_bp, run_migrations, record_student_paper_attendance

# ══════════════════════════════════════════════════════════════════
# APP INIT
# ══════════════════════════════════════════════════════════════════
app = Flask(__name__, template_folder=TEMPLATE_DIR)
app.secret_key = "secure_exam_secret_2026"

app.register_blueprint(auth_bp)
app.register_blueprint(signup_bp)
app.register_blueprint(face_bp)
app.register_blueprint(face_verify_bp)
app.register_blueprint(instructions_bp)
app.register_blueprint(exam_bp)
app.register_blueprint(forgot_password_bp)
app.register_blueprint(reset_password_bp)
app.register_blueprint(exam_mgmt_bp)
app.register_blueprint(staff_bp)


@app.context_processor
def inject_branding():
    return {
        "project_name": "SecureExam AI",
        "branding": {
            "project_name":  "SecureExam AI",
            "logo_url":      "/static/images/logo.png",
            "primary_color": "#3b82f6",
            "support_email": "support@secureexam.ai"
        }
    }


# ══════════════════════════════════════════════════════════════════
# HOME
# ══════════════════════════════════════════════════════════════════
@app.route("/")
def home():
    return render_template("home.html")


# ══════════════════════════════════════════════════════════════════
# STUDENT DASHBOARD
# ══════════════════════════════════════════════════════════════════
@app.route("/dashboard")
def dashboard():
    if "email" not in session:
        return redirect("/login")
    if not session.get("face_verified"):
        flash("Please verify your face first.", "warning")
        return redirect("/face/verify_face")

    conn   = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # ── Show ALL papers (admin sets status; for now show all so students always see them) ──
    # To restrict to only published: WHERE status='live'
    cursor.execute("SELECT id, title, subject, total_marks, duration, status FROM papers ORDER BY id DESC")
    exams = cursor.fetchall()

    cursor.execute("SELECT COUNT(*) FROM violations WHERE email=?", (session["email"],))
    violation_count = cursor.fetchone()[0]

    # Last result
    last_result = None
    try:
        cursor.execute(
            "SELECT score, total, submitted_at FROM exam_results WHERE email=? ORDER BY id DESC LIMIT 1",
            (session["email"],)
        )
        last_result = cursor.fetchone()
    except Exception:
        pass

    # All past results
    exam_results = []
    try:
        cursor.execute(
            "SELECT score, total, submitted_at FROM exam_results WHERE email=? ORDER BY id DESC",
            (session["email"],)
        )
        exam_results = cursor.fetchall()
    except Exception:
        pass

    cursor.execute(
        "SELECT reason, timestamp FROM violations WHERE email=? ORDER BY timestamp DESC LIMIT 5",
        (session["email"],)
    )
    recent_violations = cursor.fetchall()
    conn.close()

    return render_template(
        "dashboard_student.html",
        exams=exams,
        violation_count=violation_count,
        last_result=last_result,
        exam_results=exam_results,
        violations=recent_violations
    )


# ══════════════════════════════════════════════════════════════════
# MY REPORTS
# ══════════════════════════════════════════════════════════════════
@app.route("/my-reports")
def my_reports():
    if "email" not in session:
        return redirect("/login")

    conn   = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(
        "SELECT reason, timestamp FROM violations WHERE email=? ORDER BY timestamp DESC",
        (session["email"],)
    )
    user_violations = cursor.fetchall()

    exam_results = []
    try:
        cursor.execute(
            "SELECT score, total, submitted_at FROM exam_results WHERE email=? ORDER BY id DESC",
            (session["email"],)
        )
        exam_results = cursor.fetchall()
    except Exception:
        pass

    conn.close()

    return render_template(
        "dashboard_student.html",
        violations=user_violations,
        exam_results=exam_results,
        violation_count=len(user_violations),
        exams=[],
        last_result=None
    )


# ══════════════════════════════════════════════════════════════════
# LOGOUT
# ══════════════════════════════════════════════════════════════════
@app.route("/logout")
def logout():
    conn   = get_db()
    cursor = conn.cursor()
    if "email" in session:
        cursor.execute("SELECT id FROM users WHERE email=?", (session["email"],))
        user = cursor.fetchone()
        if user:
            try:
                cursor.execute(
                    "INSERT INTO audit_logs (user_id, event_type, timestamp) VALUES (?,?,datetime('now','localtime'))",
                    (user[0], "LOGOUT")
                )
                conn.commit()
            except Exception:
                pass
    conn.close()
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect("/login")


# ══════════════════════════════════════════════════════════════════
# EXAM PAGE — load questions from DB
# ══════════════════════════════════════════════════════════════════
@app.route("/exam")
def exam_page_route():
    if "email" not in session:
        return redirect("/login")
    if not session.get("face_verified"):
        flash("Please verify your face first.", "warning")
        return redirect("/face/verify_face")

    exam_id = request.args.get("exam_id")
    conn = get_db()
    # Ensure row_factory is Row to access by column name
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    exam_row = None
    questions = []

    # 1. Fetch the exam row
    if exam_id:
        exam_row = cursor.execute("SELECT * FROM papers WHERE id=?", (exam_id,)).fetchone()
    
    # Fallback: first available paper if ID not found or not provided
    if not exam_row:
        exam_row = cursor.execute("SELECT * FROM papers ORDER BY id LIMIT 1").fetchone()

    # 2. Process the data
    exam = None
    if exam_row:
        # Convert the Row object to a dictionary for JSON serialization
        exam = dict(exam_row)
        
        if exam.get("questions"):
            try:
                # If questions are stored as a JSON string in the DB
                questions = json.loads(exam["questions"])
            except Exception:
                questions = []

    conn.close()
    session["violations"] = 0

    # Auto-mark student attendance for linked staff exam
    if exam and "email" in session:
        try:
            import threading
            exam_id_for_att = exam.get("id") or exam_id
            student_email   = session["email"]
            threading.Thread(
                target=record_student_paper_attendance,
                args=(exam_id_for_att, student_email),
                daemon=True
            ).start()
        except Exception as _e:
            print(f"[AttendanceHook] {_e}")

    # Notify admin that student started exam
    if exam and "email" in session:
        import threading
        threading.Thread(target=_notify_admin_exam_event, args=(
            session.get("full_name", session["email"]),
            session["email"], "started",
            exam["title"] if exam else ""
        ), daemon=True).start()

    return render_template("exam.html", exam=exam, questions=questions)

# ══════════════════════════════════════════════════════════════════
# SUBMIT EXAM
# ══════════════════════════════════════════════════════════════════
@app.route("/submit-exam", methods=["POST"])
def submit_exam_route():
    data    = request.json or {}
    answers = data.get("answers", {})
    exam_id = data.get("exam_id")
 
    # ── FIX 1: fallback to session-stored exam_id ──
    if not exam_id:
        exam_id = session.get("current_exam_id")
    if not exam_id:
        return jsonify({"score": 0, "total": 0, "error": "no_exam_id"})
 
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
 
    paper = cursor.execute("SELECT questions FROM papers WHERE id=?", (exam_id,)).fetchone()
    if not paper or not paper["questions"]:
        conn.close()
        return jsonify({"score": 0, "total": 0, "error": "paper_not_found"})
 
    questions = json.loads(paper["questions"])
    score = 0
    for i, q in enumerate(questions):
        q_id        = q.get("id", i + 1)
        correct     = (q.get("correct") or q.get("correct_answer") or "").strip().upper()
        student_ans = answers.get(f"q_{q_id}", "").strip().upper()
        if student_ans and student_ans == correct:
            score += 1
 
    # ── FIX 2: accept email from payload if session lost ──
    email = session.get("email") or data.get("student_email", "")
    print(f"[Submit] email={email!r} exam_id={exam_id} score={score}/{len(questions)}")
 
    if email:
        try:
            import threading
            threading.Thread(target=_notify_admin_exam_event, args=(
                session.get("full_name", email), email, "submitted", ""
            ), daemon=True).start()
        except Exception:
            pass
 
        try:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS exam_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT, exam_id INTEGER, score INTEGER, total INTEGER,
                    submitted_at TEXT DEFAULT (datetime(\'now\',\'localtime\'))
                )
            """)
            cursor.execute(
                "INSERT INTO exam_results (email, exam_id, score, total) VALUES (?,?,?,?)",
                (email, exam_id, score, len(questions))
            )
            conn.commit()
            print(f"[Submit] Saved to DB: {email} {score}/{len(questions)}")
        except Exception as e:
            print(f"[Submit] DB save error: {e}")
            conn.rollback()
    else:
        print("[Submit] WARNING: No email found in session or payload — result NOT saved")
 
    conn.close()
    return jsonify({"score": score, "total": len(questions), "ok": True})

# ══════════════════════════════════════════════════════════════════
# AI FRAME ANALYSIS — THE BIG FIX
# Now calls BOTH face_recognition (identity) AND analyze_frame (YOLO/phone/deepfake)
# ══════════════════════════════════════════════════════════════════
@app.route("/analyze-frame", methods=["POST"])
def analyze_frame_route():
    data      = request.json or {}
    image_b64 = data.get("image")
    # ── FIX: accept email from payload as fallback ──
    # exam.html sends {image, email:STUDENT_EMAIL} on every frame.
    # Session email can be missing on some requests during long exams.
    email = session.get("email") or data.get("email", "").strip().lower() or None

    if not image_b64:
        return jsonify({"status": "error", "message": "Missing image",
                        "liveness": True, "face": True, "phone": False,
                        "gadget": False, "deepfake": False, "multiple_faces": False,
                        "match": None, "reason": "No image"})

    if not email:
        # No email at all — skip identity check, still run YOLO
        monitor_result = {}
        try:
            monitor_result = analyze_frame(image_b64)
        except Exception as e:
            print(f"[exam_monitor] error: {e}")
            monitor_result = {"liveness":True,"face":True,"phone":False,"gadget":False,"deepfake":False,"multiple_faces":False}
        monitor_result["match"]  = None
        monitor_result["reason"] = "No session email"
        return jsonify(monitor_result)

    # ── Step 1: Run YOLO + liveness + face detection (exam_monitor.py) ──
    monitor_result = {}
    try:
        monitor_result = analyze_frame(image_b64)
    except Exception as e:
        print(f"[exam_monitor] analyze_frame error: {e}")
        monitor_result = {
            "liveness": True, "face": True, "phone": False,
            "gadget": False, "deepfake": False, "multiple_faces": False,
            "violation": None
        }

    # ── Step 2: Face identity check (face_recognition) ──
    identity_match = True
    identity_reason = None

    try:
        conn = get_db()
        conn.row_factory = sqlite3.Row
        user = conn.execute("SELECT face_encoding FROM users WHERE email=?", (email,)).fetchone()
        conn.close()

        if user and user["face_encoding"]:
            registered_encoding = np.array(json.loads(user["face_encoding"]))

            # Decode image
            if "," in image_b64:
                _, encoded = image_b64.split(",", 1)
            else:
                encoded = image_b64
            img_bytes    = base64.b64decode(encoded)
            current_img  = face_recognition.load_image_file(io.BytesIO(img_bytes))
            current_encs = face_recognition.face_encodings(current_img)

            if current_encs:
                match = face_recognition.compare_faces(
                    [registered_encoding], current_encs[0], tolerance=0.52
                )
                if match[0]:
                    identity_match  = True
                else:
                    identity_match  = False
                    identity_reason = "Identity mismatch"
            else:
                # No face found by face_recognition — camera/lighting issue
                identity_match  = True  # Don't flag as mismatch if no face visible
                identity_reason = "No face encodeable"
    except Exception as e:
        print(f"[face_recognition] error: {e}")
        identity_match = True  # Fail open — don't penalise for processing errors

    # ── Step 3: Merge results ──
    response = {
        # From exam_monitor (YOLO + mediapipe server-side)
        "liveness":       monitor_result.get("liveness", True),
        "face":           monitor_result.get("face", True),
        "head":           monitor_result.get("head", False),
        "phone":          monitor_result.get("phone", False),
        "gadget":         monitor_result.get("gadget", False),
        "deepfake":       monitor_result.get("deepfake", False),
        "multiple_faces": monitor_result.get("multiple_faces", False),
        "violation":      monitor_result.get("violation", None),
        # From face_recognition (identity)
        "match":          identity_match,
        "reason":         identity_reason,
        "status":         "ok" if identity_match else "warning"
    }

    return jsonify(response)


# ── AI Status ──
@app.route("/ai-status")
def ai_status_route():
    return jsonify(ai_status_data)


# ══════════════════════════════════════════════════════════════════
# VIOLATION LOGGING
# ══════════════════════════════════════════════════════════════════
@app.route("/log-violation", methods=["POST"])
def log_violation():
    if "email" not in session:
        return jsonify({"status": "error"}), 403

    data           = request.json or {}
    reason         = data.get("reason", "Unknown")
    evidence_image = data.get("evidence_image", None)
    email          = session["email"]

    conn   = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "INSERT INTO violations (email, reason, evidence_image) VALUES (?,?,?)",
            (email, reason, evidence_image)
        )
        conn.commit()
    except Exception:
        try:
            cursor.execute("INSERT INTO violations (email, reason) VALUES (?,?)", (email, reason))
            conn.commit()
        except Exception as e2:
            print(f"Violation log error: {e2}")

    cursor.execute("SELECT COUNT(*) FROM violations WHERE email=?", (email,))
    total_count = cursor.fetchone()[0]
    conn.close()

    # Backend NEVER auto-terminates — only sends notification
    # Only admin clicking Terminate in dashboard ends a session
    if total_count >= 999:   # effectively unreachable
        return jsonify({"status": "terminate", "count": total_count,
                        "message": "Excessive violations — admin notified."})

    return jsonify({"status": "ok", "count": total_count})


# ── Save cheating evidence screenshot ──
@app.route("/save-cheating-frame", methods=["POST"])
def save_cheating_frame():
    data   = request.json or {}
    image  = data.get("image")
    email  = data.get("email")
    reason = data.get("reason", "unknown")

    if not image or not email:
        return jsonify({"status": "error", "message": "Missing data"})

    try:
        email_folder = email.replace("@", "_").replace(".", "_")
        folder       = os.path.join(BASE_DIR, "..", "cheating_evidence", email_folder)
        os.makedirs(folder, exist_ok=True)

        if "," in image:
            _, encoded = image.split(",", 1)
        else:
            encoded = image
        img_data    = base64.b64decode(encoded)
        timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_reason = reason.replace(" ", "_").lower()[:40]
        filename    = f"{safe_reason}_{timestamp}.jpg"
        path        = os.path.join(folder, filename)

        with open(path, "wb") as f:
            f.write(img_data)

        # Also update the violations table with the evidence filename
        try:
            conn   = get_db()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE violations SET evidence_image=?
                WHERE email=? AND evidence_image IS NULL
                ORDER BY id DESC LIMIT 1
            """, (f"{email_folder}/{filename}", email))
            conn.commit()
            conn.close()
        except Exception:
            pass

        return jsonify({"status": "saved", "filename": f"{email_folder}/{filename}"})

    except Exception as e:
        print(f"Screenshot save error: {e}")
        return jsonify({"status": "error", "message": str(e)})


# ── Clear violations ──
@app.route("/clear-violations", methods=["POST"])
def clear_violations():
    if "email" not in session:
        return jsonify({"status": "error"})
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM violations WHERE email=?", (session["email"],))
    conn.commit()
    conn.close()
    return jsonify({"status": "cleared"})


# ── Save final violation count ──
@app.route("/save-final-violations", methods=["POST"])
def save_final_violations():
    if "email" not in session:
        return jsonify({"status": "error"})
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM violations WHERE email=?", (session["email"],))
    count = cursor.fetchone()[0]
    conn.close()
    session["last_exam_violations"] = count
    session.modified = True
    return jsonify({"status": "ok", "count": count})





# ── Face registration status check ──
@app.route("/check-face-status")
def check_face_status():
    if "email" not in session:
        return jsonify({"registered": False})
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT face_registered FROM users WHERE email=?", (session["email"],))
    user = cursor.fetchone()
    conn.close()
    return jsonify({"registered": bool(user and user[0])})


# ══════════════════════════════════════════════════════════════════
# ADMIN — DASHBOARD
# ══════════════════════════════════════════════════════════════════
@app.route("/admin/dashboard")
def admin_dashboard():
    if session.get("role") != "admin":
        return redirect("/admin/login")

    conn   = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM users WHERE role='student'")
    total_students = cursor.fetchone()[0]

    # Count from papers table (not exams)
    cursor.execute("SELECT COUNT(*) FROM papers")
    total_exams = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM violations")
    total_violations = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE face_registered=1")
    total_face_registered = cursor.fetchone()[0]

    cursor.execute("SELECT id, full_name, email, role, face_registered FROM users WHERE role='student'")
    students = [list(row) for row in cursor.fetchall()]

    student_violations = {}
    for s in students:
        cursor.execute("SELECT COUNT(*) FROM violations WHERE email=?", (s[2],))
        student_violations[s[2]] = cursor.fetchone()[0]

    cursor.execute("""
        SELECT id, email, reason, timestamp, evidence_image
        FROM violations ORDER BY timestamp DESC LIMIT 5
    """)
    recent_violations = [list(row) for row in cursor.fetchall()]

    cursor.execute("SELECT id, email, reason, timestamp FROM violations ORDER BY timestamp DESC")
    all_violations = [list(row) for row in cursor.fetchall()]

    try:
        cursor.execute("""
            SELECT u.email, l.status, l.timestamp
            FROM login_logs l JOIN users u ON l.user_id=u.id
            ORDER BY l.id DESC LIMIT 20
        """)
        login_logs = [list(row) for row in cursor.fetchall()]
    except Exception:
        login_logs = []

    cursor.execute("SELECT * FROM exams")
    exams = [list(row) for row in cursor.fetchall()]

    cursor.execute("SELECT id, title, subject, total_marks, duration FROM papers ORDER BY id DESC")
    papers = [list(row) for row in cursor.fetchall()]

    conn.close()

    return render_template(
        "admin/admin_dashboard.html",
        total_students=total_students,
        total_exams=total_exams,
        total_violations=total_violations,
        total_face_registered=total_face_registered,
        full_name=session.get("full_name", "Admin"),
        students_json=students,
        violations_json=all_violations,
        recent_violations_json=recent_violations,
        login_logs_json=login_logs,
        exams_json=exams,
        papers_json=papers,
        student_violations_json=student_violations
    )


# ══════════════════════════════════════════════════════════════════
# ADMIN — BULK UPLOAD (FIXED CSV PARSING)
# ══════════════════════════════════════════════════════════════════
@app.route("/admin/bulk-upload", methods=["POST"])
def bulk_upload():
    if session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 403
    try:
        if "file" not in request.files:
            return jsonify({"status": "error", "message": "No file uploaded"}), 400

        file     = request.files["file"]
        title    = request.form.get("paper_title", "Untitled Paper")
        subject  = request.form.get("subject", "General")
        marks    = request.form.get("total_marks", 100)
        duration = request.form.get("duration", 60)

        if not file.filename:
            return jsonify({"status": "error", "message": "No file selected"}), 400

        # ── Read with BOM handling ──
        raw = file.stream.read()
        # Try UTF-8-sig first (handles Excel BOM), fallback to latin-1
        try:
            content = raw.decode("utf-8-sig")
        except Exception:
            content = raw.decode("latin-1")

        stream    = io.StringIO(content, newline=None)
        reader    = csv.DictReader(stream)

        # ── Normalize column names (strip whitespace, lowercase) ──
        raw_fields = reader.fieldnames or []
        normalized  = {f.strip().lower(): f for f in raw_fields}

        # Accept multiple possible column name variants
        COL_MAP = {
            "question": ["question", "question_text", "q", "questions"],
            "option_a": ["option_a", "a", "opt_a", "choice_a"],
            "option_b": ["option_b", "b", "opt_b", "choice_b"],
            "option_c": ["option_c", "c", "opt_c", "choice_c"],
            "option_d": ["option_d", "d", "opt_d", "choice_d"],
            "answer":   ["answer", "correct", "correct_answer", "ans", "key"],
        }

        def find_col(field_key):
            for variant in COL_MAP[field_key]:
                if variant in normalized:
                    return normalized[variant]
            return None

        col_q  = find_col("question")
        col_a  = find_col("option_a")
        col_b  = find_col("option_b")
        col_c  = find_col("option_c")
        col_d  = find_col("option_d")
        col_ans= find_col("answer")

        if not col_q or not col_ans:
            return jsonify({
                "status": "error",
                "message": f"CSV must have 'question' and 'answer' columns. Found: {raw_fields}"
            }), 400

        questions_list = []
        for row in reader:
            q_text = (row.get(col_q) or "").strip()
            ans    = (row.get(col_ans) or "").strip().upper()
            if not q_text or not ans:
                continue
            questions_list.append({
                "text":    q_text,
                "options": [
                    (row.get(col_a) or "").strip() if col_a else "",
                    (row.get(col_b) or "").strip() if col_b else "",
                    (row.get(col_c) or "").strip() if col_c else "",
                    (row.get(col_d) or "").strip() if col_d else "",
                ],
                "correct": ans
            })

        if not questions_list:
            return jsonify({
                "status": "error",
                "message": "No valid questions found. Check your CSV has data rows."
            }), 400

        conn   = get_db()
        cursor = conn.cursor()

        # Ensure status column exists
        try:
            cursor.execute("ALTER TABLE papers ADD COLUMN status TEXT DEFAULT 'draft'")
        except Exception:
            pass

        # Ensure created_by columns exist
        for col in [
            "ALTER TABLE papers ADD COLUMN created_by_email TEXT",
            "ALTER TABLE papers ADD COLUMN created_by_name  TEXT",
        ]:
            try: cursor.execute(col)
            except Exception: pass

        #created_by_email = request.form.get("created_by_email") or session.get("email", "")
        #created_by_name  = session.get("full_name", "")

        for _col in [
            "ALTER TABLE papers ADD COLUMN created_by_email TEXT",
            "ALTER TABLE papers ADD COLUMN created_by_name  TEXT",
        ]:
            try: cursor.execute(_col)
            except Exception: pass

        _cbe = request.form.get("created_by_email") or session.get("email", "")
        _cbn = session.get("full_name", "")

        cursor.execute("""
            INSERT INTO papers
                (title, subject, total_marks, duration, questions, status,
                 created_by_email, created_by_name)
            VALUES (?,?,?,?,?,'draft',?,?)
        """, (title, subject, marks, duration,
                json.dumps(questions_list), _cbe, _cbn))
        paper_id = cursor.lastrowid

        conn.commit()
        conn.close()

        return jsonify({
            "status":   "success",
            "message":  f"✅ Imported {len(questions_list)} questions! Paper ID: {paper_id}",
            "paper_id": paper_id,
            "count":    len(questions_list)
        })

    except Exception as e:
        print(f"Bulk upload error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ── Save paper (manual) ──
@app.route("/admin/save-paper", methods=["POST"])
def save_paper():
    if session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 403
    data   = request.json or {}
    conn   = get_db()
    cursor = conn.cursor()
    # Ensure columns exist
    for col in [
        "ALTER TABLE papers ADD COLUMN status TEXT DEFAULT 'draft'",
        "ALTER TABLE papers ADD COLUMN created_by_email TEXT",
        "ALTER TABLE papers ADD COLUMN created_by_name  TEXT",
    ]:
        try: cursor.execute(col)
        except Exception: pass

    # Record who created this paper — from session OR from payload (if set by frontend)
    created_by_email = data.get("created_by_email") or session.get("email", "")
    created_by_name  = session.get("full_name", "")

    for _col in [
        "ALTER TABLE papers ADD COLUMN created_by_email TEXT",
        "ALTER TABLE papers ADD COLUMN created_by_name  TEXT",
    ]:
        try: cursor.execute(_col)
        except Exception: pass

    _created_by_email = data.get("created_by_email") or session.get("email", "")
    _created_by_name  = data.get("created_by_name")  or session.get("full_name", "")

    cursor.execute("""
        INSERT INTO papers
            (title, subject, total_marks, duration, questions, status,
             created_by_email, created_by_name)
        VALUES (?,?,?,?,?,'draft',?,?)
    """, (data.get("title"), data.get("subject"),
          data.get("total_marks"), data.get("duration"),
          json.dumps(data.get("questions", [])),
          _created_by_email, _created_by_name))
    _paper_id = cursor.lastrowid

    conn.commit()
    conn.close()
    return jsonify({"status": "success", "message": "Paper saved!", "paper_id": _paper_id})



# ── Delete paper ──
@app.route("/admin/delete-paper/<int:paper_id>", methods=["POST"])
def delete_paper(paper_id):
    if session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 403
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM papers WHERE id=?", (paper_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})


# ── View paper (returns HTML for inline display) ──
@app.route("/admin/paper/<int:paper_id>")
def view_paper_admin(paper_id):
    if session.get("role") != "admin":
        return redirect("/admin/login")

    conn   = get_db()
    conn.row_factory = sqlite3.Row
    paper  = conn.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
    conn.close()

    if not paper:
        return "Paper not found", 404

    questions = []
    if paper["questions"]:
        try:
            questions = json.loads(paper["questions"])
        except Exception:
            questions = []

    labels = ['A','B','C','D']
    html = f"""<div style="font-family:'Outfit',sans-serif;color:#f0f7ff;padding:4px;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;flex-wrap:wrap;gap:12px;">
        <div>
          <div style="font-size:10px;font-weight:800;letter-spacing:2px;text-transform:uppercase;color:#60a5fa;margin-bottom:4px;">Question Paper</div>
          <h2 style="font-family:'Fraunces',serif;font-size:20px;font-weight:900;color:#f0f7ff;margin-bottom:4px;">{paper['title']}</h2>
          <div style="font-size:12px;color:rgba(148,179,220,0.5);">{paper['subject']} &nbsp;·&nbsp; {paper['duration']} min &nbsp;·&nbsp; {paper['total_marks']} marks &nbsp;·&nbsp; {len(questions)} questions</div>
        </div>
        <div style="display:flex;gap:8px;">
          <button onclick="publishPaper({paper_id})" style="background:rgba(16,185,129,0.15);border:1px solid rgba(16,185,129,0.3);color:#10b981;padding:8px 16px;border-radius:8px;font-family:'Outfit',sans-serif;font-size:12px;font-weight:700;cursor:pointer;">▶ Publish to Students</button>
          <button onclick="document.getElementById('paper-view-container').style.display='none'" style="background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.25);color:#ef4444;padding:8px 16px;border-radius:8px;font-family:'Outfit',sans-serif;font-size:12px;font-weight:700;cursor:pointer;">✕ Close</button>
        </div>
      </div>"""

    if not questions:
        html += '<div style="text-align:center;padding:32px;color:rgba(148,179,220,0.3);">No questions found in this paper.</div>'
    else:
        for i, q in enumerate(questions):
            q_text  = q.get('text') or q.get('question_text') or ''
            opts    = q.get('options') or [q.get('option_a',''), q.get('option_b',''), q.get('option_c',''), q.get('option_d','')]
            correct = (q.get('correct') or q.get('correct_answer') or '').strip().upper()
            opts_html = ""
            for oi, opt in enumerate(opts):
                lbl = labels[oi] if oi < len(labels) else str(oi)
                ok  = lbl == correct
                bg  = "rgba(16,185,129,0.08)" if ok else "rgba(255,255,255,0.02)"
                bc  = "rgba(16,185,129,0.25)" if ok else "rgba(255,255,255,0.06)"
                clr = "#10b981" if ok else "rgba(148,179,220,0.6)"
                fw  = "700" if ok else "500"
                tk  = " ✓ Correct" if ok else ""
                opts_html += f'<div style="padding:8px 12px;background:{bg};border:1px solid {bc};border-radius:6px;margin-bottom:5px;font-size:13px;color:{clr};font-weight:{fw};display:flex;gap:10px;"><span style="font-weight:800;min-width:22px;">{lbl}.</span><span>{opt}{tk}</span></div>'
            html += f"""<div style="background:rgba(8,20,50,0.55);border:1px solid rgba(59,130,246,0.12);border-radius:10px;padding:18px;margin-bottom:12px;">
              <div style="font-size:11px;font-weight:800;color:#60a5fa;margin-bottom:8px;">Q{i+1}</div>
              <div style="font-size:15px;color:#c8d8eb;line-height:1.6;margin-bottom:12px;">{q_text}</div>
              {opts_html}
            </div>"""

    html += "</div>"
    return html


# ── View paper full page ──
@app.route("/admin/view-paper/<int:paper_id>")
def admin_view_paper_detail(paper_id):
    if session.get("role") != "admin":
        return redirect("/admin/login")
    conn   = get_db()
    conn.row_factory = sqlite3.Row
    paper  = conn.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
    conn.close()
    if not paper:
        return "Paper not found", 404
    questions = json.loads(paper["questions"]) if paper["questions"] else []
    return render_template("admin/view_paper.html", paper=paper, questions=questions)


# ── Assign/Publish paper to students ──
@app.route("/admin/assign-paper/<int:paper_id>", methods=["POST"])
def assign_paper(paper_id):
    if session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 403

    data   = request.json or {}
    action = data.get("action", "publish")

    conn   = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE papers ADD COLUMN status TEXT DEFAULT 'draft'")
    except Exception:
        pass

    new_status = "live" if action == "publish" else "draft"
    cursor.execute("UPDATE papers SET status=? WHERE id=?", (new_status, paper_id))
    conn.commit()
    conn.close()
    return jsonify({"status": "success", "paper_status": new_status,
                    "message": f"Paper {'published to students' if new_status=='live' else 'unpublished'}"})


# ══════════════════════════════════════════════════════════════════
# ADMIN — EXAM SCHEDULING
# ══════════════════════════════════════════════════════════════════
@app.route("/admin/schedule-exam", methods=["POST"])
def schedule_exam():
    if session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 403

    data    = request.json or {}
    exam_id = data.get("exam_id")
    conn    = get_db()
    cursor  = conn.cursor()

    for col in ["ALTER TABLE papers ADD COLUMN start_time TEXT",
                "ALTER TABLE papers ADD COLUMN end_time TEXT",
                "ALTER TABLE papers ADD COLUMN status TEXT DEFAULT 'draft'",
                "ALTER TABLE papers ADD COLUMN max_attempts INTEGER DEFAULT 1"]:
        try: cursor.execute(col)
        except Exception: pass

    cursor.execute(
        "UPDATE papers SET start_time=?,end_time=?,status=?,max_attempts=? WHERE id=?",
        (data.get("start"), data.get("end"), data.get("status","draft"), data.get("attempts",1), exam_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})




# ── Reset face ──
@app.route("/admin/reset-face", methods=["POST"])
def admin_reset_face():
    if session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 403
    data  = request.json or {}
    email = data.get("email")
    if not email:
        return jsonify({"error": "Email required"}), 400
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET face_registered=0, face_encoding=NULL WHERE email=?", (email,))
    conn.commit()
    conn.close()
    try:
        safe_email  = email.replace("@","_").replace(".","_")
        dataset_dir = os.path.join(BASE_DIR, "dataset")
        for f in glob.glob(os.path.join(dataset_dir, f"{safe_email}*")):
            os.remove(f)
    except Exception:
        pass
    return jsonify({"status": "success", "message": f"Face reset for {email}"})


# ══════════════════════════════════════════════════════════════════
# ADMIN — TIMING SETTINGS
# ══════════════════════════════════════════════════════════════════
@app.route("/admin/save-timings", methods=["POST"])
def save_timings():
    if session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 403
    data   = request.json or {}
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    for key, value in data.items():
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, str(value)))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})


# ══════════════════════════════════════════════════════════════════
# ADMIN — QUESTION BANK
# ══════════════════════════════════════════════════════════════════
def _ensure_qbank_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS question_bank (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL, option_a TEXT, option_b TEXT,
            option_c TEXT, option_d TEXT, correct TEXT,
            subject TEXT DEFAULT 'General', difficulty TEXT DEFAULT 'easy',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)


@app.route("/admin/qbank", methods=["GET"])
def get_question_bank():
    if session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 403
    conn   = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    _ensure_qbank_table(cursor); conn.commit()
    rows = cursor.execute("SELECT * FROM question_bank ORDER BY id DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/admin/qbank/add", methods=["POST"])
def add_to_question_bank():
    if session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 403
    data   = request.json or {}
    conn   = get_db()
    cursor = conn.cursor()
    _ensure_qbank_table(cursor)
    cursor.execute("""
        INSERT INTO question_bank (text,option_a,option_b,option_c,option_d,correct,subject,difficulty)
        VALUES (?,?,?,?,?,?,?,?)
    """, (data.get("text"), data.get("a"), data.get("b"), data.get("c"), data.get("d"),
          data.get("correct"), data.get("subject","General"), data.get("difficulty","easy")))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})


@app.route("/admin/qbank/delete/<int:qid>", methods=["POST"])
def delete_from_question_bank(qid):
    if session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 403
    conn = get_db()
    conn.execute("DELETE FROM question_bank WHERE id=?", (qid,))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})


# ══════════════════════════════════════════════════════════════════
# ADMIN — EVIDENCE (FIXED)
# ══════════════════════════════════════════════════════════════════
@app.route("/evidence/<path:filename>")
def serve_evidence(filename):
    if session.get("role") != "admin":
        return "Unauthorized", 403
    base = os.path.join(os.path.dirname(BASE_DIR), "cheating_evidence")
    return send_from_directory(base, filename)


@app.route("/admin/evidence/<path:filename>")
def serve_admin_evidence(filename):
    if session.get("role") != "admin":
        return "Unauthorized", 403
    base = os.path.join(os.path.dirname(BASE_DIR), "cheating_evidence")
    return send_from_directory(base, filename)


@app.route("/admin/evidence-list")
def admin_evidence_list():
    """Return all evidence files from filesystem + DB violations."""
    if session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 403

    items = []

    # ── Scan filesystem ──
    evidence_base = os.path.join(os.path.dirname(BASE_DIR), "cheating_evidence")
    if os.path.exists(evidence_base):
        for email_folder in sorted(os.listdir(evidence_base)):
            folder_path = os.path.join(evidence_base, email_folder)
            if not os.path.isdir(folder_path):
                continue
            # Reconstruct email from folder name (user_example_com → user@example.com)
            # Safe: email folder = email.replace("@","_").replace(".","_")
            # We store as-is since exact email is in DB
            for fname in sorted(os.listdir(folder_path), reverse=True):
                if not fname.lower().endswith(('.jpg','.jpeg','.png')):
                    continue
                # Parse: reason_YYYYMMDD_HHMMSS.jpg
                name_part = fname.rsplit('.', 1)[0]
                parts     = name_part.rsplit('_', 2)
                reason    = parts[0].replace('_', ' ').title() if parts else 'Unknown'
                items.append({
                    "email":     email_folder,
                    "filename":  f"{email_folder}/{fname}",
                    "reason":    reason,
                    "timestamp": fname
                })

    # ── Also from DB (has proper email + reason) ──
    try:
        conn   = get_db()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT email, reason, evidence_image, timestamp
            FROM violations
            WHERE evidence_image IS NOT NULL AND evidence_image != ''
            ORDER BY timestamp DESC
        """)
        for row in cursor.fetchall():
            items.append({
                "email":     row["email"],
                "filename":  row["evidence_image"],
                "reason":    row["reason"],
                "timestamp": row["timestamp"]
            })
        conn.close()
    except Exception as e:
        print(f"Evidence DB error: {e}")

    # Deduplicate by filename
    seen = set()
    unique = []
    for item in items:
        k = item.get("filename","")
        if k not in seen:
            seen.add(k)
            unique.append(item)

    return jsonify(unique[:300])




# ══════════════════════════════════════════════════════════════════
# ADMIN — EXPORT CSV
# ══════════════════════════════════════════════════════════════════
@app.route("/admin/export/violations")
def export_violations_csv():
    if session.get("role") != "admin":
        return redirect("/admin/login")
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id,email,reason,timestamp FROM violations ORDER BY timestamp DESC")
    rows = cursor.fetchall()
    conn.close()
    out = io.StringIO()
    csv.writer(out).writerows([["ID","Email","Violation","Timestamp"]] + list(rows))
    resp = make_response(out.getvalue())
    resp.headers["Content-Disposition"] = "attachment; filename=violations.csv"
    resp.headers["Content-Type"]        = "text/csv"
    return resp


@app.route("/admin/export/students")
def export_students_csv():
    if session.get("role") != "admin":
        return redirect("/admin/login")
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT full_name,email,face_registered FROM users WHERE role='student'")
    students = cursor.fetchall()
    cursor.execute("SELECT email,COUNT(*) FROM violations GROUP BY email")
    viol_map = dict(cursor.fetchall())
    conn.close()
    out = io.StringIO()
    w   = csv.writer(out)
    w.writerow(["Full Name","Email","Face Registered","Violations","Risk"])
    for s in students:
        vc   = viol_map.get(s[1],0)
        sc   = max(0,100-vc*10)
        risk = "Clean" if sc>70 else ("Moderate" if sc>40 else "High Risk")
        w.writerow([s[0],s[1],"Yes" if s[2] else "No",vc,risk])
    resp = make_response(out.getvalue())
    resp.headers["Content-Disposition"] = "attachment; filename=students.csv"
    resp.headers["Content-Type"]        = "text/csv"
    return resp


@app.route("/admin/export/login")
def export_login_csv():
    if session.get("role") != "admin":
        return redirect("/admin/login")
    conn   = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT u.email,l.status,l.timestamp FROM login_logs l
            JOIN users u ON l.user_id=u.id ORDER BY l.id DESC
        """)
        rows = cursor.fetchall()
    except Exception:
        rows = []
    conn.close()
    out = io.StringIO()
    csv.writer(out).writerows([["Email","Status","Timestamp"]] + list(rows))
    resp = make_response(out.getvalue())
    resp.headers["Content-Disposition"] = "attachment; filename=login_activity.csv"
    resp.headers["Content-Type"]        = "text/csv"
    return resp


# ══════════════════════════════════════════════════════════════════
# ADMIN — CSV TEMPLATE DOWNLOAD
# ══════════════════════════════════════════════════════════════════
@app.route("/static/sample_template.csv")
def download_csv_template():
    content = (
        "question,option_a,option_b,option_c,option_d,answer\n"
        "What is 2 + 2?,2,3,4,5,C\n"
        "What is the capital of France?,London,Paris,Berlin,Rome,B\n"
        "Which language runs in the browser?,Python,Java,JavaScript,C++,C\n"
        "What does AI stand for?,Artificial Intelligence,Automated Input,Advanced Interface,Algorithm Index,A\n"
        "What is the full form of CPU?,Central Processing Unit,Computer Power Unit,Core Processing Unit,Control Process Unit,A\n"
    )
    resp = make_response(content)
    resp.headers["Content-Disposition"] = "attachment; filename=sample_template.csv"
    resp.headers["Content-Type"]        = "text/csv; charset=utf-8"
    return resp


# ══════════════════════════════════════════════════════════════════
# ADMIN — AI VIOLATION SUMMARY + CHEAT PATTERNS + DASHBOARD DATA
# ══════════════════════════════════════════════════════════════════
def _rule_based_summary(name, vc, score, top_violations):
    if vc == 0:
        return f"{name} maintained perfect conduct. No violations detected. Recommendation: PASS."
    top = top_violations[0][0] if top_violations else "Unknown"
    if score < 30:
        return f"{name} exhibited critically dishonest behaviour ({vc} violations). Top: '{top}'. Recommendation: FAIL."
    elif score < 60:
        return f"{name} flagged {vc} times. Primary: '{top}'. Recommendation: REVIEW evidence."
    else:
        return f"{name} had {vc} minor violation(s), primarily '{top}'. Recommendation: PASS with note."


@app.route("/admin/ai-summary/<email>")
def ai_violation_summary(email):
    if session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 403
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT full_name FROM users WHERE email=?", (email,))
    user = cursor.fetchone()
    cursor.execute("SELECT reason,timestamp FROM violations WHERE email=? ORDER BY timestamp DESC", (email,))
    violations = cursor.fetchall()
    conn.close()
    name = user[0] if user else email
    vc   = len(violations)
    tc   = {}
    for v in violations:
        r=v[0] or "Unknown"; tc[r]=tc.get(r,0)+1
    top  = sorted(tc.items(), key=lambda x:x[1], reverse=True)
    score= max(0,100-vc*10)
    risk = "LOW" if score>70 else ("MODERATE" if score>40 else "HIGH")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key and vc>0:
        try:
            import requests as req
            r = req.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key":api_key,"anthropic-version":"2023-06-01","Content-Type":"application/json"},
                json={"model":"claude-haiku-4-5-20251001","max_tokens":300,
                      "messages":[{"role":"user","content":
                          f"Exam proctoring AI. Student: {name} ({email}). Violations: {vc}. Score: {score}%. "
                          f"Top: {', '.join([f'{t}:{c}x' for t,c in top[:3]])}. "
                          f"Write 3-sentence professional assessment under 80 words with Recommendation (Pass/Review/Fail)."}]},
                timeout=10)
            ai_text = r.json()["content"][0]["text"]
        except Exception:
            ai_text = _rule_based_summary(name,vc,score,top)
    else:
        ai_text = _rule_based_summary(name,vc,score,top)
    return jsonify({"name":name,"email":email,"violations":vc,"score":score,"risk":risk,"top_violations":top,"summary":ai_text})


@app.route("/admin/cheat-patterns")
def cheat_patterns():
    if session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 403
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT u.email,u.full_name,COUNT(v.id) as total,GROUP_CONCAT(v.reason,'|') as reasons
        FROM users u LEFT JOIN violations v ON u.email=v.email
        WHERE u.role='student' GROUP BY u.email HAVING total>0 ORDER BY total DESC
    """)
    patterns = []
    for row in cursor.fetchall():
        tc={}
        for r in (row[3] or "").split("|"):
            if r: tc[r]=tc.get(r,0)+1
        score=max(0,100-row[2]*10)
        patterns.append({"email":row[0],"name":row[1],"total":row[2],"score":score,
            "risk":"HIGH" if score<40 else("MODERATE" if score<70 else "LOW"),
            "type_counts":tc,"is_repeat":row[2]>=5,
            "top_violation":max(tc,key=tc.get) if tc else None})
    conn.close()
    return jsonify(patterns)


@app.route("/admin/dashboard-data")
def admin_dashboard_data():
    if session.get("role") != "admin":
        return jsonify({"error": "unauthorized"}), 403
    conn   = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT u.email,l.status,l.timestamp FROM login_logs l
            JOIN users u ON l.user_id=u.id ORDER BY l.id DESC LIMIT 20
        """)
        login_logs = [list(row) for row in cursor.fetchall()]
    except Exception:
        login_logs = []
    conn.close()
    return jsonify({"login_logs": login_logs})




# ══════════════════════════════════════════════════════════════════
# ADMIN OTP — Two-Factor Authentication Routes
# ══════════════════════════════════════════════════════════════════

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    """Admin login — step 1 (password). On success, sends OTP and redirects to OTP page."""
    if session.get("role") == "admin":
        return redirect("/admin/dashboard")

    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        conn   = get_db()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE email=? AND role='admin'", (email,))
        user = cursor.fetchone()
        conn.close()

        if user:
            from werkzeug.security import check_password_hash
            if check_password_hash(user["password"], password):
                # ✅ Credentials correct — send OTP
                result = send_otp_email(email)
                session["otp_pending_email"] = email
                session["otp_pending_name"]  = user["full_name"]
                dev_mode = result.get("dev_mode", False)
                return render_template("admin/otp_verify.html",
                                       admin_email=email,
                                       dev_mode=dev_mode)

        flash("Invalid email or password.", "error")
        return redirect("/admin/login")

    return render_template("admin/admin_login.html")


@app.route("/admin/verify-otp", methods=["POST"])
def admin_verify_otp():
    """Admin login — step 2 (OTP verification)."""
    pending_email = session.get("otp_pending_email")
    if not pending_email:
        flash("Session expired. Please log in again.", "error")
        return redirect("/admin/login")

    submitted_otp = request.form.get("otp", "").strip()
    result = verify_otp(pending_email, submitted_otp)

    if result["valid"]:
        # ✅ OTP correct — complete login
        session.pop("otp_pending_email", None)
        session["email"]     = pending_email
        session["role"]      = "admin"
        session["full_name"] = session.pop("otp_pending_name", "Admin")
        session.permanent    = True
        # Log the login
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM users WHERE email=?", (pending_email,))
            u = cursor.fetchone()
            if u:
                cursor.execute(
                    "INSERT INTO login_logs (user_id, status, timestamp) VALUES (?,?,datetime('now','localtime'))",
                    (u[0], "admin_otp_success")
                )
                conn.commit()
            conn.close()
        except Exception:
            pass
        return redirect("/admin/dashboard")
    else:
        # ❌ Wrong OTP
        conn   = get_db()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE email=?", (pending_email,))
        user = cursor.fetchone()
        conn.close()
        flash(result["message"], "error")
        return render_template("admin/otp_verify.html",
                               admin_email=pending_email,
                               dev_mode=(not os.environ.get("ADMIN_EMAIL_SENDER")))


@app.route("/admin/resend-otp", methods=["POST"])
def admin_resend_otp():
    """Resend OTP to admin email."""
    pending_email = session.get("otp_pending_email")
    if not pending_email:
        return jsonify({"success": False, "message": "Session expired. Please log in again."})
    result = send_otp_email(pending_email)
    return jsonify(result)


# ══════════════════════════════════════════════════════════════════
# EXAM NOTIFICATIONS — email admin when student starts/submits
# ══════════════════════════════════════════════════════════════════

def _notify_admin_exam_event(student_name: str, student_email: str, event: str, exam_title: str = ""):
    """Fire-and-forget: email all admins about exam events."""
    try:
        conn   = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT email FROM users WHERE role='admin'")
        admins = [r[0] for r in cursor.fetchall()]
        conn.close()
    except Exception:
        admins = []

    color   = "#10b981" if event == "started" else "#60a5fa"
    icon    = "▶" if event == "started" else "✅"
    subject = f"SecureExam AI — Student {event.title()}: {student_name}"
    body    = f"""
    <div style="background:#040d1a;font-family:system-ui;padding:32px;">
    <div style="max-width:440px;margin:0 auto;background:rgba(8,20,50,0.95);border:1px solid rgba(59,130,246,0.2);border-radius:16px;overflow:hidden;">
      <div style="background:{color};padding:20px 24px;">
        <div style="font-size:14px;font-weight:800;color:white;">{icon} Student Exam {event.title()}</div>
      </div>
      <div style="padding:24px;">
        <p style="color:#c8d8eb;font-size:14px;margin-bottom:12px;"><b>{student_name}</b> ({student_email}) has {event} an exam.</p>
        {'<p style="color:rgba(148,179,220,0.5);font-size:13px;">Exam: ' + exam_title + '</p>' if exam_title else ''}
        <p style="color:rgba(148,179,220,0.5);font-size:12px;margin-top:16px;">Time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
        <a href="/admin/dashboard" style="display:inline-block;margin-top:16px;padding:10px 20px;background:#2563eb;color:white;border-radius:8px;font-size:13px;font-weight:700;text-decoration:none;">View Admin Dashboard →</a>
      </div>
      <div style="padding:14px 24px;border-top:1px solid rgba(255,255,255,0.06);font-size:11px;color:rgba(148,179,220,0.2);">© 2026 SecureExam AI</div>
    </div></div>"""

    for admin_email in admins:
        try:
            _send_notification_email(admin_email, subject, body)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════
# BULK STUDENT IMPORT
# ══════════════════════════════════════════════════════════════════

@app.route("/admin/import-students", methods=["POST"])
def import_students():
    """Bulk import students from CSV file (email, full_name columns)."""
    if session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 403
    if "file" not in request.files:
        return jsonify({"status": "error", "message": "No file uploaded"}), 400

    file = request.files["file"]
    try:
        raw = file.stream.read()
        try:    content_str = raw.decode("utf-8-sig")
        except: content_str = raw.decode("latin-1")

        import csv, io
        from werkzeug.security import generate_password_hash
        reader  = csv.DictReader(io.StringIO(content_str, newline=None))
        fields  = {f.strip().lower(): f for f in (reader.fieldnames or [])}
        col_email = fields.get("email") or fields.get("e-mail")
        col_name  = fields.get("full_name") or fields.get("name") or fields.get("student_name")

        if not col_email:
            return jsonify({"status": "error", "message": "CSV must have an 'email' column"}), 400

        conn   = get_db()
        cursor = conn.cursor()
        added = 0; skipped = 0

        for row in reader:
            email = (row.get(col_email) or "").strip().lower()
            name  = (row.get(col_name) or email.split("@")[0]).strip()
            if not email or "@" not in email:
                skipped += 1; continue
            cursor.execute("SELECT id FROM users WHERE email=?", (email,))
            if cursor.fetchone():
                skipped += 1; continue
            default_pw = generate_password_hash("SecureExam@2026")
            cursor.execute(
                "INSERT INTO users (full_name,email,password,role,face_registered) VALUES (?,?,?,?,?)",
                (name, email, default_pw, "student", 0)
            )
            added += 1

        conn.commit(); conn.close()
        return jsonify({
            "status":  "success",
            "message": f"✅ Imported {added} student(s). {skipped} skipped (already exist or invalid).",
            "added":   added, "skipped": skipped
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/admin/student-import-template")
def student_import_template():
    """Download CSV template for student bulk import."""
    if session.get("role") != "admin":
        return redirect("/admin/login")
    content_str = "full_name,email\nJohn Smith,john.smith@university.edu\nJane Doe,jane.doe@university.edu\n"
    resp = make_response(content_str)
    resp.headers["Content-Disposition"] = "attachment; filename=student_import_template.csv"
    resp.headers["Content-Type"]        = "text/csv"
    return resp


# ══════════════════════════════════════════════════════════════════
# EXAM ANALYTICS
# ══════════════════════════════════════════════════════════════════

@app.route("/admin/analytics-data")
def admin_analytics_data():
    """Return score distribution data for analytics chart."""
    if session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 403

    conn   = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT score, total, email, submitted_at FROM exam_results ORDER BY submitted_at DESC")
        results = cursor.fetchall()
    except Exception:
        results = []

    # Score distribution buckets
    buckets = {"0-20": 0, "21-40": 0, "41-60": 0, "61-80": 0, "81-100": 0}
    per_student = {}

    for r in results:
        pct = int((r["score"] / r["total"]) * 100) if r["total"] else 0
        if pct <= 20:    buckets["0-20"]   += 1
        elif pct <= 40:  buckets["21-40"]  += 1
        elif pct <= 60:  buckets["41-60"]  += 1
        elif pct <= 80:  buckets["61-80"]  += 1
        else:            buckets["81-100"] += 1

        email = r["email"]
        if email not in per_student:
            per_student[email] = {"email": email, "scores": [], "avg": 0}
        per_student[email]["scores"].append(pct)

    for s in per_student.values():
        s["avg"] = round(sum(s["scores"]) / len(s["scores"]), 1)

    conn.close()
    return jsonify({
        "distribution": buckets,
        "per_student":  sorted(per_student.values(), key=lambda x: x["avg"], reverse=True)[:20],
        "total_exams":  len(results),
        "avg_score":    round(sum((r["score"]/r["total"]*100 if r["total"] else 0) for r in results) / max(len(results),1), 1)
    })


# ══════════════════════════════════════════════════════════════════
# RAISE HAND (student → admin notification)
# ══════════════════════════════════════════════════════════════════

_raise_hand_queue = []   # list of {email, name, timestamp, message}

@app.route("/raise-hand", methods=["POST"])
def raise_hand():
    """Student can raise hand during exam — notifies admin without logging violation."""
    if "email" not in session:
        return jsonify({"status": "error"}), 403
    data = request.json or {}
    entry = {
        "email":     session["email"],
        "name":      session.get("full_name", session["email"]),
        "message":   data.get("message", "Student needs help"),
        "timestamp": datetime.now().strftime("%H:%M:%S")
    }
    _raise_hand_queue.append(entry)
    # Keep only last 50
    if len(_raise_hand_queue) > 50:
        _raise_hand_queue.pop(0)
    return jsonify({"status": "ok", "message": "Hand raised — admin notified"})


@app.route("/admin/raise-hand-queue")
def admin_raise_hand_queue():
    """Admin polls this to see who needs help."""
    if session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 403
    return jsonify(_raise_hand_queue[-20:])  # last 20


@app.route("/admin/clear-raise-hand", methods=["POST"])
def clear_raise_hand():
    if session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 403
    _raise_hand_queue.clear()
    return jsonify({"status": "cleared"})


@app.route("/admin/add-faculty", methods=["POST"])
def add_faculty():
    if session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 403
    from werkzeug.security import generate_password_hash
    data  = request.json or {}
    name  = data.get("name","").strip()
    email = data.get("email","").strip().lower()
    pw    = data.get("password","").strip()
    dept  = data.get("dept","Admin")
    if not name or not email or not pw:
        return jsonify({"status":"error","message":"All fields required"}), 400
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE email=?", (email,))
    if cursor.fetchone():
        conn.close()
        return jsonify({"status":"error","message":"Email already registered"})
    cursor.execute(
        "INSERT INTO users (full_name,email,password,role,face_registered) VALUES (?,?,?,?,?)",
        (name, email, generate_password_hash(pw), "admin", 0)
    )
    conn.commit()
    conn.close()
    return jsonify({"status":"success","message":f"Faculty account created for {name}"})

# ══════════════════════════════════════════════════════════════════
# DEBUG
# ══════════════════════════════════════════════════════════════════
@app.route("/debug-violations")
def debug_violations():
    if "email" not in session:
        return "Not logged in", 403
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM violations WHERE email=?", (session["email"],))
    violations = cursor.fetchall()
    cursor.execute("PRAGMA table_info(violations)")
    columns = cursor.fetchall()
    conn.close()
    return (f"<h2>Debug</h2><p><b>Email:</b> {session['email']}</p>"
            f"<p><b>Columns:</b> {columns}</p><p><b>Count:</b> {len(violations)}</p>"
            f"<p><b>Data:</b> {violations}</p>")


@app.route("/test-register-face")
def test_register_face():
    session["temp_user_email"] = "test@test.com"
    return render_template("register_face.html")

@app.route("/admin/api/distribution-stats")
def admin_distribution_stats():
    """Returns student/teacher counts by year for the dashboard."""
    if session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 403

    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Get Student Counts (1st, 2nd, 3rd year)
    cursor.execute("SELECT year_level, COUNT(*) as count FROM users WHERE role='student' GROUP BY year_level")
    student_stats = {f"year_{row['year_level']}": row['count'] for row in cursor.fetchall()}

    # Get Admin Counts (1st, 2nd, 3rd year)
    cursor.execute("SELECT year_level, COUNT(*) as count FROM users WHERE role='admin' GROUP BY year_level")
    teacher_stats = {f"year_{row['year_level']}": row['count'] for row in cursor.fetchall()}

    conn.close()
    return jsonify({
        "students": student_stats,
        "teachers": teacher_stats
    })





# ══════════════════════════════════════════════════════════════════
# LIVE VIDEO STREAMING — COMPLETE app.py ROUTES
# DELETE these existing routes first:
#   stream_frame, student_snapshot, admin_snapshots,
#   admin_live_status, check_termination, terminate_student
# Then paste ALL of this below your existing routes,
# above:  if __name__ == "__main__":
# ══════════════════════════════════════════════════════════════════

# ── In-memory stores ──────────────────────────────────────────────
_snap_store   = {}          # { email: {image, timestamp, violations, face, risk, name} }
_snap_lock    = threading.Lock()
_chunk_queues = {}          # { email: Queue } — video chunks from MediaRecorder
_chunk_lock   = threading.Lock()
_chunk_meta   = {}          # { email: {name, violations, face, risk, timestamp} }
_frame_counts = {}          # throttle DB hits


def _get_chunk_queue(email):
    with _chunk_lock:
        if email not in _chunk_queues:
            _chunk_queues[email] = _queue.Queue(maxsize=120)
        return _chunk_queues[email]


# ──────────────────────────────────────────────────────────────────
# STUDENT → SERVER: receive MediaRecorder video chunks
# Called by exam.html every ~200ms with raw WebM binary
# ──────────────────────────────────────────────────────────────────
@app.route("/video-chunk", methods=["POST"])
def video_chunk():
    if "email" not in session:
        return "", 403

    email = session["email"]
    chunk = request.data
    if not chunk:
        return "", 204

    # DB query every 20 chunks (~4 seconds) to reduce load
    _frame_counts[email] = _frame_counts.get(email, 0) + 1
    count = _frame_counts[email]

    if count % 20 == 1:
        try:
            conn   = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM violations WHERE email=?", (email,))
            vc = cursor.fetchone()[0]
            cursor.execute("SELECT face_registered FROM users WHERE email=?", (email,))
            row      = cursor.fetchone()
            face_reg = bool(row[0]) if row else False
            conn.close()
        except Exception:
            vc = 0; face_reg = False
    else:
        with _chunk_lock:
            prev     = _chunk_meta.get(email, {})
            vc       = prev.get("violations", 0)
            face_reg = prev.get("face", False)

    risk = "high" if vc >= 7 else "moderate" if vc >= 3 else "clean"

    with _chunk_lock:
        _chunk_meta[email] = {
            "name":       session.get("full_name", email),
            "violations": vc,
            "face":       face_reg,
            "risk":       risk,
            "timestamp":  _time.strftime("%H:%M:%S"),
            "terminated": email in terminated_students,
        }

    q = _get_chunk_queue(email)
    # Drop oldest chunk if queue full (keep stream fresh)
    if q.full():
        try:
            q.get_nowait()
        except Exception:
            pass
    try:
        q.put_nowait(chunk)
    except Exception:
        pass

    return "", 204


# ──────────────────────────────────────────────────────────────────
# ADMIN → SERVER: get one video chunk for a student
# Admin dashboard polls this every 200ms per student
# ──────────────────────────────────────────────────────────────────
@app.route("/admin/video-chunk/<student_email>")
def admin_video_chunk(student_email):
    if session.get("role") != "admin":
        return "", 403

    q = _chunk_queues.get(student_email)
    if not q:
        return "", 204

    try:
        chunk = q.get_nowait()
        return app.response_class(
            chunk,
            mimetype="application/octet-stream",
            headers={
                "Cache-Control":       "no-cache, no-store",
                "X-Accel-Buffering":   "no",
                "Access-Control-Allow-Origin": "*",
            }
        )
    except Exception:
        return "", 204


# ──────────────────────────────────────────────────────────────────
# STUDENT → SERVER: fallback snapshot (base64 JPEG)
# Still used for the thumbnail + violation badge overlay
# ──────────────────────────────────────────────────────────────────
_store = {}          # { email: {image, ts, vc, face, risk, name} }
_store_lock = _threading.Lock()
_terminated = set()  # use existing terminated_students

@app.route("/stream-frame", methods=["POST"])
def stream_frame():
    if "email" not in session:
        return "", 403
    data  = request.json or {}
    image = data.get("image", "")
    if not image:
        return "", 204
    email = session["email"]
    try:
        conn = get_db()
        cur  = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM violations WHERE email=?", (email,))
        vc = cur.fetchone()[0]
        cur.execute("SELECT face_registered FROM users WHERE email=?", (email,))
        r  = cur.fetchone()
        face = bool(r[0]) if r else False
        conn.close()
    except Exception:
        vc = 0; face = False
    risk = "high" if vc>=7 else "moderate" if vc>=3 else "clean"
    with _store_lock:
        _store[email] = {
            "image": image,
            "ts":    _time.strftime("%H:%M:%S"),
            "vc":    vc,
            "face":  face,
            "risk":  risk,
            "name":  session.get("full_name", email),
            "term":  email in terminated_students,
        }
    return "", 204

@app.route("/student-snapshot", methods=["POST"])
def student_snapshot():
    return stream_frame()

@app.route("/admin/snapshots")
def admin_snapshots():
    if session.get("role") != "admin":
        return jsonify({"error":"unauthorized"}), 403
    with _store_lock:
        return jsonify(dict(_store))

@app.route("/admin/live-status")
def admin_live_status():
    if session.get("role") != "admin":
        return jsonify({"error":"unauthorized"}), 403
    try:
        conn = get_db()
        cur  = conn.cursor()
        cur.execute("""
            SELECT u.email, u.full_name, u.face_registered, COUNT(v.id) as vc
            FROM users u LEFT JOIN violations v ON u.email=v.email
            WHERE u.role='student' GROUP BY u.email ORDER BY vc DESC
        """)
        rows = cur.fetchall()
        conn.close()
    except Exception:
        rows = []
    out = []
    with _store_lock:
        for row in rows:
            email = row[0]; vc = row[3]
            snap  = _store.get(email)
            score = max(0, 100-vc*10)
            risk  = "high" if score<40 else "moderate" if score<70 else "clean"
            out.append({
                "email":      email,
                "name":       row[1],
                "face":       bool(row[2]),
                "violations": vc,
                "risk":       risk,
                "terminated": email in terminated_students,
                "online":     snap is not None,
                "snap_image": snap["image"] if snap else None,
                "snap_ts":    snap["ts"]    if snap else "",
            })
    return jsonify({
        "students":  out,
        "total":     len(out),
        "clean":     sum(1 for s in out if s["risk"]=="clean"),
        "moderate":  sum(1 for s in out if s["risk"]=="moderate"),
        "high_risk": sum(1 for s in out if s["risk"]=="high"),
    })

@app.route("/check-termination")
def check_termination():
    email = session.get("email")
    if not email:
        return jsonify({"terminated": False})
    if email in terminated_students:
        terminated_students.discard(email)
        return jsonify({"terminated": True})
    return jsonify({"terminated": False})

@app.route("/admin/terminate-student", methods=["POST"])
def terminate_student():
    if session.get("role") != "admin":
        return jsonify({"error":"Unauthorized"}), 403
    email = (request.json or {}).get("email")
    if not email:
        return jsonify({"error":"Email required"}), 400
    terminated_students.add(email)
    with _store_lock:
        if email in _store:
            _store[email]["term"] = True
    try:
        conn = get_db()
        conn.execute("INSERT INTO violations (email,reason) VALUES (?,?)",
                     (email, "Admin Terminated Exam"))
        conn.commit(); conn.close()
    except Exception:
        pass
    return jsonify({"status":"terminated","email":email})

# ══════════════════════════════════════════════════════════════════
# RUN
# ══════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    run_migrations()   # Creates new tables if they don't exist
    #app.run(debug=True, port=5000)


    # Adding debug=True enables the auto-reloader
    app.run(debug=True, port=5000)