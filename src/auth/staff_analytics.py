# src/staff_analytics.py
# ══════════════════════════════════════════════════════════════════
# STAFF ANALYTICS — REBUILT TO MATCH YOUR EXACT DATABASE SCHEMA
# ══════════════════════════════════════════════════════════════════

import io
import csv
import json
import sqlite3
from datetime import datetime, date
from flask import (
    Blueprint, request, jsonify, session,
    redirect, make_response
)
from database.db_utils import get_db

staff_bp = Blueprint("staff_analytics", __name__)


# ══════════════════════════════════════════════════════════════════
# SAFE MIGRATIONS
# ══════════════════════════════════════════════════════════════════
def run_migrations():
    conn   = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS departments (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            name             TEXT UNIQUE NOT NULL,
            head_staff_email TEXT,
            created_at       TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS staff (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name     TEXT,
            email         TEXT UNIQUE,
            designation   TEXT DEFAULT 'Lecturer',
            phone         TEXT,
            department_id INTEGER,
            created_at    TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (department_id) REFERENCES departments(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subjects (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            code          TEXT UNIQUE,
            department_id INTEGER,
            staff_user_id INTEGER,
            created_at    TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS student_subjects (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id    INTEGER,
            student_email TEXT,
            enrolled_at   TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(subject_id, student_email)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS staff_exams (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            title         TEXT NOT NULL,
            subject_id    INTEGER,
            staff_user_id INTEGER NOT NULL,
            paper_id      INTEGER,
            exam_date     TEXT NOT NULL,
            exam_type     TEXT DEFAULT 'written',
            duration_min  INTEGER DEFAULT 60,
            total_marks   INTEGER DEFAULT 100,
            venue         TEXT DEFAULT '',
            notes         TEXT DEFAULT '',
            start_time    TEXT,
            end_time      TEXT,
            created_at    TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exam_attendance (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_id      INTEGER,
            email        TEXT,
            attended     INTEGER DEFAULT 0,
            marks        REAL,
            marked_at    TEXT,
            started_at   TEXT DEFAULT (datetime('now','localtime')),
            submitted_at TEXT,
            score        INTEGER DEFAULT 0,
            total        INTEGER DEFAULT 0,
            UNIQUE(exam_id, email)
        )
    """)

    for col_sql in ["ALTER TABLE users ADD COLUMN department_id INTEGER"]:
        try:    cursor.execute(col_sql)
        except: pass

    # Auto-backfill paper_staff_link from existing staff_exams
    try:
        linked_exams = cursor.execute("""
            SELECT se.id, se.paper_id, se.staff_user_id, se.subject_id,
                   u.email AS staff_email, u.full_name AS staff_name
            FROM staff_exams se
            JOIN users u ON u.id = se.staff_user_id
            WHERE se.paper_id IS NOT NULL
        """).fetchall()
        for row in linked_exams:
            cursor.execute("""
                INSERT INTO paper_staff_link
                    (paper_id, staff_user_id, staff_email, staff_name, subject_id)
                VALUES (?,?,?,?,?)
                ON CONFLICT(paper_id) DO UPDATE SET
                    staff_user_id=excluded.staff_user_id,
                    staff_email=excluded.staff_email,
                    staff_name=excluded.staff_name,
                    subject_id=excluded.subject_id
            """, (row[1], row[2], row[4], row[5], row[3]))
            cursor.execute("""
                UPDATE papers SET
                    created_by_email=CASE WHEN created_by_email IS NULL OR created_by_email=''
                                     THEN ? ELSE created_by_email END,
                    created_by_name =CASE WHEN created_by_name  IS NULL OR created_by_name =''
                                     THEN ? ELSE created_by_name  END
                WHERE id=?
            """, (row[4], row[5], row[1]))
        conn.commit()
        print(f"[Migration] Backfilled paper_staff_link for {len(linked_exams)} exam(s)")
    except Exception as e:
        print(f"[Migration] Backfill error (non-fatal): {e}")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS staff_notifications (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER,
            message    TEXT,
            link       TEXT,
            is_read    INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    for col_sql in ["ALTER TABLE departments ADD COLUMN head_staff_email TEXT"]:
        try:    cursor.execute(col_sql)
        except: pass

    for col_sql in [
        "ALTER TABLE papers ADD COLUMN created_by_email TEXT",
        "ALTER TABLE papers ADD COLUMN created_by_name  TEXT",
    ]:
        try:    cursor.execute(col_sql)
        except: pass

    for col_sql in [
        "ALTER TABLE exam_attendance ADD COLUMN attended  INTEGER DEFAULT 0",
        "ALTER TABLE exam_attendance ADD COLUMN marks     REAL",
        "ALTER TABLE exam_attendance ADD COLUMN marked_at TEXT",
    ]:
        try:    cursor.execute(col_sql)
        except: pass

    try:
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_exam_attendance_exam_email
            ON exam_attendance (exam_id, email)
        """)
    except: pass

    for col_sql in [
        "ALTER TABLE staff_exams ADD COLUMN start_time TEXT",
        "ALTER TABLE staff_exams ADD COLUMN end_time   TEXT",
        "ALTER TABLE staff_exams ADD COLUMN paper_id   INTEGER",
    ]:
        try:    cursor.execute(col_sql)
        except: pass

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS paper_staff_link (
            paper_id      INTEGER PRIMARY KEY,
            staff_user_id INTEGER,
            staff_email   TEXT,
            staff_name    TEXT,
            subject_id    INTEGER,
            created_at    TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    conn.commit()
    conn.close()
    print("[StaffAnalytics] Migrations complete")


# ══════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════
def _admin_only():
    role = (session.get("role") or "").lower()
    return role in ("admin", "superadmin", "staff_admin")


def _rows_to_dicts(rows):
    return [dict(r) for r in rows]


def _classify(exam_date_str, start_time_str=None, end_time_str=None):
    try:
        now      = datetime.now()
        date_str = str(exam_date_str)[:10]
        if start_time_str and end_time_str:
            start_dt = datetime.fromisoformat(f"{date_str}T{str(start_time_str)[:5]}")
            end_dt   = datetime.fromisoformat(f"{date_str}T{str(end_time_str)[:5]}")
            if now < start_dt:  return "upcoming"
            if now <= end_dt:   return "current"
            return "previous"
        elif start_time_str:
            start_dt   = datetime.fromisoformat(f"{date_str}T{str(start_time_str)[:5]}")
            end_of_day = datetime.fromisoformat(f"{date_str}T23:59:59")
            if now < start_dt:    return "upcoming"
            if now <= end_of_day: return "current"
            return "previous"
        else:
            ed    = date.fromisoformat(date_str)
            today = date.today()
            if ed < today:  return "previous"
            if ed == today: return "current"
            return "upcoming"
    except Exception:
        return "previous"


# ══════════════════════════════════════════════════════════════════
# DEBUG
# ══════════════════════════════════════════════════════════════════
@staff_bp.route("/admin/debug-db", methods=["GET"])
def debug_db():
    conn = get_db()
    conn.row_factory = sqlite3.Row
    result = {
        "session_role":  session.get("role"),
        "session_email": session.get("email"),
        "admin_check":   _admin_only(),
        "tables": {}
    }
    for tbl in ["users","staff","departments","subjects",
                "staff_exams","exam_attendance","papers",
                "paper_staff_link","student_subjects","exam_results"]:
        try:    result["tables"][tbl] = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        except Exception as e: result["tables"][tbl] = f"ERROR: {e}"
    try:
        roles = conn.execute("SELECT role, COUNT(*) as n FROM users GROUP BY role").fetchall()
        result["user_roles"] = {r["role"]: r["n"] for r in roles}
    except Exception as e:
        result["user_roles"] = str(e)
    conn.close()
    return jsonify(result)


# ══════════════════════════════════════════════════════════════════
# REPAIR LINKS
# ══════════════════════════════════════════════════════════════════
@staff_bp.route("/admin/repair-links", methods=["POST"])
def repair_links():
    if not _admin_only():
        return jsonify({"error": "Unauthorized"}), 403
    conn   = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    fixed  = 0
    try:
        linked_exams = cursor.execute("""
            SELECT se.paper_id, se.staff_user_id, se.subject_id,
                   u.email AS staff_email, u.full_name AS staff_name
            FROM staff_exams se
            JOIN users u ON u.id = se.staff_user_id
            WHERE se.paper_id IS NOT NULL
        """).fetchall()
        for row in linked_exams:
            cursor.execute("""
                INSERT INTO paper_staff_link
                    (paper_id, staff_user_id, staff_email, staff_name, subject_id)
                VALUES (?,?,?,?,?)
                ON CONFLICT(paper_id) DO UPDATE SET
                    staff_user_id=excluded.staff_user_id,
                    staff_email=excluded.staff_email,
                    staff_name=excluded.staff_name,
                    subject_id=excluded.subject_id
            """, (row["paper_id"], row["staff_user_id"],
                  row["staff_email"], row["staff_name"], row["subject_id"]))
            cursor.execute("""
                UPDATE papers SET
                    created_by_email=CASE WHEN created_by_email IS NULL OR created_by_email=''
                                     THEN ? ELSE created_by_email END,
                    created_by_name =CASE WHEN created_by_name  IS NULL OR created_by_name =''
                                     THEN ? ELSE created_by_name  END
                WHERE id=?
            """, (row["staff_email"], row["staff_name"], row["paper_id"]))
            fixed += 1
        conn.commit()

        # Backfill attendance for already-submitted students
        results = cursor.execute("""
            SELECT er.exam_id, er.email
            FROM exam_results er
            JOIN paper_staff_link psl ON psl.paper_id = er.exam_id
        """).fetchall()
        att_fixed = 0
        for r in results:
            _upsert_attendance_direct(conn, r["exam_id"], r["email"])
            att_fixed += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({"error": str(e)}), 500

    conn.close()
    return jsonify({
        "status": "success",
        "links_rebuilt": fixed,
        "attendance_backfilled": att_fixed,
        "message": f"Rebuilt {fixed} paper→staff links and backfilled {att_fixed} attendance records"
    })


def _upsert_attendance_direct(conn, paper_id: int, student_email: str):
    try:
        exam_rows = conn.execute(
            "SELECT id FROM staff_exams WHERE paper_id=?", (paper_id,)
        ).fetchall()
        for exam_row in exam_rows:
            conn.execute("""
                INSERT INTO exam_attendance (exam_id, email, attended, marked_at)
                VALUES (?,?,1,datetime('now','localtime'))
                ON CONFLICT(exam_id, email) DO UPDATE SET
                    attended=1, marked_at=datetime('now','localtime')
            """, (exam_row["id"], student_email))
    except Exception as e:
        print(f"[_upsert_attendance_direct] {e}")


# ══════════════════════════════════════════════════════════════════
# DEPARTMENTS
# ══════════════════════════════════════════════════════════════════
@staff_bp.route("/admin/departments", methods=["GET"])
def get_departments():
    if not _admin_only(): return jsonify({"error": "Unauthorized"}), 403
    conn = get_db(); conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT d.*,
               (SELECT COUNT(*) FROM subjects s WHERE s.department_id=d.id) AS subjects_count,
               (SELECT COUNT(*) FROM staff s WHERE s.department_id=d.id) AS staff_count,
               COALESCE(
                   (SELECT u.full_name FROM users u WHERE u.email=d.head_staff_email LIMIT 1),
                   (SELECT sf.full_name FROM staff sf WHERE sf.email=d.head_staff_email LIMIT 1)
               ) AS head_name
        FROM departments d ORDER BY d.name
    """).fetchall()
    conn.close()
    return jsonify(_rows_to_dicts(rows))


@staff_bp.route("/admin/departments/<int:dept_id>/set-head", methods=["POST"])
def set_department_head(dept_id):
    if not _admin_only(): return jsonify({"error": "Unauthorized"}), 403
    data  = request.json or {}
    email = (data.get("email") or "").strip().lower()
    if not email: return jsonify({"error": "Staff email required"}), 400
    conn   = get_db(); conn.row_factory = sqlite3.Row
    person = conn.execute(
        "SELECT full_name FROM staff WHERE email=? UNION "
        "SELECT full_name FROM users WHERE email=? AND LOWER(role) IN ('admin','staff') LIMIT 1",
        (email, email)
    ).fetchone()
    if not person:
        conn.close(); return jsonify({"error": "Staff member not found"}), 404
    conn.execute("UPDATE departments SET head_staff_email=? WHERE id=?", (email, dept_id))
    conn.commit()
    dept = conn.execute("""
        SELECT d.*,
               COALESCE(
                   (SELECT u.full_name FROM users u WHERE u.email=d.head_staff_email LIMIT 1),
                   (SELECT sf.full_name FROM staff sf WHERE sf.email=d.head_staff_email LIMIT 1)
               ) AS head_name,
               (SELECT COUNT(*) FROM staff s WHERE s.department_id=d.id) AS staff_count,
               (SELECT COUNT(*) FROM subjects s WHERE s.department_id=d.id) AS subjects_count
        FROM departments d WHERE d.id=?
    """, (dept_id,)).fetchone()
    conn.close()
    return jsonify({"status":"success","message":f"{person['full_name']} set as department head",
                    "dept": dict(dept) if dept else {}})


@staff_bp.route("/admin/departments", methods=["POST"])
def create_department():
    if not _admin_only(): return jsonify({"error": "Unauthorized"}), 403
    data = request.json or {}
    name = (data.get("name") or "").strip()
    code = (data.get("code") or "").strip()
    desc = (data.get("description") or "").strip()
    if not name: return jsonify({"error": "Department name is required"}), 400
    if not code:
        code = "".join(w[0].upper() for w in name.split()[:4])
    conn = get_db()
    try:
        conn.execute("INSERT INTO departments (name, code, description) VALUES (?,?,?)",
                     (name, code, desc or None))
        conn.commit()
        return jsonify({"status":"success","message":f"Department '{name}' created"})
    except sqlite3.IntegrityError:
        return jsonify({"error":"Department name or code already exists"}), 409
    finally:
        conn.close()


@staff_bp.route("/admin/departments/<int:dept_id>", methods=["DELETE"])
def delete_department(dept_id):
    if not _admin_only(): return jsonify({"error": "Unauthorized"}), 403
    conn = get_db()
    conn.execute("DELETE FROM departments WHERE id=?", (dept_id,))
    conn.commit(); conn.close()
    return jsonify({"status":"success"})


# ══════════════════════════════════════════════════════════════════
# SUBJECTS
# ══════════════════════════════════════════════════════════════════
@staff_bp.route("/admin/subjects", methods=["GET"])
def get_subjects():
    if not _admin_only(): return jsonify({"error": "Unauthorized"}), 403
    dept_id = request.args.get("dept_id")
    conn = get_db(); conn.row_factory = sqlite3.Row
    base = """
        SELECT sub.id, sub.name, sub.code, sub.department_id, sub.staff_user_id,
               d.name AS dept_name, u.full_name AS staff_name,
               (SELECT COUNT(*) FROM student_subjects ss WHERE ss.subject_id=sub.id) AS enrolled_count
        FROM subjects sub
        LEFT JOIN departments d ON d.id = sub.department_id
        LEFT JOIN users u ON u.id = sub.staff_user_id
    """
    rows = conn.execute(
        base + (" WHERE sub.department_id=? ORDER BY sub.name" if dept_id else " ORDER BY sub.name"),
        (dept_id,) if dept_id else ()
    ).fetchall()
    conn.close()
    return jsonify(_rows_to_dicts(rows))


@staff_bp.route("/admin/subjects", methods=["POST"])
def create_subject():
    if not _admin_only(): return jsonify({"error": "Unauthorized"}), 403
    data = request.json or {}
    name = (data.get("name") or "").strip()
    if not name: return jsonify({"error": "Subject name required"}), 400
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO subjects (name, code, department_id, staff_user_id) VALUES (?,?,?,?)",
            (name, data.get("code") or None,
             data.get("department_id") or None, data.get("staff_user_id") or None))
        conn.commit()
        return jsonify({"status":"success","message":f"Subject '{name}' created"})
    except sqlite3.IntegrityError:
        return jsonify({"error":"Subject code already exists"}), 409
    finally:
        conn.close()


@staff_bp.route("/admin/subjects/<int:subj_id>", methods=["DELETE"])
def delete_subject(subj_id):
    if not _admin_only(): return jsonify({"error": "Unauthorized"}), 403
    conn = get_db()
    conn.execute("DELETE FROM student_subjects WHERE subject_id=?", (subj_id,))
    conn.execute("DELETE FROM subjects WHERE id=?", (subj_id,))
    conn.commit(); conn.close()
    return jsonify({"status":"success"})


@staff_bp.route("/admin/subjects/<int:subj_id>/enrol", methods=["POST"])
def enrol_students(subj_id):
    if not _admin_only(): return jsonify({"error": "Unauthorized"}), 403
    data   = request.json or {}
    emails = [e.strip().lower() for e in (data.get("emails") or []) if e.strip()]
    if not emails: return jsonify({"error":"No emails provided"}), 400
    conn = get_db(); added = 0
    for email in emails:
        try:
            conn.execute("INSERT OR IGNORE INTO student_subjects (student_email, subject_id) VALUES (?,?)",
                         (email, subj_id))
            added += 1
        except: pass
    conn.commit(); conn.close()
    return jsonify({"status":"success","enrolled": added})


@staff_bp.route("/admin/subjects/<int:subj_id>/students", methods=["GET"])
def subject_students(subj_id):
    if not _admin_only(): return jsonify({"error": "Unauthorized"}), 403
    conn = get_db(); conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT ss.student_email AS email,
               COALESCE(u.full_name, ss.student_email) AS full_name
        FROM student_subjects ss
        LEFT JOIN users u ON u.email = ss.student_email
        WHERE ss.subject_id=? ORDER BY full_name
    """, (subj_id,)).fetchall()
    conn.close()
    return jsonify(_rows_to_dicts(rows))


# ══════════════════════════════════════════════════════════════════
# STAFF LIST
# ══════════════════════════════════════════════════════════════════
@staff_bp.route("/admin/staff-list", methods=["GET"])
def staff_list():
    if not _admin_only(): return jsonify({"error": "Unauthorized"}), 403
    conn = get_db(); conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT s.id, s.full_name, s.email, s.designation, s.phone, s.created_at,
               COALESCE(d.name,'General') AS department,
               d.id AS department_id, d.name AS dept_name,
               COUNT(DISTINCT sub.id) AS subjects_count,
               COUNT(DISTINCT se.id)  AS exams_count,
               COUNT(DISTINCT ss.student_email) AS students_count,
               u.id AS user_id,
               CASE WHEN d.head_staff_email=s.email THEN 1 ELSE 0 END AS is_head,
               (SELECT COUNT(*) FROM papers p WHERE p.created_by_email=s.email) AS papers_created
        FROM staff s
        LEFT JOIN departments d ON d.id=s.department_id
        LEFT JOIN users u ON u.email=s.email
        LEFT JOIN subjects sub ON sub.staff_user_id=u.id
        LEFT JOIN student_subjects ss ON ss.subject_id=sub.id
        LEFT JOIN staff_exams se ON se.staff_user_id=u.id
        GROUP BY s.id ORDER BY s.full_name
    """).fetchall()
    staff_emails = {r["email"] for r in rows}
    ph = ",".join("?" * len(staff_emails)) if staff_emails else "'__none__'"
    extra = conn.execute(f"""
        SELECT u.id AS id, u.full_name, u.email, 'Lecturer' AS designation,
               '' AS phone, u.id AS user_id,
               COALESCE(d.name,'General') AS department, d.id AS department_id,
               d.name AS dept_name,
               COUNT(DISTINCT sub.id) AS subjects_count,
               COUNT(DISTINCT se.id)  AS exams_count,
               COUNT(DISTINCT ss.student_email) AS students_count,
               CASE WHEN d.head_staff_email=u.email THEN 1 ELSE 0 END AS is_head,
               (SELECT COUNT(*) FROM papers p WHERE p.created_by_email=u.email) AS papers_created
        FROM users u
        LEFT JOIN departments d ON d.id=u.department_id
        LEFT JOIN subjects sub ON sub.staff_user_id=u.id
        LEFT JOIN student_subjects ss ON ss.subject_id=sub.id
        LEFT JOIN staff_exams se ON se.staff_user_id=u.id
        WHERE LOWER(u.role) IN ('admin','staff','staff_admin','superadmin')
          AND u.email NOT IN ({ph})
        GROUP BY u.id ORDER BY u.full_name
    """, list(staff_emails) if staff_emails else []).fetchall()
    conn.close()
    return jsonify(_rows_to_dicts(rows) + _rows_to_dicts(extra))


@staff_bp.route("/admin/staff-list", methods=["POST"])
def upsert_staff():
    if not _admin_only(): return jsonify({"error": "Unauthorized"}), 403
    data  = request.json or {}
    email = (data.get("email") or "").strip().lower()
    if not email: return jsonify({"error":"Email required"}), 400
    name  = (data.get("full_name") or email).strip()
    conn  = get_db()
    try:
        conn.execute("""
            INSERT INTO staff (full_name, email, department_id, designation, phone)
            VALUES (?,?,?,?,?)
            ON CONFLICT(email) DO UPDATE SET
                full_name=excluded.full_name, department_id=excluded.department_id,
                designation=excluded.designation, phone=excluded.phone
        """, (name, email, data.get("department_id") or None,
              data.get("designation","Lecturer"), data.get("phone") or None))
        if data.get("department_id"):
            conn.execute("UPDATE users SET department_id=? WHERE email=?",
                         (data["department_id"], email))
        conn.commit()
        return jsonify({"status":"success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# STAFF INDIVIDUAL DASHBOARD
# ══════════════════════════════════════════════════════════════════
@staff_bp.route("/admin/staff/<int:staff_id>/dashboard", methods=["GET"])
def staff_dashboard(staff_id):
    if not _admin_only(): return jsonify({"error": "Unauthorized"}), 403
    conn   = get_db(); conn.row_factory = sqlite3.Row; cursor = conn.cursor()

    person = cursor.execute("""
        SELECT s.id, s.full_name, s.email, s.designation, s.phone,
               COALESCE(d.name,'General') AS department, d.id AS department_id,
               u.id AS user_id
        FROM staff s
        LEFT JOIN departments d ON d.id=s.department_id
        LEFT JOIN users u ON u.email=s.email
        WHERE s.id=?
    """, (staff_id,)).fetchone()

    if not person:
        person = cursor.execute("""
            SELECT u.id, u.full_name, u.email, 'Lecturer' AS designation, '' AS phone,
                   COALESCE(d.name,'General') AS department, d.id AS department_id,
                   u.id AS user_id
            FROM users u
            LEFT JOIN departments d ON d.id=u.department_id
            WHERE u.id=? AND LOWER(u.role) IN ('admin','staff','staff_admin','superadmin')
        """, (staff_id,)).fetchone()

    if not person:
        conn.close(); return jsonify({"error":"Staff not found"}), 404

    pd      = dict(person)
    user_id = pd.get("user_id") or staff_id

    subjects = cursor.execute("""
        SELECT sub.id, sub.name, sub.code, d.name AS dept_name,
               (SELECT COUNT(*) FROM student_subjects ss WHERE ss.subject_id=sub.id) AS student_count
        FROM subjects sub
        LEFT JOIN departments d ON d.id=sub.department_id
        WHERE sub.staff_user_id=? ORDER BY sub.name
    """, (user_id,)).fetchall()

    # ── students_count: UNION all sources ──
    students_count = cursor.execute("""
        SELECT COUNT(DISTINCT email) FROM (
            SELECT er.email FROM staff_exams se
            JOIN exam_results er ON er.exam_id=se.paper_id
            WHERE se.staff_user_id=?
            UNION
            SELECT ea.email FROM staff_exams se
            JOIN exam_attendance ea ON ea.exam_id=se.id
            WHERE se.staff_user_id=?
            UNION
            SELECT ss.student_email FROM subjects sub
            JOIN student_subjects ss ON ss.subject_id=sub.id
            WHERE sub.staff_user_id=?
        )
    """, (user_id, user_id, user_id)).fetchone()[0] or 0

    total_students_in_system = cursor.execute(
        "SELECT COUNT(*) FROM users WHERE LOWER(role)='student'"
    ).fetchone()[0] or 1

    # ── exams with UNION-based counts ──
    exams_raw = cursor.execute("""
        SELECT se.*, sub.name AS subject_name,
               (SELECT COUNT(DISTINCT email) FROM (
                   SELECT email FROM exam_attendance WHERE exam_id=se.id
                   UNION
                   SELECT email FROM exam_results WHERE exam_id=se.paper_id
               )) AS total_enrolled,
               (SELECT COUNT(DISTINCT email) FROM (
                   SELECT email FROM exam_attendance WHERE exam_id=se.id AND attended=1
                   UNION
                   SELECT email FROM exam_results WHERE exam_id=se.paper_id
               )) AS present_count
        FROM staff_exams se
        LEFT JOIN subjects sub ON sub.id=se.subject_id
        WHERE se.staff_user_id=?
        ORDER BY se.exam_date DESC
    """, (user_id,)).fetchall()

    exams_list = []
    for e in exams_raw:
        ed       = dict(e)
        cls      = _classify(ed["exam_date"])
        enrolled = ed["total_enrolled"] or 0
        attended = ed["present_count"]  or 0
        att      = round(attended / enrolled * 100, 1) if enrolled > 0 else 0

        # ── attendees: UNION exam_attendance + exam_results ──
        attendees = cursor.execute("""
            SELECT combined.email,
                   combined.attended,
                   COALESCE(u.full_name, combined.email) AS name,
                   combined.marked_at,
                   combined.score,
                   combined.total
            FROM (
                SELECT ea.email, ea.attended, ea.marked_at, er.score, er.total
                FROM exam_attendance ea
                LEFT JOIN exam_results er ON er.email=ea.email AND er.exam_id=?
                WHERE ea.exam_id=?
                UNION
                SELECT er2.email, 1 AS attended, er2.submitted_at AS marked_at,
                       er2.score, er2.total
                FROM exam_results er2
                WHERE er2.exam_id=?
                  AND er2.email NOT IN (
                      SELECT email FROM exam_attendance WHERE exam_id=?
                  )
            ) combined
            LEFT JOIN users u ON u.email=combined.email
            ORDER BY combined.attended DESC, name ASC
        """, (ed["id"], ed["id"], ed["id"], ed["id"])).fetchall()

        ed.update({
            "classification": cls,
            "attendance_pct": att,
            "attended_count": attended,
            "enrolled_count": enrolled,
            "attendees": [{
                "email":    a["email"],
                "name":     a["name"],
                "attended": a["attended"],
                "marked_at":a["marked_at"] or "",
                "score":    a["score"],
                "total":    a["total"],
            } for a in attendees],
        })
        exams_list.append(ed)

    previous = [e for e in exams_list if e["classification"] == "previous"]
    current  = [e for e in exams_list if e["classification"] == "current"]
    upcoming = [e for e in exams_list if e["classification"] == "upcoming"]

    # ── overall avg attendance: UNION both tables ──
    total_attended = cursor.execute("""
        SELECT COUNT(DISTINCT email) FROM (
            SELECT ea.email FROM staff_exams se
            JOIN exam_attendance ea ON ea.exam_id=se.id AND ea.attended=1
            WHERE se.staff_user_id=?
            UNION
            SELECT er.email FROM staff_exams se
            JOIN exam_results er ON er.exam_id=se.paper_id
            WHERE se.staff_user_id=?
        )
    """, (user_id, user_id)).fetchone()[0] or 0
    avg_att = round(total_attended / total_students_in_system * 100, 1)

    # papers created
    papers = cursor.execute("""
        SELECT id, title, subject, total_marks, duration, status
        FROM papers WHERE created_by_email=(SELECT email FROM users WHERE id=?)
        ORDER BY id DESC
    """, (user_id,)).fetchall()

    conn.close()
    return jsonify({
        "staff":            pd,
        "subjects":         _rows_to_dicts(subjects),
        "students_count":   students_count,
        "students_attended":total_attended,
        "exams_total":      len(exams_list),
        "previous_exams":   previous,
        "current_exams":    current,
        "upcoming_exams":   upcoming,
        "avg_attendance":   avg_att,
        "papers":           _rows_to_dicts(papers),
    })


# ══════════════════════════════════════════════════════════════════
# STAFF EXAMS — GET ALL
# ══════════════════════════════════════════════════════════════════
@staff_bp.route("/admin/staff-exams", methods=["GET"])
def all_staff_exams():
    if not _admin_only(): return jsonify({"error": "Unauthorized"}), 403
    dept_id   = request.args.get("dept_id")
    subj_id   = request.args.get("subj_id")
    staff_uid = request.args.get("staff_user_id")
    ex_type   = request.args.get("type")
    q         = (request.args.get("q") or "").strip().lower()
    page      = max(1, int(request.args.get("page", 1)))
    per_page  = int(request.args.get("per_page", 15))

    conn = get_db(); conn.row_factory = sqlite3.Row
    sql = """
        SELECT se.*,
               sub.name AS subject_name, d.name AS dept_name, u.full_name AS staff_name,
               (SELECT COUNT(DISTINCT email) FROM (
                   SELECT email FROM exam_attendance WHERE exam_id=se.id
                   UNION
                   SELECT email FROM exam_results WHERE exam_id=se.paper_id
               )) AS total_enrolled,
               (SELECT COUNT(DISTINCT email) FROM (
                   SELECT email FROM exam_attendance WHERE exam_id=se.id AND attended=1
                   UNION
                   SELECT email FROM exam_results WHERE exam_id=se.paper_id
               )) AS present_count
        FROM staff_exams se
        LEFT JOIN subjects sub ON sub.id=se.subject_id
        LEFT JOIN departments d ON d.id=sub.department_id
        LEFT JOIN users u ON u.id=se.staff_user_id
        WHERE 1=1
    """
    params = []
    if dept_id:   sql += " AND d.id=?";            params.append(dept_id)
    if subj_id:   sql += " AND sub.id=?";           params.append(subj_id)
    if staff_uid: sql += " AND se.staff_user_id=?"; params.append(staff_uid)
    sql += " ORDER BY se.exam_date DESC"

    rows   = conn.execute(sql, params).fetchall()
    conn.close()
    result = []
    for r in rows:
        ed  = dict(r)
        cls = _classify(ed["exam_date"], ed.get("start_time"), ed.get("end_time"))
        ed["classification"] = cls
        te = ed["total_enrolled"] or 0
        pc = ed["present_count"]  or 0
        ed["attendance_pct"] = round(pc / te * 100, 1) if te else 0
        if ex_type and cls != ex_type: continue
        if q and q not in ((ed.get("title") or "") + (ed.get("staff_name") or "") +
                           (ed.get("subject_name") or "")).lower(): continue
        result.append(ed)

    total = len(result)
    paged = result[(page-1)*per_page : page*per_page]
    return jsonify({"exams": paged, "total": total, "page": page,
                    "per_page": per_page, "pages": max(1,(total+per_page-1)//per_page)})


# ══════════════════════════════════════════════════════════════════
# STAFF EXAMS — CREATE
# ══════════════════════════════════════════════════════════════════
@staff_bp.route("/admin/staff-exams", methods=["POST"])
def create_staff_exam():
    if not _admin_only(): return jsonify({"error": "Unauthorized"}), 403
    data       = request.json or {}
    title      = (data.get("title") or "").strip()
    exam_date  = (data.get("exam_date") or "").strip()
    staff_uid  = data.get("staff_user_id")
    start_time = (data.get("start_time") or "").strip()
    end_time   = (data.get("end_time")   or "").strip()

    if not title or not exam_date or not staff_uid:
        return jsonify({"error": "title, exam_date and staff_user_id are required"}), 400

    if start_time and not end_time:
        try:
            dur = int(data.get("duration_min", 60))
            sh, sm = map(int, start_time.split(":"))
            total_m = sh*60 + sm + dur
            end_time = f"{total_m//60:02d}:{total_m%60:02d}"
        except: pass

    conn = get_db(); cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO staff_exams
            (title, subject_id, staff_user_id, paper_id, exam_date,
             exam_type, duration_min, total_marks, venue, notes, start_time, end_time)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (title, data.get("subject_id") or None, int(staff_uid),
          data.get("paper_id") or None, exam_date,
          data.get("exam_type","written"), int(data.get("duration_min",60)),
          int(data.get("total_marks",100)), data.get("venue",""),
          data.get("notes",""), start_time or None, end_time or None))
    exam_id = cursor.lastrowid

    paper_id = data.get("paper_id")
    if paper_id:
        staff_row = conn.execute(
            "SELECT email, full_name FROM users WHERE id=?", (int(staff_uid),)
        ).fetchone()
        staff_email = staff_row[0] if staff_row else ""
        staff_name  = staff_row[1] if staff_row else ""
        try:
            cursor.execute("""
                INSERT INTO paper_staff_link
                    (paper_id, staff_user_id, staff_email, staff_name, subject_id)
                VALUES (?,?,?,?,?)
                ON CONFLICT(paper_id) DO UPDATE SET
                    staff_user_id=excluded.staff_user_id,
                    staff_email=excluded.staff_email,
                    staff_name=excluded.staff_name,
                    subject_id=excluded.subject_id
            """, (int(paper_id), int(staff_uid), staff_email, staff_name,
                  data.get("subject_id") or None))
        except Exception as e:
            print(f"[paper_staff_link] {e}")
        try:
            cursor.execute("""
                UPDATE papers SET
                    created_by_email=COALESCE(NULLIF(created_by_email,''),?),
                    created_by_name =COALESCE(NULLIF(created_by_name, ''),?)
                WHERE id=?
            """, (staff_email, staff_name, int(paper_id)))
        except Exception as e:
            print(f"[paper created_by update] {e}")

    subj_id = data.get("subject_id")
    if subj_id:
        students = conn.execute(
            "SELECT student_email FROM student_subjects WHERE subject_id=?", (subj_id,)
        ).fetchall()
        for s in students:
            try:
                cursor.execute(
                    "INSERT OR IGNORE INTO exam_attendance (exam_id, email) VALUES (?,?)",
                    (exam_id, s[0]))
            except: pass

    if _classify(exam_date) == "upcoming":
        try:
            cursor.execute(
                "INSERT INTO staff_notifications (user_id, message, link) VALUES (?,?,?)",
                (int(staff_uid), f"Upcoming exam: {title} on {exam_date}",
                 "/admin/dashboard#staff-exams"))
        except: pass

    conn.commit(); conn.close()
    return jsonify({"status":"success","exam_id":exam_id,"message":f"Exam '{title}' created"})


@staff_bp.route("/admin/staff-exams/<int:exam_id>", methods=["DELETE"])
def delete_staff_exam(exam_id):
    if not _admin_only(): return jsonify({"error": "Unauthorized"}), 403
    conn = get_db()
    conn.execute("DELETE FROM exam_attendance WHERE exam_id=?", (exam_id,))
    conn.execute("DELETE FROM staff_exams WHERE id=?", (exam_id,))
    conn.commit(); conn.close()
    return jsonify({"status":"success"})


# ══════════════════════════════════════════════════════════════════
# ATTENDANCE ENDPOINTS
# ══════════════════════════════════════════════════════════════════
@staff_bp.route("/admin/staff-exams/<int:exam_id>/attendance", methods=["GET"])
def get_attendance(exam_id):
    if not _admin_only(): return jsonify({"error": "Unauthorized"}), 403
    conn = get_db(); conn.row_factory = sqlite3.Row
    exam = conn.execute("""
        SELECT se.*, sub.name AS subject_name, u.full_name AS staff_name
        FROM staff_exams se
        LEFT JOIN subjects sub ON sub.id=se.subject_id
        LEFT JOIN users u ON u.id=se.staff_user_id
        WHERE se.id=?
    """, (exam_id,)).fetchone()

    # UNION both attendance sources
    att = conn.execute("""
        SELECT combined.email, combined.attended,
               COALESCE(u.full_name, combined.email) AS full_name,
               combined.marked_at, combined.score, combined.total
        FROM (
            SELECT ea.email, ea.attended, ea.marked_at, er.score, er.total
            FROM exam_attendance ea
            LEFT JOIN exam_results er ON er.email=ea.email AND er.exam_id=?
            WHERE ea.exam_id=?
            UNION
            SELECT er2.email, 1 AS attended, er2.submitted_at AS marked_at,
                   er2.score, er2.total
            FROM exam_results er2
            WHERE er2.exam_id=?
              AND er2.email NOT IN (SELECT email FROM exam_attendance WHERE exam_id=?)
        ) combined
        LEFT JOIN users u ON u.email=combined.email
        ORDER BY combined.attended DESC, full_name ASC
    """, (exam_id, exam_id, exam_id, exam_id)).fetchall()

    rows_list = _rows_to_dicts(att)
    total     = len(rows_list)
    present   = sum(1 for r in rows_list if r.get("attended"))
    conn.close()
    return jsonify({"exam": dict(exam) if exam else {}, "attendance": rows_list,
                    "total": total, "present": present})


@staff_bp.route("/admin/staff-exams/<int:exam_id>/attendance", methods=["POST"])
def mark_attendance(exam_id):
    if not _admin_only(): return jsonify({"error": "Unauthorized"}), 403
    records = (request.json or {}).get("records", [])
    conn = get_db(); updated = 0
    for rec in records:
        email    = (rec.get("email") or "").strip()
        attended = 1 if rec.get("attended") else 0
        marks    = rec.get("marks")
        if not email: continue
        conn.execute("""
            INSERT INTO exam_attendance (exam_id, email, attended, marks, marked_at)
            VALUES (?,?,?,?,datetime('now','localtime'))
            ON CONFLICT(exam_id, email) DO UPDATE SET
                attended=excluded.attended, marks=excluded.marks,
                marked_at=excluded.marked_at
        """, (exam_id, email, attended, marks))
        updated += 1
    conn.commit(); conn.close()
    return jsonify({"status":"success","updated": updated})


# ══════════════════════════════════════════════════════════════════
# GLOBAL ANALYTICS  ← ALL COUNTS USE UNION
# ══════════════════════════════════════════════════════════════════
@staff_bp.route("/admin/analytics/staff", methods=["GET"])
def analytics_staff():
    if not _admin_only(): return jsonify({"error": "Unauthorized"}), 403
    conn = get_db(); conn.row_factory = sqlite3.Row; cursor = conn.cursor()

    staff_rows = cursor.execute("""
        SELECT s.id, s.full_name AS name, s.email,
               COALESCE(d.name,'General') AS department,
               COALESCE(s.designation,'Lecturer') AS designation,
               d.id AS department_id, u.id AS user_id,
               COUNT(DISTINCT sub.id) AS subjects_count,
               COUNT(DISTINCT se.id)  AS exams_count,
               COUNT(DISTINCT ss.student_email) AS students_count
        FROM staff s
        LEFT JOIN departments d ON d.id=s.department_id
        LEFT JOIN users u ON u.email=s.email
        LEFT JOIN subjects sub ON sub.staff_user_id=u.id
        LEFT JOIN student_subjects ss ON ss.subject_id=sub.id
        LEFT JOIN staff_exams se ON se.staff_user_id=u.id
        GROUP BY s.id ORDER BY exams_count DESC
    """).fetchall()

    staff_emails = {r["email"] for r in staff_rows}
    ph = ",".join("?" * len(staff_emails)) if staff_emails else "'__none__'"
    extra_admins = cursor.execute(f"""
        SELECT u.id, u.full_name AS name, u.email,
               COALESCE(d.name,'General') AS department,
               'Lecturer' AS designation, d.id AS department_id, u.id AS user_id,
               COUNT(DISTINCT sub.id) AS subjects_count,
               COUNT(DISTINCT se.id)  AS exams_count,
               COUNT(DISTINCT ss.student_email) AS students_count
        FROM users u
        LEFT JOIN departments d ON d.id=u.department_id
        LEFT JOIN subjects sub ON sub.staff_user_id=u.id
        LEFT JOIN student_subjects ss ON ss.subject_id=sub.id
        LEFT JOIN staff_exams se ON se.staff_user_id=u.id
        WHERE LOWER(u.role) IN ('admin','staff','staff_admin','superadmin')
          AND u.email NOT IN ({ph})
        GROUP BY u.id ORDER BY exams_count DESC
    """, list(staff_emails) if staff_emails else []).fetchall()

    total_students = cursor.execute(
        "SELECT COUNT(*) FROM users WHERE LOWER(role)='student'"
    ).fetchone()[0] or 1

    def _avg_att(uid):
        if not uid: return 0.0
        attended = cursor.execute("""
            SELECT COUNT(DISTINCT email) FROM (
                SELECT ea.email FROM staff_exams se
                JOIN exam_attendance ea ON ea.exam_id=se.id AND ea.attended=1
                WHERE se.staff_user_id=?
                UNION
                SELECT er.email FROM staff_exams se
                JOIN exam_results er ON er.exam_id=se.paper_id
                WHERE se.staff_user_id=?
            )
        """, (uid, uid)).fetchone()[0] or 0
        return round(attended / total_students * 100, 1)

    def _students_attended(uid):
        if not uid: return 0
        return cursor.execute("""
            SELECT COUNT(DISTINCT email) FROM (
                SELECT ea.email FROM staff_exams se
                JOIN exam_attendance ea ON ea.exam_id=se.id AND ea.attended=1
                WHERE se.staff_user_id=?
                UNION
                SELECT er.email FROM staff_exams se
                JOIN exam_results er ON er.exam_id=se.paper_id
                WHERE se.staff_user_id=?
            )
        """, (uid, uid)).fetchone()[0] or 0

    def _students_allotted(uid):
        if not uid: return 0
        return cursor.execute("""
            SELECT COUNT(DISTINCT email) FROM (
                SELECT er.email FROM staff_exams se
                JOIN exam_results er ON er.exam_id=se.paper_id
                WHERE se.staff_user_id=?
                UNION
                SELECT ea.email FROM staff_exams se
                JOIN exam_attendance ea ON ea.exam_id=se.id
                WHERE se.staff_user_id=?
                UNION
                SELECT ss.student_email FROM subjects sub
                JOIN student_subjects ss ON ss.subject_id=sub.id
                WHERE sub.staff_user_id=?
            )
        """, (uid, uid, uid)).fetchone()[0] or 0

    staff_list_data, seen_emails = [], set()
    for r in list(staff_rows) + list(extra_admins):
        if r["email"] in seen_emails: continue
        seen_emails.add(r["email"])
        d   = dict(r)
        uid = d.get("user_id")
        d["avg_attendance"]    = _avg_att(uid)
        d["students_attended"] = _students_attended(uid)
        d["students_count"]    = _students_allotted(uid)
        staff_list_data.append(d)

    dept_rows = cursor.execute("""
        SELECT d.name AS dept, d.id,
               COUNT(DISTINCT sub.id) AS subjects_count,
               COUNT(DISTINCT se.id)  AS exams_count,
               COUNT(DISTINCT ss.student_email) AS students
        FROM departments d
        LEFT JOIN subjects sub ON sub.department_id=d.id
        LEFT JOIN staff_exams se ON se.subject_id=sub.id
        LEFT JOIN student_subjects ss ON ss.subject_id=sub.id
        GROUP BY d.id ORDER BY exams_count DESC
    """).fetchall()

    type_rows = cursor.execute("""
        SELECT exam_type, COUNT(*) AS count,
               AVG(CASE WHEN te>0 THEN CAST(pc AS FLOAT)/te*100 ELSE NULL END) AS avg_att
        FROM (
            SELECT se.exam_type,
                   COUNT(ea.id) AS te,
                   SUM(CASE WHEN ea.attended=1 THEN 1 ELSE 0 END) AS pc
            FROM staff_exams se
            LEFT JOIN exam_attendance ea ON ea.exam_id=se.id
            GROUP BY se.id
        ) GROUP BY exam_type
    """).fetchall()

    trend_rows = cursor.execute("""
        SELECT exam_date,
               COUNT(*) AS exams_on_day,
               AVG(CASE WHEN te>0 THEN CAST(pc AS FLOAT)/te*100 ELSE NULL END) AS att_pct
        FROM (
            SELECT se.exam_date,
                   COUNT(ea.id) AS te,
                   SUM(CASE WHEN ea.attended=1 THEN 1 ELSE 0 END) AS pc
            FROM staff_exams se
            LEFT JOIN exam_attendance ea ON ea.exam_id=se.id
            GROUP BY se.id
        ) GROUP BY exam_date ORDER BY exam_date DESC LIMIT 30
    """).fetchall()

    upcoming = cursor.execute("""
        SELECT se.title, se.exam_date, se.exam_type,
               u.full_name AS staff_name, sub.name AS subject_name, d.name AS dept_name
        FROM staff_exams se
        LEFT JOIN users u ON u.id=se.staff_user_id
        LEFT JOIN subjects sub ON sub.id=se.subject_id
        LEFT JOIN departments d ON d.id=sub.department_id
        WHERE date(se.exam_date) >= date('now')
        ORDER BY se.exam_date ASC LIMIT 10
    """).fetchall()

    total_students_count = max(
        cursor.execute("SELECT COUNT(*) FROM users WHERE LOWER(role)='student'").fetchone()[0],
        cursor.execute("SELECT COUNT(*) FROM users WHERE LOWER(role)='student'").fetchone()[0]
    )

    conn.close()
    global_avg = (
        round(sum(s["avg_attendance"] for s in staff_list_data) / len(staff_list_data), 1)
        if staff_list_data else 0
    )
    return jsonify({
        "staff":            staff_list_data,
        "departments":      _rows_to_dicts(dept_rows),
        "exam_types":       _rows_to_dicts(type_rows),
        "attendance_trend": [dict(r) for r in reversed(trend_rows)],
        "upcoming_exams":   _rows_to_dicts(upcoming),
        "global_avg_att":   global_avg,
        "totals": {
            "staff":    len(staff_list_data),
            "exams":    sum(s["exams_count"] for s in staff_list_data),
            "subjects": sum(s["subjects_count"] for s in staff_list_data),
            "students": total_students_count,
        }
    })


# ══════════════════════════════════════════════════════════════════
# NOTIFICATIONS
# ══════════════════════════════════════════════════════════════════
@staff_bp.route("/admin/notifications", methods=["GET"])
def get_notifications():
    if not _admin_only(): return jsonify({"error": "Unauthorized"}), 403
    conn = get_db(); conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT n.*, u.full_name FROM staff_notifications n
        LEFT JOIN users u ON u.id=n.user_id
        ORDER BY n.created_at DESC LIMIT 30
    """).fetchall()
    unread = conn.execute(
        "SELECT COUNT(*) FROM staff_notifications WHERE is_read=0"
    ).fetchone()[0]
    conn.close()
    return jsonify({"notifications": _rows_to_dicts(rows), "unread": unread})


@staff_bp.route("/admin/notifications/read-all", methods=["POST"])
def mark_all_read():
    if not _admin_only(): return jsonify({"error": "Unauthorized"}), 403
    conn = get_db()
    conn.execute("UPDATE staff_notifications SET is_read=1")
    conn.commit(); conn.close()
    return jsonify({"status":"success"})


# ══════════════════════════════════════════════════════════════════
# EXPORTS
# ══════════════════════════════════════════════════════════════════
@staff_bp.route("/admin/export/staff-report")
def export_staff_report():
    if not _admin_only(): return redirect("/admin/login")
    conn = get_db(); conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT s.full_name, s.email, COALESCE(d.name,'General') AS department,
               COALESCE(s.designation,'Lecturer') AS designation,
               COUNT(DISTINCT sub.id) AS subjects,
               COUNT(DISTINCT se.id)  AS exams,
               COUNT(DISTINCT ss.student_email) AS students
        FROM staff s
        LEFT JOIN departments d ON d.id=s.department_id
        LEFT JOIN users u ON u.email=s.email
        LEFT JOIN subjects sub ON sub.staff_user_id=u.id
        LEFT JOIN student_subjects ss ON ss.subject_id=sub.id
        LEFT JOIN staff_exams se ON se.staff_user_id=u.id
        GROUP BY s.id ORDER BY s.full_name
    """).fetchall()
    conn.close()
    out = io.StringIO(); w = csv.writer(out)
    w.writerow(["Name","Email","Department","Designation","Subjects","Exams","Students"])
    for r in rows:
        w.writerow([r["full_name"],r["email"],r["department"],r["designation"],
                    r["subjects"],r["exams"],r["students"]])
    resp = make_response(out.getvalue())
    resp.headers["Content-Disposition"] = "attachment; filename=staff_report.csv"
    resp.headers["Content-Type"] = "text/csv"
    return resp


@staff_bp.route("/admin/export/exams-report")
def export_exams_report():
    if not _admin_only(): return redirect("/admin/login")
    conn = get_db(); conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT se.title, se.exam_date, se.exam_type,
               sub.name AS subject, u.full_name AS staff,
               d.name AS department, se.total_marks, se.venue,
               (SELECT COUNT(DISTINCT email) FROM (
                   SELECT email FROM exam_attendance WHERE exam_id=se.id
                   UNION SELECT email FROM exam_results WHERE exam_id=se.paper_id
               )) AS enrolled,
               (SELECT COUNT(DISTINCT email) FROM (
                   SELECT email FROM exam_attendance WHERE exam_id=se.id AND attended=1
                   UNION SELECT email FROM exam_results WHERE exam_id=se.paper_id
               )) AS present
        FROM staff_exams se
        LEFT JOIN subjects sub ON sub.id=se.subject_id
        LEFT JOIN departments d ON d.id=sub.department_id
        LEFT JOIN users u ON u.id=se.staff_user_id
        ORDER BY se.exam_date DESC
    """).fetchall()
    conn.close()
    out = io.StringIO(); w = csv.writer(out)
    w.writerow(["Title","Date","Type","Subject","Staff","Department",
                "Total Marks","Venue","Enrolled","Present","Attendance %"])
    for r in rows:
        att = round(r["present"]/r["enrolled"]*100,1) if r["enrolled"] else 0
        w.writerow([r["title"],r["exam_date"],r["exam_type"],
                    r["subject"] or "—",r["staff"],r["department"] or "—",
                    r["total_marks"],r["venue"] or "—",r["enrolled"],r["present"],att])
    resp = make_response(out.getvalue())
    resp.headers["Content-Disposition"] = "attachment; filename=exams_report.csv"
    resp.headers["Content-Type"] = "text/csv"
    return resp


# ══════════════════════════════════════════════════════════════════
# PAPERS
# ══════════════════════════════════════════════════════════════════
@staff_bp.route("/admin/papers/created-by/<email>", methods=["GET"])
def papers_by_staff(email):
    if not _admin_only(): return jsonify({"error": "Unauthorized"}), 403
    conn = get_db(); conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, title, subject, total_marks, duration, status,
               created_by_email, created_by_name
        FROM papers WHERE created_by_email=? ORDER BY id DESC
    """, (email,)).fetchall()
    conn.close()
    return jsonify(_rows_to_dicts(rows))


@staff_bp.route("/admin/papers/with-creators", methods=["GET"])
def papers_with_creators():
    if not _admin_only(): return jsonify({"error": "Unauthorized"}), 403
    conn = get_db(); conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT p.id, p.title, p.subject, p.total_marks, p.duration,
               p.status, p.created_by_email, p.created_by_name,
               COALESCE(d.name,'—') AS dept_name
        FROM papers p
        LEFT JOIN users u ON u.email=p.created_by_email
        LEFT JOIN departments d ON d.id=u.department_id
        ORDER BY p.id DESC
    """).fetchall()
    conn.close()
    return jsonify(_rows_to_dicts(rows))


# ══════════════════════════════════════════════════════════════════
# STUDENT → STAFF ATTENDANCE LINK
# ══════════════════════════════════════════════════════════════════
def record_student_paper_attendance(paper_id: int, student_email: str):
    if not paper_id or not student_email:
        print(f"[Attendance] Skipped — missing paper_id={paper_id!r} or email={student_email!r}")
        return
    try:
        conn = get_db(); conn.row_factory = sqlite3.Row; marked = []

        # PATH 1: paper_staff_link table
        link = conn.execute(
            "SELECT * FROM paper_staff_link WHERE paper_id=?", (int(paper_id),)
        ).fetchone()
        if link:
            print(f"[Attendance] PATH1 paper {paper_id} → staff_user_id={link['staff_user_id']}")
            exam_rows = conn.execute(
                "SELECT id FROM staff_exams WHERE paper_id=? AND staff_user_id=?",
                (int(paper_id), link["staff_user_id"])
            ).fetchall()
            for exam_row in exam_rows:
                _upsert_attendance(conn, exam_row["id"], student_email)
                marked.append(exam_row["id"])

        # PATH 2: staff_exams.paper_id direct
        if not marked:
            exam_rows = conn.execute(
                "SELECT id FROM staff_exams WHERE paper_id=?", (int(paper_id),)
            ).fetchall()
            for exam_row in exam_rows:
                print(f"[Attendance] PATH2 exam_id={exam_row['id']}")
                _upsert_attendance(conn, exam_row["id"], student_email)
                marked.append(exam_row["id"])

        # PATH 3: papers.created_by_email → users → staff_exams
        if not marked:
            paper_row = conn.execute(
                "SELECT created_by_email FROM papers WHERE id=?", (int(paper_id),)
            ).fetchone()
            if paper_row and paper_row["created_by_email"]:
                user_row = conn.execute(
                    "SELECT id FROM users WHERE email=?", (paper_row["created_by_email"],)
                ).fetchone()
                if user_row:
                    exam_rows = conn.execute(
                        "SELECT id FROM staff_exams WHERE staff_user_id=? ORDER BY id DESC LIMIT 5",
                        (user_row["id"],)
                    ).fetchall()
                    for exam_row in exam_rows:
                        print(f"[Attendance] PATH3 exam_id={exam_row['id']}")
                        _upsert_attendance(conn, exam_row["id"], student_email)
                        marked.append(exam_row["id"])

        if marked:
            conn.commit()
            print(f"[Attendance] ✅ Marked {student_email} in exam_ids={marked} for paper {paper_id}")
        else:
            print(f"[Attendance] ⚠️  No staff_exam found for paper_id={paper_id}")
        conn.close()
    except Exception as e:
        print(f"[record_student_paper_attendance] ❌ {e}")
        import traceback; traceback.print_exc()


def _upsert_attendance(conn, exam_id: int, student_email: str):
    try:
        conn.execute("""
            INSERT INTO exam_attendance (exam_id, email, attended, marked_at)
            VALUES (?,?,1,datetime('now','localtime'))
            ON CONFLICT(exam_id, email) DO UPDATE SET
                attended=1, marked_at=datetime('now','localtime')
        """, (exam_id, student_email))
    except Exception:
        try:
            updated = conn.execute("""
                UPDATE exam_attendance SET attended=1, marked_at=datetime('now','localtime')
                WHERE exam_id=? AND email=?
            """, (exam_id, student_email)).rowcount
            if updated == 0:
                conn.execute("""
                    INSERT INTO exam_attendance (exam_id, email, attended, marked_at)
                    VALUES (?,?,1,datetime('now','localtime'))
                """, (exam_id, student_email))
        except Exception as e2:
            print(f"[_upsert_attendance] fallback failed: {e2}")


@staff_bp.route("/api/student-exam-start", methods=["POST"])
def student_exam_start_hook():
    data          = request.json or {}
    paper_id      = data.get("paper_id")
    student_email = data.get("email") or session.get("email")
    if not paper_id or not student_email:
        return jsonify({"status":"skipped"})
    record_student_paper_attendance(int(paper_id), student_email)
    return jsonify({"status":"ok"})


# ══════════════════════════════════════════════════════════════════
# CLASSIFY
# ══════════════════════════════════════════════════════════════════
@staff_bp.route("/admin/staff-exams/<int:exam_id>/classify", methods=["GET"])
def classify_exam(exam_id):
    conn = get_db(); conn.row_factory = sqlite3.Row
    row  = conn.execute(
        "SELECT exam_date, start_time, end_time FROM staff_exams WHERE id=?", (exam_id,)
    ).fetchone()
    conn.close()
    if not row: return jsonify({"error":"Not found"}), 404
    cls = _classify(row["exam_date"], row["start_time"], row["end_time"])
    return jsonify({"exam_id":exam_id,"classification":cls,
                    "server_time":datetime.now().strftime("%H:%M:%S")})


# ══════════════════════════════════════════════════════════════════
# PAPER → STAFF MANUAL LINK
# ══════════════════════════════════════════════════════════════════
@staff_bp.route("/admin/papers/<int:paper_id>/link-staff", methods=["POST"])
def link_paper_to_staff(paper_id):
    if not _admin_only(): return jsonify({"error":"Unauthorized"}), 403
    data      = request.json or {}
    staff_uid = data.get("staff_user_id")
    if not staff_uid: return jsonify({"error":"staff_user_id required"}), 400
    conn      = get_db(); conn.row_factory = sqlite3.Row
    staff_row = conn.execute(
        "SELECT email, full_name FROM users WHERE id=?", (int(staff_uid),)
    ).fetchone()
    if not staff_row:
        conn.close(); return jsonify({"error":"Staff user not found"}), 404
    conn.execute("""
        INSERT INTO paper_staff_link
            (paper_id, staff_user_id, staff_email, staff_name, subject_id)
        VALUES (?,?,?,?,?)
        ON CONFLICT(paper_id) DO UPDATE SET
            staff_user_id=excluded.staff_user_id,
            staff_email=excluded.staff_email,
            staff_name=excluded.staff_name,
            subject_id=excluded.subject_id
    """, (paper_id, int(staff_uid), staff_row["email"],
          staff_row["full_name"], data.get("subject_id") or None))
    conn.execute(
        "UPDATE papers SET created_by_email=?, created_by_name=? WHERE id=?",
        (staff_row["email"], staff_row["full_name"], paper_id))
    conn.commit(); conn.close()
    return jsonify({"status":"success",
                    "message":f"Paper linked to {staff_row['full_name']}",
                    "staff_name":staff_row["full_name"]})