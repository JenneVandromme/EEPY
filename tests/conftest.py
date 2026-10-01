"""Shared test fixtures.

Tests run against temporary behaviour folders, not the real behaviors/ folder,
except for the handful that deliberately check the repo's own files.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


MINIMAL_BLOCKS = """
gaze_targets:
  user:
    pitch: -12
  notes:
    pitch: 20
blocks:
  nod_small:
    description: One small nod.
    relative: true
    keyframes:
      - head: { pitch: 8 }
        duration: 0.25
      - head: { pitch: -8 }
        duration: 0.3
  look_up:
    description: Look at the user.
    keyframes:
      - look_at: user
        duration: 0.5
"""

MINIMAL_LINES = """
lines:
  hello:
    description: A greeting.
    variants:
      hedged:
        - "Hi?"
      direct:
        - "Hi."
  with_slot:
    description: Uses a placeholder.
    variants:
      direct:
        - "In {minutes} minutes."
"""


@pytest.fixture
def behaviors_dir(tmp_path: Path) -> Path:
    """An otherwise-valid behaviours folder that tests write single files into."""
    directory = tmp_path / "behaviors"
    directory.mkdir()
    (directory / "_blocks.yaml").write_text(MINIMAL_BLOCKS)
    (directory / "lines.yaml").write_text(MINIMAL_LINES)
    return directory


@pytest.fixture
def sounds_dir(tmp_path: Path) -> Path:
    """A sounds folder holding one real (silent) wav file."""
    import struct
    import wave

    directory = tmp_path / "sounds"
    directory.mkdir()
    with wave.open(str(directory / "beep.wav"), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(22050)
        handle.writeframes(struct.pack("<100h", *([0] * 100)))
    return directory


@pytest.fixture
def write_behaviors(behaviors_dir: Path):
    """Write a behaviours YAML file into the temp folder."""

    def _write(body: str, name: str = "test.yaml") -> Path:
        path = behaviors_dir / name
        path.write_text(body)
        return path

    return _write


@pytest.fixture
def run_validator(behaviors_dir: Path, sounds_dir: Path):
    """Run the validator over the temp folders and hand back the Validator."""
    from eepy.behaviors.validate import Validator

    def _run(check_reach: bool = True) -> Validator:
        validator = Validator(
            directory=behaviors_dir, sounds=sounds_dir, check_reach=check_reach
        )
        validator.run()
        return validator

    return _run
