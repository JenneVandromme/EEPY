"""Pre-generated audio for every fixed line in lines.yaml.

A clip's file name is a hash of the engine, its voice, and the exact text:

    .cache/speech/3f9a1c...e2.wav      "Back to it?" in piper / en_US-lessac-medium

So the cache needs no bookkeeping to stay right: edit a wording, and its hash
changes -- that one line is generated again, everything else is reused. Switch
voices, and every line gets a new hash. After each preparation, clips whose
wording no longer exists are deleted.

Wordings with a placeholder ("{minutes} minutes") cannot be prepared -- the
number is not known until the robot says it -- so they are spoken live, like
free text. Piper takes about 30-100 ms for that.

Live clips go to .cache/speech/live/ and are cleared out as new ones arrive.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .engines import Engine
from .lines import placeholders

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / ".cache" / "speech"
KEEP_LIVE = 20          # live clips to keep around (the robot may still be playing one)


def clip_name(engine: Engine, text: str) -> str:
    digest = hashlib.sha256(f"{engine.key}\n{text}".encode("utf-8")).hexdigest()[:32]
    return f"{digest}.{engine.ext}"


def fixed_wordings(lines: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """(line_id, variant, text) for every wording with no placeholder."""
    out = []
    for line_id, line in lines.items():
        for variant in ("hedged", "direct"):
            for text in getattr(line.variants, variant):
                if not placeholders(text):
                    out.append((line_id, variant, text))
    return out


class SpeechCache:
    def __init__(self, directory: Path = CACHE_DIR) -> None:
        self.dir = Path(directory)
        self.live_dir = self.dir / "live"

    def path(self, engine: Engine, text: str) -> Path:
        return self.dir / clip_name(engine, text)

    def get(self, engine: Engine, text: str) -> Path | None:
        path = self.path(engine, text)
        return path if path.is_file() and path.stat().st_size > 0 else None

    def put(self, engine: Engine, text: str, data: bytes) -> Path:
        return _write_atomic(self.path(engine, text), data)

    def live(self, engine: Engine, data: bytes) -> Path:
        """A one-off clip (free text, filled-in placeholders)."""
        self.live_dir.mkdir(parents=True, exist_ok=True)
        path = _write_atomic(self.live_dir / f"{uuid.uuid4().hex}.{engine.ext}", data)
        clips = sorted(self.live_dir.iterdir(), key=lambda p: p.stat().st_mtime)
        for old in clips[:-KEEP_LIVE]:
            old.unlink(missing_ok=True)
        return path

    def prepare(self, engine: Engine, lines: Mapping[str, Any],
                progress: Callable[[str], None] | None = None) -> dict[str, Any]:
        """Generate every fixed wording that is missing; delete stale clips.

        Returns a report: how many were reused, generated, failed, removed.
        A wording that fails is reported, not fatal -- it is retried live.
        """
        self.dir.mkdir(parents=True, exist_ok=True)
        wanted: set[str] = set()
        report: dict[str, Any] = {"reused": 0, "generated": 0, "failed": [], "removed": 0,
                                  "seconds": 0.0, "engine": engine.label}
        started = time.monotonic()
        manifest = {}
        for line_id, variant, text in fixed_wordings(lines):
            name = clip_name(engine, text)
            wanted.add(name)
            manifest[name] = {"line_id": line_id, "variant": variant, "text": text,
                              "engine": engine.key}
            if self.get(engine, text):
                report["reused"] += 1
                continue
            try:
                self.put(engine, text, engine.synthesize(text))
                report["generated"] += 1
                if progress:
                    progress(f'{line_id} ({variant}): "{text}"')
            except Exception as exc:  # noqa: BLE001 -- reported, and the line is retried live
                report["failed"].append(f"{line_id}: {exc}")
        for path in self.dir.iterdir():
            if path.is_file() and path.suffix in (".wav", ".mp3") and path.name not in wanted:
                path.unlink(missing_ok=True)
                report["removed"] += 1
        _write_atomic(self.dir / "manifest.json",
                      json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"))
        report["seconds"] = round(time.monotonic() - started, 2)
        report["clips"] = sorted(self.dir / name for name in wanted if (self.dir / name).is_file())
        return report

    def clips(self) -> Iterable[Path]:
        return sorted(p for p in self.dir.glob("*") if p.suffix in (".wav", ".mp3"))


def _write_atomic(path: Path, data: bytes) -> Path:
    """Never leave a half-written clip: another process may be reading the cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return path
