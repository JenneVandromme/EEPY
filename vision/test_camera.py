"""
Sanity check for the vision/ environment: opens your webcam and overlays a
live face mesh using mediapipe. If you see your face traced with dots and
lines, opencv + mediapipe + your camera permissions are all working.

Usage:
    python vision/test_camera.py               # default camera (index 0)
    python vision/test_camera.py --camera 1     # pick a different camera

Press 'q' (with the video window focused) to quit.
"""

import argparse

import cv2
import mediapipe as mp

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--camera", type=int, default=0, help="camera index to open")
args = parser.parse_args()

cap = cv2.VideoCapture(args.camera)
if not cap.isOpened():
    raise SystemExit(
        f"Could not open camera {args.camera}. Is another app using it, "
        "or does this terminal/app need camera permission?"
    )

face_mesh = mp.solutions.face_mesh.FaceMesh(
    max_num_faces=1, refine_landmarks=True, min_detection_confidence=0.5
)
drawing = mp.solutions.drawing_utils
drawing_styles = mp.solutions.drawing_styles

print("Camera opened. Press 'q' in the video window to quit.")

while True:
    ok, frame = cap.read()
    if not ok:
        print("Failed to read a frame, stopping.")
        break

    results = face_mesh.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    if results.multi_face_landmarks:
        for landmarks in results.multi_face_landmarks:
            drawing.draw_landmarks(
                image=frame,
                landmark_list=landmarks,
                connections=mp.solutions.face_mesh.FACEMESH_TESSELATION,
                landmark_drawing_spec=None,
                connection_drawing_spec=drawing_styles.get_default_face_mesh_tesselation_style(),
            )

    cv2.imshow("EEPY vision test — press q to quit", frame)
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()
