"""FaceObservation: what MediaPipe found in one frame, before any feature maths.

Every feature extractor -- A9's eye, mouth and head ones, and later Py-Feat
(A10) and gaze (A11) -- starts from this, so the face is found only once per
frame.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np

from eepy.camera.frame import Frame
from eepy.camera.region import Box

from . import landmarks as lm


@dataclass(frozen=True)
class HeadAngles:
    """Degrees, the robot's convention, for the PERSON's head:
    yaw + = turned to their left, pitch + = chin down, roll + = tipped to their left ear."""

    yaw: float
    pitch: float
    roll: float


@dataclass(frozen=True)
class FaceObservation:
    """One frame's face, or the absence of one.

    t, frame_index, source  copied from the camera Frame: `t` is on the logging
                            clock, so features line up with robot events.
    frame                   the Frame itself, for extractors that need pixels
                            (Py-Feat crops the face from frame.image) or where
                            the picture sat in the webcam (frame.box, gaze).
    present                 False when no face was found; then everything below is None/empty.
    landmarks               (478, 3): x, y normalised to frame.image (0..1), z
                            relative depth. 468-477 are the irises.
    face_box                pixel box around the landmarks, in frame.image.
    head                    head angles from the transformation matrix.
    transform               MediaPipe's 4x4 facial transformation matrix
                            (canonical face -> camera space, centimetres).
    blendshapes             all 52 MediaPipe blendshapes, 0..1, e.g.
                            "eyeBlinkLeft", "jawOpen", "eyeLookOutRight".
    process_ms              time MediaPipe took on this frame.
    """

    t: float
    frame_index: int
    source: str
    frame: Optional[Frame]
    present: bool
    landmarks: Optional[np.ndarray] = None
    face_box: Optional[Box] = None
    head: Optional[HeadAngles] = None
    transform: Optional[np.ndarray] = None
    blendshapes: Dict[str, float] = field(default_factory=dict)
    process_ms: float = 0.0
    width: int = 0
    height: int = 0

    def points(self) -> np.ndarray:
        """Landmarks in pixels of frame.image, (478, 2)."""
        assert self.landmarks is not None
        return lm.to_pixels(self.landmarks, self.width, self.height)

    def __repr__(self) -> str:  # no arrays in logs or tracebacks
        if not self.present:
            return f"FaceObservation(#{self.frame_index} no face)"
        h = self.head
        angles = f" yaw={h.yaw:.0f} pitch={h.pitch:.0f} roll={h.roll:.0f}" if h else ""
        return f"FaceObservation(#{self.frame_index} face{angles} {self.process_ms:.1f} ms)"


def absent(frame: Frame, process_ms: float = 0.0) -> FaceObservation:
    return FaceObservation(t=frame.t, frame_index=frame.index, source=frame.source, frame=frame,
                           present=False, process_ms=process_ms,
                           width=frame.width, height=frame.height)


def from_mediapipe(frame: Frame, result: object, process_ms: float) -> FaceObservation:
    """Build an observation from a FaceLandmarkerResult (first face only)."""
    faces = getattr(result, "face_landmarks", None) or []
    if not faces:
        return absent(frame, process_ms)
    marks = np.array([(p.x, p.y, p.z) for p in faces[0]], dtype=np.float32)
    shapes = {}
    for group in (getattr(result, "face_blendshapes", None) or [])[:1]:
        shapes = {c.category_name: float(c.score) for c in group}
    matrix = None
    matrices = getattr(result, "facial_transformation_matrixes", None) or []
    head = None
    if matrices:
        matrix = np.array(matrices[0], dtype=float)
        head = HeadAngles(*lm.head_angles(matrix))
    width, height = frame.width, frame.height
    return FaceObservation(
        t=frame.t, frame_index=frame.index, source=frame.source, frame=frame, present=True,
        landmarks=marks, face_box=lm.face_box(lm.to_pixels(marks, width, height)),
        head=head, transform=matrix, blendshapes=shapes, process_ms=process_ms,
        width=width, height=height)
