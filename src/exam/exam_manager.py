import pandas as pd
import os
from flask import Blueprint, request, flash, redirect, url_for, render_template, session
from database.db_utils import get_db

exam_mgmt_bp = Blueprint('exam_mgmt', __name__)

@exam_mgmt_bp.route('/admin/dashboard')
def admin_home():
    # 1. Check if the user is actually an admin
    if session.get('role') != 'admin':
        flash("Unauthorized access!", "danger")
        return redirect(url_for('auth.admin_login'))

    # 2. Fetch the exams from the DB so they appear on the dashboard
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM exams")
    exams = cursor.fetchall()
    conn.close()

    # 3. Render the page
    return render_template('admin_dashboard.html', exams=exams)

@exam_mgmt_bp.route('/admin/bulk-upload/<int:exam_id>', methods=['POST'])
def bulk_upload(exam_id):
    if 'file' not in request.files:
        flash("No file part", "danger")
        return redirect(request.url)
    
    file = request.files['file']
    if file and file.filename.endswith('.csv'):
        try:
            # Using pandas for secure, structured parsing
            df = pd.read_csv(file)
            
            # Basic validation to ensure "Really Careful" security
            required_cols = ['question_text', 'option_a', 'option_b', 'option_c', 'option_d', 'correct_answer']
            if not all(col in df.columns for col in required_cols):
                flash("Invalid CSV format. Please use the system template.", "danger")
                return redirect(request.url)

            conn = get_db()
            cursor = conn.cursor()
            for _, row in df.iterrows():
                cursor.execute('''
                    INSERT INTO questions (exam_id, question_text, option_a, option_b, option_c, option_d, correct_answer)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (exam_id, row['question_text'], row['option_a'], row['option_b'], 
                      row['option_c'], row['option_d'], row['correct_answer']))
            
            conn.commit()
            conn.close()
            flash("Questions uploaded successfully!", "success")
        except Exception as e:
            flash(f"Upload failed: {str(e)}", "danger")
            
    return redirect(url_for('exam_mgmt.admin_home'))