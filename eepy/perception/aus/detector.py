"""The AU model: Py-Feat's multitask network, fed with MediaPipe's face box.

Py-Feat normally finds faces itself (RetinaFace) before running its AU model.
MediaPipe (A9) has already found the face, so EEPY skips that detector: it cuts
the face out exactly the way Py-Feat's model was trained (a square around the
face, 1.2x its size, scaled to 256 px -- Py-Feat's own crop function) and runs
only the multitask model. On a MacBook GPU that is ~2.7x faster than Py-Feat's
standard path, and it loads neither RetinaFace nor the identity model.

Full precision (fp32) on every device, so AU values from the CUDA desktop, a
Mac and a CPU are comparable. Pinned to py-feat 2.1.2 (requirements-aus.txt):
the model class used here is not part of Py-Feat's documented API.
"""

from __future__ import annotations

import importlib.util
import sys
import warnings
from typing import Any, Dict, Optional, Sequence, Tuple

from eepy.camera.region import Box

from .device import choose_device, cpu_threads


class AUUnavailable(Exception):
    """AUs cannot run on this machine. The message is for a person."""


def aus_available() -> Tuple[bool, str]:
    """(True, "") when Py-Feat can be imported here, else (False, why) -- cheaply,
    without importing PyTorch."""
    if sys.version_info < (3, 11):
        return False, (f"AUs need Python 3.11 or newer; this environment has "
                       f"{sys.version_info.major}.{sys.version_info.minor}. See eepy/perception/aus/README.md.")
    for module in ("torch", "feat"):
        if importlib.util.find_spec(module) is None:
            return False, ("AUs are not installed (optional). To add them: "
                           "pip install -r requirements-aus.txt")
    return True, ""


def import_pyfeat() -> Tuple[Any, Tuple[Any, Any]]:
    """torch, then Py-Feat's model class and crop function -- in THAT order.

    torch and scikit-learn (which Py-Feat imports) each bring their own copy of
    the OpenMP library. If scikit-learn's loads first, torch's crashes the whole
    process on macOS ("OMP: Error #179 ... pthread_mutex_init failed", a
    segmentation fault). Importing torch first avoids it (tested repeatedly).
    So: never `import feat` anywhere else in EEPY -- always come through here.
    """
    # Py-Feat's dependencies warn about deprecations at import. Filters are added
    # permanently (not with warnings.catch_warnings(), which is not thread-safe:
    # this runs in the AU worker thread while MediaPipe starts in another).
    for noisy in (FutureWarning, DeprecationWarning, UserWarning):
        warnings.filterwarnings("ignore", category=noisy, module=r"(feat|timm|torchvision|torch|sklearn|skops)(\.|$)")
    try:
        import torch
        from feat.multitask.inference import MultitaskModel
        from feat.utils.image_operations import extract_face_square_pad_torch
    except OSError as exc:
        # Typically: torchcodec (which Py-Feat imports) was built for a different
        # PyTorch -- e.g. a CUDA-13 torchcodec from PyPI next to a CPU or CUDA-12.6
        # PyTorch ("libnvrtc.so.13: cannot open shared object file").
        raise AUUnavailable(
            "PyTorch and torchcodec do not match, so Py-Feat cannot load "
            f"({str(exc.__cause__ or exc)[:160]}). Install all three from the SAME "
            "PyTorch index, then the extra again -- see eepy/perception/aus/README.md, "
            "'Install'.") from None
    return torch, (MultitaskModel, extract_face_square_pad_torch)


class AUDetector:
    """The interface the extractor uses. Tests substitute a fake."""

    device: str = "cpu"
    units: Tuple[str, ...] = ()
    missing: Tuple[str, ...] = ()     # asked for, but the model does not output them

    def predict(self, image_bgr: Any, box: Box) -> Dict[str, float]:
        """AU probabilities (0-1) for the face in `box` of a BGR picture."""
        raise NotImplementedError

    def close(self) -> None:
        pass


