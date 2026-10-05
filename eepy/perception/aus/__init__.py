"""Facial Action Units with Py-Feat (task A10) -- OPTIONAL.

    pip install -r requirements-aus.txt      # in .venv-vision; Python 3.11+

Without it everything else runs and the AU extractor reports itself "off".
See eepy/perception/aus/README.md.
"""

from __future__ import annotations

from .config import AUConfig, Profile, load as load_config
from .detector import AUDetector, AUUnavailable, aus_available
from .extractor import ActionUnitExtractor, AUResult

# What each AU means, in plain words (shown in the preview and the docs).
AU_DESCRIPTIONS = {
    "AU01": "inner brows raised",
    "AU02": "outer brows raised",
    "AU04": "brows lowered (frown)",
    "AU05": "upper lids raised (wide eyes)",
    "AU06": "cheeks raised",
    "AU07": "lids tightened (squint)",
    "AU09": "nose wrinkled",
    "AU10": "upper lip raised",
    "AU11": "nasolabial deepened",
    "AU12": "lip corners pulled (smile)",
    "AU14": "dimpler",
    "AU15": "lip corners down",
    "AU17": "chin raised",
    "AU20": "lips stretched",
    "AU23": "lips tightened",
    "AU24": "lips pressed",
    "AU25": "lips apart",
    "AU26": "jaw dropped",
    "AU28": "lips sucked in",
    "AU43": "eyes closed",
}

__all__ = ["ActionUnitExtractor", "AUResult", "AUConfig", "Profile", "load_config", "AUDetector",
           "AUUnavailable", "aus_available", "AU_DESCRIPTIONS"]
