"""
src/exam/exam_instructions.py
──────────────────────────────
REPLACE your existing exam_instructions.py with this.

Changes:
- Passes `papers` list to instructions.html so exam selector dropdown works
- Passes `selected_exam_id` from URL param
- Still enforces login + face verification
"""
from flask import Blueprint, render_template, session, redirect, url_for, request
from database.db_utils import get_db

instructions_bp = Blueprint(
    "instructions",
    __name__,
    template_folder="templates"
)


@instructions_bp.route("/instructions")
def instructions():
    # 1. Check Login
    if "email" not in session:
        return redirect("/login")

    # 2. Check Face Verification
    if not session.get("face_verified"):
        return redirect("/face/verify_face")

    # 3. Get exam_id from URL if passed
    exam_id = request.args.get("exam_id")

    # 4. Load all available papers for the selector
    conn   = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT id, title, subject, total_marks, duration
            FROM papers
            ORDER BY id DESC
        """)
        papers = cursor.fetchall()
    except Exception:
        papers = [] 
    conn.close()

    return render_template(
        "instructions.html",
        papers=papers,
        selected_exam_id=exam_id
    )
