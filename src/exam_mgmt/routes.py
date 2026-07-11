# src/exam_mgmt/routes.py — COMPLETE REPLACEMENT
# Fixes:
#  1. admin_home — safe column selection (no hard-coded 'questions')
#  2. take_exam  — loads questions from papers.questions JSON (not questions table)
#  3. save_paper — uses correct question format
import cv2
import sqlite3
import csv
import io
import json
from flask import (
    Blueprint, render_template, request,
    redirect, url_for, flash, jsonify, session
)
from database.db_utils import get_db

exam_mgmt_bp = Blueprint('exam_mgmt', __name__)


# ══════════════════════════════════════════════════════════════════
# ADMIN HOME
# ══════════════════════════════════════════════════════════════════
@exam_mgmt_bp.route('/admin')
def admin_home():
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cur  = conn.cursor()

    def to_dict(rows):
        return [dict(r) for r in rows]

    students = to_dict(cur.execute(
        "SELECT id, full_name, email, role, face_registered FROM users WHERE role='student'"
    ).fetchall())

    violations = to_dict(cur.execute(
        "SELECT id, email, reason, timestamp FROM violations ORDER BY timestamp DESC"
    ).fetchall())

    recent_violations = to_dict(cur.execute(
        "SELECT id, email, reason, timestamp FROM violations ORDER BY id DESC LIMIT 5"
    ).fetchall())

    exams = to_dict(cur.execute("SELECT * FROM exams").fetchall())

    # ── Safe paper select: never request 'questions' column directly ──
    # We get id,title,subject,total_marks,duration — that's all the table needs
    try:
        papers = to_dict(cur.execute(
            "SELECT id, title, subject, total_marks, duration FROM papers ORDER BY id DESC"
        ).fetchall())
    except Exception:
        papers = []

    try:
        logs = to_dict(cur.execute(
            "SELECT u.email, l.status, l.timestamp "
            "FROM login_logs l JOIN users u ON l.user_id=u.id "
            "ORDER BY l.id DESC LIMIT 20"
        ).fetchall())
    except Exception:
        logs = []

    conn.close()

    sv = {}
    for v in violations:
        e = v['email']
        sv[e] = sv.get(e, 0) + 1

    return render_template(
        'admin/admin_dashboard.html',
        students_json          = json.dumps(students),
        violations_json        = json.dumps(violations),
        recent_violations_json = json.dumps(recent_violations),
        login_logs_json        = json.dumps(logs),
        exams_json             = json.dumps(exams),
        papers_json            = json.dumps(papers),
        student_violations_json= json.dumps(sv),
        total_students         = len(students),
        total_face_registered  = sum(1 for s in students if s.get('face_registered')),
        total_exams            = len(exams),
        total_violations       = len(violations)
    )

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

# ══════════════════════════════════════════════════════════════════
# CREATE EXAM
# ══════════════════════════════════════════════════════════════════
@exam_mgmt_bp.route('/admin/create-exam', methods=['GET', 'POST'])
def create_exam():
    if request.method == 'POST':
        title    = request.form.get('title', '').strip()
        code     = request.form.get('exam_code', '').strip()
        duration = request.form.get('duration', 60)
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO exams (title, exam_code, duration) VALUES (?,?,?)",
                (title, code, int(duration))
            )
            conn.commit()
            flash("Exam created!", "success")
        except Exception as e:
            flash(f"Error: {e}", "error")
        finally:
            conn.close()
        return redirect(url_for('exam_mgmt.admin_home'))
    return render_template('admin/create_exam.html')



# ══════════════════════════════════════════════════════════════════
# STUDENT LOGIN
# ══════════════════════════════════════════════════════════════════
@exam_mgmt_bp.route('/student/login', methods=['GET', 'POST'])
def student_login():
    if request.method == 'POST':
        exam_code = request.form.get('exam_code', '').strip()
        conn = get_db()
        conn.row_factory = sqlite3.Row
        exam = conn.execute(
            "SELECT * FROM exams WHERE exam_code=?", (exam_code,)
        ).fetchone()
        conn.close()
        if exam:
            return redirect(url_for('exam_mgmt.take_exam', exam_id=exam['id']))
        return "Invalid Exam Code!"
    return render_template('student/student_login.html')


