"""The MediaPipe Face Landmarker model file: where it lives, and fetching it.

It is downloaded once (3.7 MB) into .cache/models/, which git ignores, and its
checksum is verified so a half-finished or tampered download is never used.

    python -m eepy.perception.model          # download now (else: on first use)
"""

from __future__ import annotations

import hashlib
import os
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = REPO_ROOT / ".cache" / "models"
MODEL_PATH = MODEL_DIR / "face_landmarker.task"
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
             "face_landmarker/float16/1/face_landmarker.task")
MODEL_SHA256 = "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"


class ModelError(Exception):
    """The model is missing and could not be fetched. Message is for a person."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_model(path: Path = MODEL_PATH, quiet: bool = False) -> Path:
    """The model's path, downloading it first if needed."""
    if path.is_file() and _sha256(path) == MODEL_SHA256:
        return path
    if not quiet:
        print("  Downloading the face model (3.7 MB, once)...", file=sys.stderr)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=60) as response, open(partial, "wb") as out:
            while True:
                chunk = response.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise ModelError(
            f"Could not download the face model ({exc}).\n"
            "  It is needed once; check the internet connection and try again. Or download\n"
            f"  {MODEL_URL}\n  by hand and put it at {path}") from None
    if _sha256(partial) != MODEL_SHA256:
        partial.unlink()
        raise ModelError("The downloaded face model is damaged (wrong checksum). Try again.")
    os.replace(partial, path)
    return path


if __name__ == "__main__":
    try:
        print(f"  Face model ready: {ensure_model()}")
    except ModelError as exc:
        print(f"\n  {exc}", file=sys.stderr)
        raise SystemExit(1)
