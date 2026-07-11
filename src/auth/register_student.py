import cv2
import os
from auth.face_status import mark_face_registered

# TEMP: later replace with session value
student_email = "test@gmail.com"

base_path = "data/registered_faces"
student_path = os.path.join(base_path, student_email)
os.makedirs(student_path, exist_ok=True)

cap = cv2.VideoCapture(0)

img_count = 0
MAX_IMAGES = 2

while True:
    ret, frame = cap.read()
    if not ret:
        break

    cv2.imshow("Register Student Face", frame)
    key = cv2.waitKey(1) & 0xFF

    if key == ord('c'):
        img_count += 1
        cv2.imwrite(os.path.join(student_path, f"img{img_count}.jpg"), frame)

        if img_count == MAX_IMAGES:
            break

    elif key == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()

# ✅ UPDATE DATABASE
mark_face_registered(student_email)
print("✅ Face registered & database updated")
