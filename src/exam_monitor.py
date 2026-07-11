import cv2
import mediapipe as mp
from mediapipe.python.solutions import face_detection as mp_face_logic
from mediapipe.python.solutions import face_mesh as mp_mesh_logic
from ultralytics import YOLO
import base64
import numpy as np
import os
import face_recognition   # pip install face-recognition

# ── FRAME COUNTERS ──
face_missing_frames  = 0
head_movement_frames = 0
phone_frames         = 0
multiface_frames     = 0

# ── LOAD MODELS ──
face_detector = mp_face_logic.FaceDetection(model_selection=0, min_detection_confidence=0.6)
face_mesh = mp_mesh_logic.FaceMesh(
    max_num_faces=2,
    refine_landmarks=True,
    min_detection_confidence=0.6,
    min_tracking_confidence=0.6
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
yolo_model = YOLO(os.path.join(BASE_DIR, "..", "yolov8s.pt"))

# ── DATASET DIR — registered face encodings ──
DATASET_DIR = os.path.join(BASE_DIR, "..", "dataset", "faces")

# ── IDENTITY STATE ──
# Cached registered encoding per email so we only load from disk once per session
_registered_encodings = {}   # { email: np.array or None }
_identity_check_frame = 0    # run identity check every N analyze_frame calls
IDENTITY_CHECK_EVERY  = 5    # check identity every 5th frame (~4s at 800ms interval)
_identity_fail_frames = 0    # consecutive identity failures before flagging
IDENTITY_FAIL_THRESH  = 3    # 3 consecutive fails = confirmed mismatch

# Current session email (set by app.py calling set_session_email)
_session_email = None

def set_session_email(email: str):
    """Called by app.py when a student starts an exam session."""
    global _session_email, _identity_fail_frames
    _session_email = email
    _identity_fail_frames = 0
    _load_registered_encoding(email)

def _load_registered_encoding(email: str):
    """Load and cache the registered face encoding for this student."""
    global _registered_encodings
    if email in _registered_encodings:
        return  # already cached
    npy_path = os.path.join(DATASET_DIR, f"{email}.npy")
    if os.path.exists(npy_path):
        try:
            _registered_encodings[email] = np.load(npy_path)
            print(f"[Identity] Loaded registered encoding for {email}")
        except Exception as e:
            print(f"[Identity] Failed to load encoding for {email}: {e}")
            _registered_encodings[email] = None
    else:
        print(f"[Identity] No registered face found for {email} at {npy_path}")
        _registered_encodings[email] = None

# ── SHARED STATE ──
ai_status_data = {
    "liveness":       True,
    "face":           True,
    "head":           False,
    "phone":          False,
    "gadget":         False,
    "deepfake":       False,
    "multiple_faces": False,
    "match":          None,   # NEW: True/False/None for identity check
    "reason":         None,   # NEW: reason string if mismatch
}

# ── LANDMARK INDICES ──
LEFT_EYE  = [33, 160, 158, 133, 153, 144]
RIGHT_EYE = [362, 385, 387, 263, 373, 380]
EAR_THRESH = 0.20


def _decode(image_data):
    try:
        if "," in image_data:
            _, encoded = image_data.split(",", 1)
        else:
            encoded = image_data
        nparr = np.frombuffer(base64.b64decode(encoded), np.uint8)
        return cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    except:
        return None


def _head_pose(lm, w, h):
    nose  = lm[1];   l_eye = lm[33];  r_eye = lm[263]
    chin  = lm[152]; fore  = lm[10]
    emx   = (l_eye.x + r_eye.x) / 2
    emy   = (l_eye.y + r_eye.y) / 2
    yaw   = (nose.x - emx) * 180.0
    fh    = abs(chin.y - fore.y)
    pitch = ((nose.y - emy) / fh - 0.35) * 180.0 if fh > 0 else 0.0
    return float(yaw), float(pitch)


def _ear(lm, idx):
    def d(a, b): return np.hypot(lm[a].x - lm[b].x, lm[a].y - lm[b].y)
    C = d(idx[0], idx[3])
    return (d(idx[1], idx[5]) + d(idx[2], idx[4])) / (2.0 * C) if C > 0 else 1.0


def _gaze(lm):
    if len(lm) < 478:
        return "center"
    li = lm[468]; ri = lm[473]
    ll = lm[33];  lr = lm[133]
    rl = lm[362]; rr = lm[263]
    lw = abs(lr.x - ll.x); rw = abs(rr.x - rl.x)
    if lw > 0 and rw > 0:
        avg = ((li.x - ll.x)/lw + (ri.x - rl.x)/rw) / 2
        if avg < 0.28: return "left"
        if avg > 0.72: return "right"
    et = lm[159]; eb = lm[145]
    eh = abs(eb.y - et.y)
    if eh > 0 and (li.y - et.y) / eh > 0.82: return "down"
    return "center"


def _check_identity(rgb_frame, face_locations) -> dict:
    """
    Compare the live face against the registered encoding.
    Returns {"match": bool, "reason": str or None}

    match=True  → same person
    match=False → different person (mismatch)
    match=None  → cannot determine (no registered face, no live encoding)
    """
    global _identity_fail_frames, _session_email

    email = _session_email
    if not email:
        return {"match": None, "reason": "No session email set"}

    registered = _registered_encodings.get(email)
    if registered is None:
        return {"match": None, "reason": "No registered face on file"}

    if not face_locations:
        return {"match": None, "reason": "No face to compare"}

    try:
        live_encodings = face_recognition.face_encodings(rgb_frame, face_locations)
        if not live_encodings:
            return {"match": None, "reason": "Could not encode live face"}

        live_enc = live_encodings[0]
        # Compare with tolerance 0.55 (stricter than default 0.6 — fewer false positives)
        results   = face_recognition.compare_faces([registered], live_enc, tolerance=0.55)
        distance  = face_recognition.face_distance([registered], live_enc)[0]

        if results[0]:
            # Match — reset fail counter
            _identity_fail_frames = 0
            return {"match": True, "reason": None}
        else:
            # Mismatch — increment fail counter
            _identity_fail_frames += 1
            if _identity_fail_frames >= IDENTITY_FAIL_THRESH:
                # Confirmed mismatch after N consecutive failures
                return {
                    "match": False,
                    "reason": "Identity mismatch",
                    "distance": round(float(distance), 3)
                }
            else:
                # Not enough consecutive fails yet — treat as uncertain
                return {"match": None, "reason": f"Checking ({_identity_fail_frames}/{IDENTITY_FAIL_THRESH})"}
    except Exception as e:
        print(f"[Identity] Comparison error: {e}")
        return {"match": None, "reason": f"Error: {str(e)[:40]}"}


def analyze_frame(image_data: str, email: str = None) -> dict:
    """
    Main proctoring function. Analyzes one webcam frame.
    Now accepts optional email param — if provided, updates session email.
    Returns dict with all status flags + match/reason for identity.
    """
    global face_missing_frames, head_movement_frames, phone_frames, multiface_frames
    global _identity_check_frame

    # Update session email if provided (app.py should call this)
    if email and email != _session_email:
        set_session_email(email)

    result = {
        "liveness": True, "face": False, "head": False,
        "phone": False, "gadget": False, "deepfake": False,
        "multiple_faces": False, "violation": None,
        "match": None, "reason": None,  # identity fields
    }

    frame = _decode(image_data)
    if frame is None:
        return result

    rgb  = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h, w = frame.shape[:2]

    # ── 1. LIVENESS ──
    if gray.mean() < 25:
        result["liveness"]  = False
        result["violation"] = "Camera Covered"
        ai_status_data.update(result)
        return result

    # ── 2. FACE COUNT ──
    fd = face_detector.process(rgb)
    face_locations_for_identity = []

    if fd.detections:
        n = len(fd.detections)
        result["face"]       = True
        face_missing_frames  = 0

        if n >= 2:
            multiface_frames += 1
            if multiface_frames >= 5:
                result["multiple_faces"] = True
                result["violation"]      = "Multiple Faces Detected"
        else:
            multiface_frames = max(0, multiface_frames - 1)

        # Convert mediapipe face detections to face_recognition location format
        # for identity verification
        for detection in fd.detections:
            bbox = detection.location_data.relative_bounding_box
            top    = max(0, int(bbox.ymin * h))
            right  = min(w, int((bbox.xmin + bbox.width) * w))
            bottom = min(h, int((bbox.ymin + bbox.height) * h))
            left   = max(0, int(bbox.xmin * w))
            face_locations_for_identity.append((top, right, bottom, left))

    else:
        face_missing_frames += 1
        result["face"] = face_missing_frames < 6
        if not result["face"]:
            result["violation"] = "Face Not Detected"

    # ── 3. IDENTITY CHECK (every Nth frame to save CPU) ──
    _identity_check_frame += 1
    if result["face"] and not result["multiple_faces"] and _identity_check_frame % IDENTITY_CHECK_EVERY == 0:
        identity_result = _check_identity(rgb, face_locations_for_identity[:1])  # check first face only
        result["match"]  = identity_result["match"]
        result["reason"] = identity_result["reason"]
        if result["match"] is False:
            result["violation"] = result["violation"] or "Identity Mismatch"
            print(f"[Identity] ⚠ MISMATCH detected for {_session_email}")
    elif not result["face"]:
        result["match"]  = None
        result["reason"] = "No face visible"

    # ── 4. MESH: HEAD POSE + GAZE + EYE ──
    if result["face"]:
        mr = face_mesh.process(rgb)
        if mr.multi_face_landmarks:
            lm = mr.multi_face_landmarks[0].landmark

            # Head pose
            yaw, pitch = _head_pose(lm, w, h)
            if abs(yaw) > 30 or pitch > 25:
                head_movement_frames += 1
                if head_movement_frames >= 8:
                    result["head"]      = True
                    direction = "Right" if yaw > 0 else "Left" if yaw < -30 else "Down"
                    result["violation"] = result["violation"] or f"Head Turned {direction}"
            else:
                head_movement_frames = max(0, head_movement_frames - 1)

            # Gaze
            g = _gaze(lm)
            if g != "center":
                gf = getattr(analyze_frame, "_gf", 0) + 1
                analyze_frame._gf = gf
                if gf >= 12:
                    result["violation"] = result["violation"] or (
                        "Looking Left"  if g == "left"  else
                        "Looking Right" if g == "right" else
                        "Looking Down (Phone)"
                    )
            else:
                analyze_frame._gf = max(0, getattr(analyze_frame, "_gf", 0) - 1)

            # Eye closed
            avg_ear = (_ear(lm, LEFT_EYE) + _ear(lm, RIGHT_EYE)) / 2
            if avg_ear < EAR_THRESH:
                cf = getattr(analyze_frame, "_cf", 0) + 1
                analyze_frame._cf = cf
                if cf >= 20:
                    result["violation"] = result["violation"] or "Eyes Closed"
            else:
                analyze_frame._cf = max(0, getattr(analyze_frame, "_cf", 0) - 1)

    # ── 5. YOLO — every 5th frame ──
    fc = getattr(analyze_frame, "_fc", 0) + 1
    analyze_frame._fc = fc

    if fc % 5 == 0:
        try:
            yolo_res = yolo_model(frame, imgsz=416, conf=0.45, verbose=False)
            phone_now = False; gadget_now = False
            for r in yolo_res:
                for box in r.boxes:
                    label = yolo_model.names[int(box.cls[0])]
                    conf  = float(box.conf[0])
                    if label == "cell phone" and conf >= 0.50:
                        phone_now = True
                    if label in ["laptop","keyboard","mouse","tablet","headphones"] and conf >= 0.45:
                        gadget_now = True

            if phone_now:
                phone_frames += 1
                if phone_frames >= 3:
                    result["phone"]     = True
                    result["violation"] = result["violation"] or "Phone Detected"
            else:
                phone_frames = max(0, phone_frames - 1)

            result["gadget"] = gadget_now
            if gadget_now:
                result["violation"] = result["violation"] or "Electronic Device Detected"

        except Exception as e:
            print(f"YOLO error: {e}")

    ai_status_data.update(result)
    return result


def get_ai_status():
    return dict(ai_status_data)

