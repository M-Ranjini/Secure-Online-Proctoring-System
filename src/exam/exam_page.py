import cv2
import base64
import numpy as np
import os
import face_recognition
from flask import Blueprint, render_template, session, redirect, request, jsonify, Response, url_for
from flask import Blueprint, render_template, request, session, redirect, url_for, flash
from database.db_utils import get_db


exam_bp = Blueprint("exam", __name__)

# --- CONFIGURATION ---
MAX_VIOLATIONS = 3
DATASET_DIR = "dataset/faces"

# --- CORE PROCTORING LOGIC ---
def proctor_logic(frame, user_email):
    """
    This function runs the 'Watchdog' logic:
    1. Detects if a face is present.
    2. Detects if MULTIPLE faces are present (Impersonation/Collaboration).
    3. Verifies if the face matches the registered student.
    """
    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    face_locations = face_recognition.face_locations(rgb_frame)
    
    # 🚩 VIOLATION: No face or Multiple faces
    if len(face_locations) == 0:
        return "No face detected"
    if len(face_locations) > 1:
        return "Multiple people detected"

    # 🚩 VIOLATION: Impersonation Check (Periodic)
    # To save CPU, we only check identity every few seconds or frames
    # (Implementation below assumes saved .npy exists)
    npy_path = os.path.join(DATASET_DIR, f"{user_email}.npy")
    if os.path.exists(npy_path):
        saved_encoding = np.load(npy_path)
        live_encoding = face_recognition.face_encodings(rgb_frame, face_locations)[0]
        match = face_recognition.compare_faces([saved_encoding], live_encoding, tolerance=0.6)
        if not match[0]:
            return "Unauthorized person detected"
            
    return None

# --- VIDEO STREAMING ---
def generate_frames(user_email):
    camera = cv2.VideoCapture(0)
    while True:
        success, frame = camera.read()
        if not success:
            break
        else:
            # You can call proctor_logic here for real-time background analysis
            ret, buffer = cv2.imencode('.jpg', frame)
            frame_bytes = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

# --- ROUTES ---

@exam_bp.route("/exam/<int:exam_id>")
def exam_session(exam_id):
    # 🛡️ SECURITY CHECK: Must be logged in and face-verified
    if "email" not in session:
        return redirect(url_for("auth.login"))
    
    if not session.get("face_verified"):
        flash("Please verify your identity before starting the exam.", "error")
        return redirect(url_for("face.verify_face"))

    # Reset violations for a fresh exam attempt
    session["violations"] = 0
    
    conn = get_db()
    cursor = conn.cursor()

    # 1. Fetch Exam Metadata (Title, Duration)
    cursor.execute("SELECT * FROM exams WHERE id = ?", (exam_id,))
    exam_row = cursor.fetchone()

    if not exam_row:
        conn.close()
        flash("Exam not found.", "error")
        return redirect(url_for("dashboard"))

    # 2. Fetch Questions linked to this Exam
    # Ensure your table has a column 'exam_id' to link the questions
    cursor.execute("SELECT * FROM questions WHERE exam_id = ?", (exam_id,))
    questions_rows = cursor.fetchall()
    
    # Convert sqlite3.Row objects to list of dictionaries for JSON compatibility
    questions = [dict(row) for row in questions_rows]
    exam = dict(exam_row)

    conn.close()

    # Debug to console: check if questions are being retrieved
    print(f"DEBUG: Loaded {len(questions)} questions for Exam: {exam['title']}")

    return render_template(
        "exam.html", 
        exam=exam, 
        questions=questions, 
        project_name="SecureExam AI"
    )

@exam_bp.route("/video_feed")
def video_feed():
    if "email" not in session:
        return "Unauthorized", 401
    return Response(generate_frames(session["email"]), mimetype='multipart/x-mixed-replace; boundary=frame')

@exam_bp.route("/log-violation", methods=["POST"])
def log_violation():
    if "email" not in session:
        return jsonify({"status": "error"}), 403

    data = request.json
    reason = data.get("reason", "Unknown Violation")
    email = session["email"]

    # Increment session violation count
    session["violations"] = session.get("violations", 0) + 1
    current_count = session["violations"]

    # 💾 Save to Database (Ensuring we use 'users' context)
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO violations (email, reason) VALUES (?, ?)",
            (email, reason)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"DB Error logging violation: {e}")

    # 🛑 TERMINATION LOGIC
    if current_count >= 3:
        return jsonify({
            "status": "terminate", 
            "count": current_count, # 👈 ADD THIS LINE
            "message": "Maximum violations reached. The exam has been terminated."
        })

    return jsonify({
        "status": "ok", 
        "count": current_count,
        "message": f"Violation detected: {reason}"
    })

@exam_bp.route("/exam-done")
def exam_done():
    # Clean up session but keep the user logged in
    session.pop("violations", None)
    return render_template("exam_done.html")