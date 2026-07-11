import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "database", "proctoring.db")

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# DROP old broken table if exists
cursor.execute("DROP TABLE IF EXISTS login_logs")

# CREATE correct table
cursor.execute("""
CREATE TABLE login_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    status TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")

# ── Step 1: Add this migration to your init_db.py or run once ──

DEPT_MIGRATION_SQL = """
-- Departments table
CREATE TABLE IF NOT EXISTS departments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    code TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

-- Staff table
CREATE TABLE IF NOT EXISTS staff (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    department_id INTEGER REFERENCES departments(id),
    designation TEXT DEFAULT 'Lecturer',
    phone TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

-- Staff subjects table (one staff can handle multiple subjects)
CREATE TABLE IF NOT EXISTS staff_subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    staff_id INTEGER REFERENCES staff(id) ON DELETE CASCADE,
    subject TEXT NOT NULL,
    paper_id INTEGER REFERENCES papers(id) ON DELETE SET NULL,
    UNIQUE(staff_id, subject)
);

-- Link students to departments
ALTER TABLE users ADD COLUMN department_id INTEGER REFERENCES departments(id);

-- Exam attendance (who took which exam)
CREATE TABLE IF NOT EXISTS exam_attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id INTEGER REFERENCES papers(id),
    email TEXT NOT NULL,
    started_at TEXT DEFAULT (datetime('now','localtime')),
    submitted_at TEXT,
    score INTEGER,
    total INTEGER,
    UNIQUE(exam_id, email)
);

-- Insert sample departments
INSERT OR IGNORE INTO departments (name, code, description) VALUES
    ('Computer Science', 'CS', 'Software, AI, and Computing'),
    ('Electronics', 'EC', 'Electronics and Communication'),
    ('Mechanical', 'ME', 'Mechanical Engineering'),
    ('Civil', 'CE', 'Civil and Structural Engineering');
"""


conn.commit()
conn.close()

print("✅ login_logs table fixed successfully")

if __name__ == "__main__":
    ()