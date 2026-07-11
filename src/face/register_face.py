
import os
import cv2
import base64
import numpy as np
import json
import face_recognition
import io
from flask import Blueprint, render_template, request, jsonify, session
from database.db_utils import get_db

face_bp = Blueprint("face", __name__)

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATASET_DIR = os.path.join(BASE_DIR, "dataset")
CASCADE_PATH = os.path.join(BASE_DIR, "models", "haarcascade_frontalface_default.xml")

face_detector = cv2.CascadeClassifier(CASCADE_PATH)

if not os.path.exists(DATASET_DIR):
    os.makedirs(DATASET_DIR)

@face_bp.route("/face/register-face")
def register_face():
    if "temp_user_email" not in session:
        return "Unauthorized", 401
    return render_template("register_face.html")

@face_bp.route("/save-face", methods=["POST"])
def save_face():
    if "temp_user_email" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        email = session["temp_user_email"]
        data = request.json.get("image")
        if not data:
            return jsonify({"error": "No image received"}), 400

        # Decode image for OpenCV checks
        image_data = data.split(",")[1]
        image_bytes = base64.b64decode(image_data)
        np_arr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        height, width = gray.shape

        # --- 1. QUALITY CHECKS (Your original logic) ---
        brightness = np.mean(gray)
        if brightness < 70:
            return jsonify({"error": "Lighting too low."})

        faces = face_detector.detectMultiScale(gray, 1.3, 5)
        if len(faces) == 0: return jsonify({"error": "No face detected."})
        if len(faces) > 1: return jsonify({"error": "Multiple faces detected."})

        (x, y, w, h) = faces[0]
        blur = cv2.Laplacian(gray, cv2.CV_64F).var()
        if blur < 40: return jsonify({"error": "Image too blurry."})

        # --- 2. GENERATE 128-NUMBER ENCODING (The New Part) ---
        # Convert OpenCV BGR to RGB for face_recognition
        rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        encodings = face_recognition.face_encodings(rgb_img)

        if len(encodings) == 0:
            return jsonify({"error": "Could not extract facial features. Try again."})
        
        # Convert the first face found to a JSON string
        face_encoding_json = json.dumps(encodings[0].tolist())

        # --- 3. SAVE IMAGE & UPDATE DATABASE ---
        # --- 3. SAVE IMAGE & UPDATE DATABASE ---
                # --- 3. SAVE IMAGE & UPDATE DATABASE ---
        safe_email = email.replace("@","_").replace(".","_")

        file_path = os.path.join(DATASET_DIR, f"{safe_email}.jpg")

        cv2.imwrite(file_path, img)

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute(
    "UPDATE users SET face_registered=1 WHERE email=?",
    (email,)
)

        conn.commit()
        conn.close()

        # ✅ KEEP USER SESSION
        session["email"] = email
        session["face_verified"] = False

        # remove temp session
        session.pop("temp_user_email", None)

        return jsonify({
            "success": True,
            "redirect": "/face/verify_face"
        })

    except Exception as e:
        print("Face Save Error:", e)

        return jsonify({
            "error": "Face registration failed"
        }), 500