# ══════════════════════════════════════════════════════════════════
# TAKE EXAM  ← KEY FIX: loads questions from papers.questions JSON
# ══════════════════════════════════════════════════════════════════
@exam_mgmt_bp.route('/student/take-exam/<int:exam_id>')
def take_exam(exam_id):
    if 'email' not in session:
        return redirect('/login')

    conn = get_db()
    conn.row_factory = sqlite3.Row

    # Get the exam row
    exam = conn.execute(
        "SELECT * FROM exams WHERE id=?", (exam_id,)
    ).fetchone()

    if not exam:
        conn.close()
        return "Exam not found.", 404

    exam_dict = dict(exam)
    if 'duration_minutes' not in exam_dict:
        exam_dict['duration_minutes'] = int(exam_dict.get('duration') or 60)

    questions = []

    # ── Strategy 1: check exam_paper_assignments table ───────────
    try:
        assignment = conn.execute(
            "SELECT paper_id FROM exam_paper_assignments WHERE exam_id=?",
            (exam_id,)
        ).fetchone()
        if assignment:
            paper = conn.execute(
                "SELECT questions FROM papers WHERE id=?",
                (assignment['paper_id'],)
            ).fetchone()
            if paper and paper['questions']:
                raw = json.loads(paper['questions'])
                questions = _normalise_questions(raw)
    except Exception:
        pass

    # ── Strategy 2: exam has a linked paper_id directly ──────────
    if not questions:
        try:
            paper = conn.execute(
                "SELECT questions FROM papers WHERE id=?", (exam_id,)
            ).fetchone()
            if paper and paper['questions']:
                raw = json.loads(paper['questions'])
                questions = _normalise_questions(raw)
        except Exception:
            pass

    # ── Strategy 3: questions table (old bulk-upload path) ───────
    if not questions:
        try:
            rows = conn.execute(
                "SELECT * FROM questions WHERE exam_id=? ORDER BY id",
                (exam_id,)
            ).fetchall()
            if rows:
                questions = [_row_to_question(dict(r)) for r in rows]
        except Exception:
            pass

    # ── Strategy 4: first live paper in papers table ─────────────
    if not questions:
        try:
            paper = conn.execute(
                "SELECT questions FROM papers WHERE status='live' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if paper and paper['questions']:
                raw = json.loads(paper['questions'])
                questions = _normalise_questions(raw)
        except Exception:
            pass

    conn.close()

    session['current_exam_id'] = exam_id
    session.modified = True

    return render_template(
        'exam.html',
        exam      = exam_dict,
        questions = questions
    )


def _normalise_questions(raw_list):
    """
    Convert questions stored in any format into a uniform dict:
    { id, question_text, option_a, option_b, option_c, option_d, correct_answer }
    """
    result = []
    for i, q in enumerate(raw_list):
        if not isinstance(q, dict):
            continue
        # Support both 'text' and 'question_text' keys
        text = q.get('question_text') or q.get('text') or ''
        opts = q.get('options') or []

        # Support both 'options' list and option_a/b/c/d keys
        if opts and len(opts) >= 4:
            oa, ob, oc, od = opts[0], opts[1], opts[2], opts[3]
        else:
            oa = q.get('option_a', '')
            ob = q.get('option_b', '')
            oc = q.get('option_c', '')
            od = q.get('option_d', '')

        correct = (
            q.get('correct_answer') or
            q.get('correct') or 'A'
        ).strip().upper()

        result.append({
            'id':             q.get('id', i + 1),
            'question_text':  text,
            'option_a':       oa,
            'option_b':       ob,
            'option_c':       oc,
            'option_d':       od,
            'correct_answer': correct,
        })
    return result


def _row_to_question(row):
    return {
        'id':             row.get('id', 0),
        'question_text':  row.get('question_text', ''),
        'option_a':       row.get('option_a', ''),
        'option_b':       row.get('option_b', ''),
        'option_c':       row.get('option_c', ''),
        'option_d':       row.get('option_d', ''),
        'correct_answer': row.get('correct_answer', 'A'),
    }


# ══════════════════════════════════════════════════════════════════
# SUBMIT EXAM
# ══════════════════════════════════════════════════════════════════
@exam_mgmt_bp.route('/submit-exam', methods=['POST'])
def submit_exam():
    data    = request.json or {}
    answers = data.get('answers', {})
    exam_id = data.get('exam_id') or session.get('current_exam_id')

    if not exam_id:
        return jsonify({"score": 0, "total": 0})

    conn = get_db()
    conn.row_factory = sqlite3.Row

    # Try to get questions the same way take_exam does
    questions = []
    try:
        assignment = conn.execute(
            "SELECT paper_id FROM exam_paper_assignments WHERE exam_id=?", (exam_id,)
        ).fetchone()
        if assignment:
            paper = conn.execute(
                "SELECT questions FROM papers WHERE id=?",
                (assignment['paper_id'],)
            ).fetchone()
            if paper and paper['questions']:
                questions = _normalise_questions(json.loads(paper['questions']))
    except Exception:
        pass

    if not questions:
        try:
            paper = conn.execute(
                "SELECT questions FROM papers WHERE id=?", (exam_id,)
            ).fetchone()
            if paper and paper['questions']:
                questions = _normalise_questions(json.loads(paper['questions']))
        except Exception:
            pass

    if not questions:
        try:
            rows = conn.execute(
                "SELECT * FROM questions WHERE exam_id=? ORDER BY id", (exam_id,)
            ).fetchall()
            questions = [_row_to_question(dict(r)) for r in rows]
        except Exception:
            pass

    score = 0
    for q in questions:
        qid = q.get('id')
        student_ans = (answers.get(f"q_{qid}") or '').strip().upper()
        correct     = (q.get('correct_answer') or '').strip().upper()
        if student_ans and student_ans == correct:
            score += 1

    # Save result
    email = session.get('email', '')
    if email:
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS exam_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT, exam_id INTEGER,
                    score INTEGER, total INTEGER,
                    submitted_at TEXT DEFAULT (datetime('now','localtime'))
                )
            """)
            conn.execute(
                "INSERT INTO exam_results (email, exam_id, score, total) VALUES (?,?,?,?)",
                (email, exam_id, score, len(questions))
            )
            conn.commit()
        except Exception as e:
            print("Result save error:", e)

    conn.close()
    session.pop('current_exam_id', None)
    session.modified = True
    return jsonify({"score": score, "total": len(questions)})


@exam_mgmt_bp.route('/exam-done')
def exam_done():
    return render_template(
        'exam_done.html',
        score      = request.args.get('score', 0),
        total      = request.args.get('total', 0),
        terminated = request.args.get('terminated', 0),
        violations = session.get('last_exam_violations', 0)
    )


# ══════════════════════════════════════════════════════════════════
# SAVE PAPER (manual entry from admin dashboard)
# ══════════════════════════════════════════════════════════════════
@exam_mgmt_bp.route("/admin/save-paper", methods=["POST"])
def save_paper():
    data           = request.json or {}
    title          = data.get("title", "Untitled")
    subject        = data.get("subject", "General")
    marks          = data.get("total_marks", 50)
    duration       = data.get("duration", 60)
    questions_list = data.get("questions", [])

    conn   = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO papers (title, subject, total_marks, duration, questions, status) "
            "VALUES (?,?,?,?,?,'draft')",
            (title, subject, marks, duration, json.dumps(questions_list))
        )
        paper_id = cursor.lastrowid
        conn.commit()
        return jsonify({"status": "success", "paper_id": paper_id})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# DELETE PAPER
# ══════════════════════════════════════════════════════════════════
@exam_mgmt_bp.route("/admin/delete-paper/<int:paper_id>", methods=["POST"])
def delete_paper(paper_id):
    conn = get_db()
    conn.execute("DELETE FROM papers WHERE id=?", (paper_id,))
    conn.execute("DELETE FROM questions WHERE paper_id=?", (paper_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})


# ══════════════════════════════════════════════════════════════════
# VIEW PAPER (returns JSON for the popup modal)
# ══════════════════════════════════════════════════════════════════
@exam_mgmt_bp.route("/api/papers/<int:paper_id>", methods=["GET"])
def api_paper_detail(paper_id):
    conn = get_db()
    conn.row_factory = sqlite3.Row
    paper = conn.execute(
        "SELECT id, title, subject, total_marks, duration, questions FROM papers WHERE id=?",
        (paper_id,)
    ).fetchone()
    conn.close()

    if not paper:
        return jsonify({"error": "Paper not found"}), 404

    questions = []
    if paper['questions']:
        try:
            questions = _normalise_questions(json.loads(paper['questions']))
        except Exception:
            questions = []

    return jsonify({
        "paper": {
            "id":          paper['id'],
            "title":       paper['title'],
            "subject":     paper['subject'],
            "total_marks": paper['total_marks'],
            "duration":    paper['duration'],
        },
        "questions": questions
    })


# ══════════════════════════════════════════════════════════════════
# OLD HTML VIEW (kept for backward compat)
# ══════════════════════════════════════════════════════════════════
@exam_mgmt_bp.route("/admin/paper/view/<int:paper_id>")
def blueprint_view_paper(paper_id):
    conn = get_db()
    conn.row_factory = sqlite3.Row
    paper     = conn.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
    questions_raw = []
    if paper and paper['questions']:
        try:
            questions_raw = json.loads(paper['questions'])
        except Exception:
            pass
    questions = _normalise_questions(questions_raw)
    conn.close()
    if not paper:
        return "Paper not found", 404
    return render_template("admin/view_questions.html", exam=paper, questions=questions)

