"""Which device the AU model runs on, and how many CPU threads it may use.

Pure logic on a torch-like module, so it is tested without PyTorch installed.
"""

from __future__ import annotations

import os
import shutil
from typing import Any, Optional, Tuple, Union


def choose_device(preference: str, torch: Any) -> Tuple[str, Optional[str]]:
    """(device, note). preference: auto, cuda, mps or cpu.

    auto picks CUDA, then Apple's MPS, then the CPU. A device asked for but not
    available falls back to auto, with a note saying so.
    """
    cuda = bool(torch.cuda.is_available())
    mps = bool(getattr(getattr(torch, "backends", None), "mps", None)
               and torch.backends.mps.is_available())
    available = {"cuda": cuda, "mps": mps, "cpu": True}
    note = None
    if preference != "auto":
        if available.get(preference):
            return preference, None
        note = f"{preference} was asked for but is not available here"
    for device in ("cuda", "mps", "cpu"):
        if available[device]:
            return device, note
    return "cpu", note


def cpu_threads(setting: Union[int, str], cores: Optional[int] = None) -> int:
    """PyTorch CPU threads. Py-Feat's import drops PyTorch to ONE thread, so this
    is always set explicitly. auto: half the cores, between 2 and 4 -- on a
    MacBook 4 threads beat 8 (the rest are slower efficiency cores), and
    MediaPipe needs cores of its own."""
    if setting != "auto":
        return max(1, int(setting))
    cores = cores or os.cpu_count() or 2
    return max(2, min(4, cores // 2))


def nvidia_gpu_present() -> bool:
    """Is there an NVIDIA GPU with a driver (nvidia-smi on the PATH)? Used to
    spot the common mistake: a GPU, but a CPU-only PyTorch."""
    return shutil.which("nvidia-smi") is not None


def gpu_advice(device: str, require_gpu: bool) -> Optional[str]:
    """A warning for the person, or None when all is as it should be."""
    if device in ("cuda", "mps"):
        return None
    if nvidia_gpu_present():
        return ("This computer has an NVIDIA GPU, but PyTorch cannot use it (CPU-only "
                "PyTorch?). AUs run slowly on the CPU. Fix: eepy/perception/aus/README.md, "
                "'Install'.")
    if require_gpu:
        return ("This profile expects a GPU, but none was found: AUs run on the CPU, "
                "at a much lower rate.")
    return None
