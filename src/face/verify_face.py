from flask import Blueprint, render_template, session, redirect, url_for, flash, request, jsonify
import os
import base64
import cv2
import numpy as np
from database.db_utils import get_db
from deepface import DeepFace

face_verify_bp = Blueprint(
    "face_verify",
    __name__,
    url_prefix="/face"
)

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATASET_DIR = os.path.join(BASE_DIR, "dataset")


# ---------------- GET FACE IMAGE PATH ----------------
def get_face_image_path(email):

    safe_email = email.replace("@", "_").replace(".", "_")

    jpg_path = os.path.join(DATASET_DIR, f"{safe_email}.jpg")
    png_path = os.path.join(DATASET_DIR, f"{safe_email}.png")

    print("Checking JPG:", jpg_path)
    print("Exists JPG:", os.path.exists(jpg_path))

    print("Checking PNG:", png_path)
    print("Exists PNG:", os.path.exists(png_path))

    if os.path.exists(jpg_path):
        return jpg_path

    if os.path.exists(png_path):
        return png_path

    return None


# ---------------- OPEN FACE VERIFY PAGE ----------------
@face_verify_bp.route("/verify_face")
def verify_face_page():

    if "email" not in session:
        flash("Please login first.", "error")
        return redirect("/login")

    email = session["email"]

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT face_registered FROM users WHERE email=?",
        (email,)
    )

    user = cursor.fetchone()
    conn.close()

    if not user:
        flash("User not found.", "error")
        return redirect("/login")

    if user[0] != 1:
        flash("Face not registered.", "error")
        return redirect("/login")

    image_path = get_face_image_path(email)

    if not image_path:
        flash("Face data not found.", "error")
        return redirect("/login")

    return render_template("verify_face.html")


# ---------------- CAPTURE & VERIFY FACE ----------------
@face_verify_bp.route("/verify_face_capture", methods=["POST"])
def verify_face_capture():

    if "email" not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    email = session["email"]

    try:

        data = request.json.get("image")

        image_data = data.split(",")[1]
        image_bytes = base64.b64decode(image_data)

        np_arr = np.frombuffer(image_bytes, np.uint8)
        captured_img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

        saved_path = get_face_image_path(email)

        if not saved_path:
            return jsonify({
                "status": "error",
                "message": "Face not registered"
            })

        # Save captured image temporarily
        temp_path = os.path.join(DATASET_DIR, "temp_verify.jpg")
        cv2.imwrite(temp_path, captured_img)

        # -------- AI FACE VERIFICATION --------
        result = DeepFace.verify(
            img1_path=temp_path,
            img2_path=saved_path,
            enforce_detection=False
        )

        print("Verification result:", result)

        if result["verified"]:

            session["face_verified"] = True

            return jsonify({
                "status": "success",
                "redirect": "/dashboard"
            })

        else:
            return jsonify({
                "status": "error",
                "message": "Face not matched. Please try again."
            })

    except Exception as e:

        print("Verification Error:", e)

        return jsonify({
            "status": "error",
            "message": "Verification failed"
        })