class PyFeatDetector(AUDetector):
    def __init__(self, units: Sequence[str], device: str = "auto",
                 threads: Any = "auto") -> None:
        ok, why = aus_available()
        if not ok:
            raise AUUnavailable(why)
        torch, feat_modules = import_pyfeat()
        MultitaskModel, extract_face_square_pad_torch = feat_modules
        self._torch = torch
        self._crop = extract_face_square_pad_torch
        self.device, self.device_note = choose_device(device, torch)
        self.threads = cpu_threads(threads)
        torch.set_num_threads(self.threads)
        try:
            self._model = MultitaskModel(device=self.device, weights_path=_weights_path(),
                                         amp=False, compile=False)
        except Exception as exc:  # noqa: BLE001 -- shown to the person
            raise AUUnavailable(f"Could not load or download the AU model ({exc}). It is "
                                "downloaded once (~120 MB); check the internet connection.") from None
        names = list(self._model.au_names)
        self.units = tuple(u for u in units if u in names)
        self.missing = tuple(u for u in units if u not in names)
        self._index = [names.index(u) for u in self.units]
        self._frame_idx = torch.zeros(1, dtype=torch.long, device=self.device)
        self.precision = "fp32"

    def predict(self, image_bgr: Any, box: Box) -> Dict[str, float]:
        torch = self._torch
        with torch.inference_mode():
            frame = torch.from_numpy(image_bgr[:, :, ::-1].copy()).to(self.device)
            frame = frame.permute(2, 0, 1).unsqueeze(0).float().div_(255.0)
            corners = torch.tensor([[box.x, box.y, box.x + box.w, box.y + box.h]],
                                   dtype=torch.float32, device=self.device)
            chips, _ = self._crop(frame, corners, face_size=256, expand_bbox=1.2,
                                  frame_idx=self._frame_idx)
            probs = self._model(chips).au
        row = probs[0] if hasattr(probs, "__getitem__") else probs
        return {unit: float(row[i]) for unit, i in zip(self.units, self._index)}

    def warm_up(self, size: Tuple[int, int] = (821, 462)) -> None:
        """One throw-away pass: the first one on a GPU is slow (kernel setup)."""
        import numpy as np

        width, height = size
        self.predict(np.zeros((height, width, 3), dtype=np.uint8),
                     Box(width // 3, height // 4, width // 3, height // 2))


def _weights_path() -> str:
    """The model file: Py-Feat's cached copy when there is one (no network at all,
    so a demo works offline), else downloaded once into the same cache."""
    import logging
    import os

    override = os.environ.get("FEAT_MULTITASK_WEIGHTS")   # Py-Feat's own override
    if override:
        return override
    import_pyfeat()   # torch before Py-Feat: see import_pyfeat()
    from feat.multitask.inference import HF_REPO, HF_WEIGHTS_FILE
    from feat.utils.io import get_resource_path
    from huggingface_hub import hf_hub_download

    for noisy in ("httpx", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        return hf_hub_download(repo_id=HF_REPO, filename=HF_WEIGHTS_FILE,
                               cache_dir=get_resource_path(), local_files_only=True)
    except Exception:  # noqa: BLE001 -- not cached yet
        print("  Downloading the AU model (~120 MB, once)...", file=sys.stderr)
        return hf_hub_download(repo_id=HF_REPO, filename=HF_WEIGHTS_FILE,
                               cache_dir=get_resource_path())


def load_detector(units: Sequence[str], device: str = "auto", threads: Any = "auto",
                  warm: bool = True) -> PyFeatDetector:
    detector = PyFeatDetector(units, device, threads)
    if warm:
        detector.warm_up()
    return detector


def describe(detector: Optional[AUDetector]) -> str:
    if detector is None:
        return "off"
    return f"{detector.device}" + (f", {getattr(detector, 'threads', '?')} CPU threads"
                                   if detector.device == "cpu" else "")
