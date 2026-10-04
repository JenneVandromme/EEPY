"""Download Piper voices, visibly: with progress, resume, and a size check.

Piper's own downloader prints nothing for minutes on a slow connection -- a
60 MB voice took nine minutes on the lab's Wi-Fi during development -- which
looks exactly like a hang. This one shows progress, picks up where it left off
if interrupted, and only puts the file in place once it is complete.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Callable, Optional

from .engines import VOICES_DIR

BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

# Voices worth trying, checked to exist. About 60 MB each.
SUGGESTED = {
    "en_US-lessac-medium": "American English, neutral, clear (the default)",
    "en_US-amy-medium": "American English, female",
    "en_US-ryan-medium": "American English, male",
    "en_US-hfc_female-medium": "American English, female, warm",
    "en_US-joe-medium": "American English, male",
    "en_GB-alba-medium": "British English (Scottish), female",
    "en_GB-jenny_dioco-medium": "British English, female",
    "en_GB-northern_english_male-medium": "British English (northern), male",
    "en_GB-cori-medium": "British English, female",
    "nl_BE-nathalie-medium": "Flemish (Belgian Dutch), female",
    "nl_BE-rdh-medium": "Flemish (Belgian Dutch), male",
}


class DownloadError(Exception):
    pass


def voice_url(voice: str, suffix: str) -> str:
    """en_US-lessac-medium -> .../en/en_US/lessac/medium/en_US-lessac-medium<suffix>"""
    try:
        language, name, quality = voice.split("-")
    except ValueError:
        raise DownloadError(f'"{voice}" is not a Piper voice name (like en_US-lessac-medium).') from None
    return f"{BASE}/{language.split('_')[0]}/{language}/{name}/{quality}/{voice}{suffix}"


def downloaded(voices_dir: Path = VOICES_DIR) -> list[str]:
    return sorted(p.stem for p in Path(voices_dir).glob("*.onnx")
                  if p.with_suffix(".onnx.json").is_file())


def _fetch(url: str, target: Path, progress: Optional[Callable[[int, int], None]]) -> None:
    import requests

    part = target.with_name(target.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with requests.get(url, headers=headers, stream=True, timeout=30, allow_redirects=True) as reply:
        if reply.status_code == 404:
            raise DownloadError(f"not found: {url}")
        if reply.status_code == 416:            # we already have all of it
            part.rename(target)
            return
        reply.raise_for_status()
        if reply.status_code == 200 and have:    # the server ignored Range: start over
            have = 0
        total = have + int(reply.headers.get("content-length", 0))
        with part.open("ab" if have else "wb") as handle:
            for chunk in reply.iter_content(chunk_size=256 * 1024):
                handle.write(chunk)
                have += len(chunk)
                if progress:
                    progress(have, total)
    if total and have != total:
        raise DownloadError(f"incomplete: got {have} of {total} bytes -- run it again to resume")
    part.rename(target)


def download(voice: str, voices_dir: Path = VOICES_DIR, retries: int = 5,
             quiet: bool = False) -> Path:
    """Download one voice (model + its config). Returns the model's path."""
    voices_dir = Path(voices_dir)
    voices_dir.mkdir(parents=True, exist_ok=True)
    model = voices_dir / f"{voice}.onnx"
    config = voices_dir / f"{voice}.onnx.json"
    if model.is_file() and config.is_file():
        return model

    def show(done: int, total: int) -> None:
        if quiet or not total:
            return
        width = 30
        filled = int(width * done / total)
        sys.stdout.write(f"\r  {voice}: [{'#' * filled}{'.' * (width - filled)}] "
                         f"{done / 1048576:5.1f} / {total / 1048576:.1f} MB ")
        sys.stdout.flush()

    for attempt in range(1, retries + 1):
        try:
            if not config.is_file():
                _fetch(voice_url(voice, ".onnx.json"), config, None)
            if not model.is_file():
                _fetch(voice_url(voice, ".onnx"), model, show)
            if not quiet:
                print(f"\r  {voice}: done.{' ' * 50}")
            return model
        except DownloadError:
            raise
        except Exception as exc:  # noqa: BLE001 -- network trouble: resume and retry
            if attempt == retries:
                raise DownloadError(f"{voice}: gave up after {retries} tries ({exc}). "
                                    "Run the same command again -- it resumes.") from exc
            if not quiet:
                print(f"\n  connection trouble ({type(exc).__name__}); resuming in {2 * attempt} s...")
            time.sleep(2 * attempt)
    return model
