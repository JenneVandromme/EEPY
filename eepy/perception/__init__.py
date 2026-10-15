"""Face features from the camera (task A9): MediaPipe Face Landmarker, then
plug-in extractors for eyes, mouth, head and presence.

    from eepy.camera import open_camera
    from eepy.perception import FacePipeline

    pipeline = FacePipeline(open_camera()).start()
    result = pipeline.wait_for_result()
    result.values["ear"], result.values["head_pitch"], result.events, result.window

See eepy/perception/README.md for what every value means.
"""

from __future__ import annotations

from .baseline import Baseline, BaselineProvider
from .config import PerceptionConfig, load as load_config
from .extractors import (EyeExtractor, Extractor, FaceEvent, HeadExtractor, MouthExtractor,
                         Output, PresenceExtractor, default_extractors)
from .observation import FaceObservation, HeadAngles
from .pipeline import FacePipeline, PerceptionError, PerceptionResult

__all__ = ["FacePipeline", "PerceptionResult", "PerceptionError", "FaceObservation", "HeadAngles",
           "Extractor", "Output", "FaceEvent", "PresenceExtractor", "EyeExtractor", "MouthExtractor",
           "HeadExtractor", "default_extractors", "Baseline", "BaselineProvider",
           "PerceptionConfig", "load_config"]
