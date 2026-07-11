import cv2
import os
from deepface import DeepFace
import numpy as np
from ultralytics import YOLO


# ✅ FACE DETECTOR
face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))

MODEL_PATH = os.path.join(PROJECT_ROOT, "yolov8s.pt")

print("🚀 YOLO FINAL PATH:", MODEL_PATH)
print("✅ EXISTS:", os.path.exists(MODEL_PATH))

yolo_model = YOLO(MODEL_PATH)

def count_faces(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, 1.3, 5)
    return len(faces)


def is_fake_face(frame):

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    variance = cv2.Laplacian(gray, cv2.CV_64F).var()
    brightness = np.mean(gray)

    edges = cv2.Canny(gray, 100, 200)
    edge_density = np.mean(edges)

    print("VAR:", variance)
    print("BRIGHT:", brightness)
    print("EDGE:", edge_density)

    # 🚨 Balanced Anti-Spoof Logic

    # very blurry → likely fake
    if variance < 25:
        return True

    # extreme bright mobile glare
    if brightness > 240:
        return True

    # extremely flat texture
    if edge_density < 2:
        return True

    return False

def verify_student(student_id, captured_img):
    BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    DATASET_DIR = os.path.join(BASE_DIR, "dataset")

    safe_email = student_id.replace("@", "_").replace(".", "_")

    jpg_path = os.path.join(DATASET_DIR, f"{safe_email}.jpg")
    png_path = os.path.join(DATASET_DIR, f"{safe_email}.png")

    if os.path.exists(jpg_path):
        db_path = jpg_path

    elif os.path.exists(png_path):
        db_path = png_path

    else:
        return {
            "verified": False,
            "error": "Registered face not found"
        }

    print("USING FACE IMAGE:", db_path)

    frame = captured_img
    frames = []

    for i in range(5):
        frames.append(captured_img.copy())

    # ✅ STEP 1: MULTIPLE FACE CHECK
    face_count = count_faces(frame)

    if face_count == 0:
       # cam.release()
        return {"verified": False, "error": "No face detected"}

    if face_count > 1:
        #cam.release()
        return {
            "verified": False,
            "error": "Multiple faces detected"
        }
    if not detect_eye_movement(frame):
        return {
        "verified": False,
        "error": "Eyes not detected properly"
    }
    
    # 🚨 STEP 2.5: REAL FACE LIVENESS CHECK
    if not check_real_face(frames):

        return {
        "verified": False,
        "error": "Fake face / static image detected"
    }
    
    # 🚨 STEP 2.5: PHONE DETECTION
    # 🚨 STRICT PHONE CHECK (multiple frames)
    phone_count = 0

    for f in frames:
        if detect_phone(f):
            phone_count += 1

    if phone_count >= 1:   # detected in multiple frames
       # cam.release()
        return {
            "verified": False,
            "error": "Phone detected (spoof attempt)"
        }

    # ✅ STEP 2: FAKE / PHONE DETECTION
    fake_count = 0

    for f in frames:
        if is_fake_face(f):
            fake_count += 1

    if fake_count >= 2:
        #cam.release()
        return {
            "verified": False,
            "error": "Fake face detected (screen/photo)"
        }
    # ✅ FACE SIZE CHECK (anti far-photo attack)
    h, w, _ = frame.shape

    faces = face_cascade.detectMultiScale(
        cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
        1.3, 5
    )

    (x, y, fw, fh) = faces[0]

    face_area = fw * fh
    frame_area = w * h

    print("FACE AREA RATIO:", face_area / frame_area)

    if face_area / frame_area < 0.08:
        return {
            "verified": False,
            "error": "Move closer to camera"
        }
    
    # ✅ SAVE TEMP IMAGE
    temp_img = "temp.jpg"
    cv2.imwrite(temp_img, frame)

    #cam.release()
    #cv2.destroyAllWindows()

    # ✅ STEP 3: REAL FACE MATCHING
    try:
        result = DeepFace.verify(
    img1_path=temp_img,
    img2_path=db_path,
    model_name="VGG-Face",   # 🔥 no manual download needed
    enforce_detection=False
)
        print("DEEPFACE RESULT:", result)
        if result["verified"] and is_fake_face(frame):
             return {"verified": False, "error": "Spoof detected after verification"}

        if result["verified"]:
            return {"verified": True}

        return {
            "verified": False,
            "error": "Face not matching"
        }

    except Exception as e:
        return {
            "verified": False,
            "error": str(e)
        }
    
def check_real_face(frames):

    motions = []

    for i in range(len(frames)-1):

        diff = cv2.absdiff(frames[i], frames[i+1])

        motion = np.mean(diff)

        motions.append(motion)

    if len(motions) == 0:
        return False

    avg_motion = sum(motions) / len(motions)

    print("AVG MOTION:", avg_motion)

    # 🚨 stricter
    if avg_motion < 1.2:
        return False

    return True      # allow real users

def detect_phone(frame):
    results = yolo_model(frame, imgsz=416, conf=0.4, verbose=False)

    for r in results:
        for box in r.boxes:
            label = yolo_model.names[int(box.cls[0])]
            conf  = float(box.conf[0])

            if label == "cell phone" and conf > 0.75:
                return True

    return False    

def detect_eye_movement(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    eyes = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_eye.xml"
    )

    detected = eyes.detectMultiScale(gray, 1.3, 5)

    print("EYES DETECTED:", len(detected))

    return len(detected) >= 1