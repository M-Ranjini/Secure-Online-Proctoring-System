# src/database/db_utils.py — COMPLETE REPLACEMENT
# Fixes: WAL mode prevents "database is locked"
#        auto-adds missing columns so bulk upload never fails

import sqlite3
import os

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DB_PATH  = os.path.join(BASE_DIR, 'database', 'proctoring.db')


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA synchronous=NORMAL")

    # ── Auto-add missing columns so old DBs never break ──────────
    _ensure_columns(conn)
    return conn


def _ensure_columns(conn):
    """Silently add any missing columns to papers and other tables."""
    needed = [
        ("papers", "questions",    "TEXT"),
        ("papers", "status",       "TEXT DEFAULT 'draft'"),
        ("papers", "start_time",   "TEXT"),
        ("papers", "end_time",     "TEXT"),
        ("papers", "max_attempts", "INTEGER DEFAULT 1"),
    ]
    for table, col, col_type in needed:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
            conn.commit()
        except Exception:
            pass   # column already exists — ignore

