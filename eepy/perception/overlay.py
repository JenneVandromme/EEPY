"""Drawing the face features on the camera preview (python -m eepy.camera.preview).

Shows, live: the landmarks (L toggles them), eye and mouth openness, head
angles, the latest window figures, and a coloured flash with a label when a
blink, yawn or nod is detected. Display only: nothing here is recorded.
"""

from __future__ import annotations

import time
from typing import Any, List, Optional, Tuple

from . import landmarks as lm
from .pipeline import PerceptionResult

GREEN, YELLOW, CYAN, WHITE, BLACK, GREY = (80, 220, 80), (0, 220, 255), (255, 220, 0), \
    (255, 255, 255), (0, 0, 0), (170, 170, 170)
FLASH_SECONDS = 0.9
FLASH_COLOURS = {"blink": CYAN, "yawn": YELLOW, "nod": (255, 120, 255)}


def _text(cv2: Any, image: Any, line: str, x: int, y: int, color: Tuple[int, int, int] = WHITE,
          scale: float = 0.55) -> None:
    cv2.putText(image, line, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, BLACK, 4, cv2.LINE_AA)
    cv2.putText(image, line, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)


def _fmt(value: Optional[float], spec: str = ".2f") -> str:
    return "-" if value is None else format(value, spec)


class FaceOverlay:
    def __init__(self, aus: Any = None) -> None:
        self.aus = aus                    # the ActionUnitExtractor, if there is one
        self.show_aus = True
        self.show_landmarks = True
        self.flashes: List[Tuple[float, str, str]] = []    # (shown until, kind, label)
        self.window: Optional[dict] = None

    def key(self, key: int) -> None:
        if key in (ord("l"), ord("L")):
            self.show_landmarks = not self.show_landmarks
        if key in (ord("a"), ord("A")):
            self.show_aus = not self.show_aus

    def note(self, result: PerceptionResult) -> None:
        """Remember events and window figures (call for EVERY result, even ones not drawn)."""
        now = time.monotonic()
        for event in result.events:
            if event.type == "blink":
                label = f"BLINK {event.fields['duration_ms']:.0f} ms" + (" (long)" if event.fields.get("long") else "")
            elif event.type == "yawn":
                label = f"YAWN {event.fields['duration_s']:.1f} s"
            elif event.type == "nod":
                label = f"NOD {event.fields['depth_deg']:.0f} deg"
            else:
                label = event.type.replace("_", " ").upper()
            self.flashes.append((now + FLASH_SECONDS, event.type, label))
        if result.window is not None:
            self.window = result.window

    def draw(self, cv2: Any, image: Any, result: PerceptionResult, offset: Tuple[int, int] = (0, 0)) -> None:
        obs, v = result.observation, result.values
        height, width = image.shape[:2]
        now = time.monotonic()
        self.flashes = [f for f in self.flashes if f[0] > now]

        if obs.present and self.show_landmarks and obs.landmarks is not None:
            points = obs.points() + offset
            for x, y in points[:468:2]:                       # every other mesh point: lighter
                cv2.circle(image, (int(x), int(y)), 1, GREY, -1)
            for index in lm.RIGHT_EYE + lm.LEFT_EYE:
                cv2.circle(image, tuple(int(c) for c in points[index]), 2, GREEN, -1)
            for a, b in lm.MOUTH_VERTICAL + (lm.MOUTH_CORNERS,):
                cv2.line(image, tuple(int(c) for c in points[a]), tuple(int(c) for c in points[b]), YELLOW, 1)

        lines = [("face features   (L: landmarks on/off)", GREY)]
        if not obs.present:
            lines.append(("no face", YELLOW))
        else:
            lines += [
                (f"eyes  EAR L {_fmt(v.get('ear_left'))}  R {_fmt(v.get('ear_right'))}   "
                 f"closed {_fmt(v.get('eye_closure'), '.0%')}   blink model L {_fmt(v.get('blink_left'))} "
                 f"R {_fmt(v.get('blink_right'))}", WHITE),
                (f"mouth MAR {_fmt(v.get('mar'))}   jaw open {_fmt(v.get('jaw_open'))}", WHITE),
                (f"head  yaw {_fmt(v.get('head_yaw'), '+.0f')}  pitch {_fmt(v.get('head_pitch'), '+.0f')}"
                 f"  roll {_fmt(v.get('head_roll'), '+.0f')}  deg     face size {_fmt(v.get('face_size'), '.0%')}",
                 WHITE),
            ]
        lines.append((f"processing {_fmt(v.get('process_ms'), '.1f')} ms per frame", GREY))
        w = self.window
        if w:
            lines.append((f"last 60 s: PERCLOS {_fmt(w.get('perclos_60s'), '.0%')}  blinks/min "
                          f"{_fmt(w.get('blink_rate_60s'), '.0f')}  yawns {w.get('yawns_60s')}  nods "
                          f"{w.get('nods_60s')}  away {_fmt(w.get('look_away_ratio_60s'), '.0%')}"
                          + ("   (fps too low for blinks)" if w.get("blink_confidence_10s") == "low" else ""),
                          WHITE))
        y = height - 14 - 24 * (len(lines) - 1)
        for line, color in lines:
            _text(cv2, image, line, 12, y, color)
            y += 24

        if self.aus is not None and self.show_aus:
            self._draw_aus(cv2, image, v, width, height)

        if self.flashes:
            _, kind, _ = self.flashes[-1]
            cv2.rectangle(image, (0, 0), (width - 1, height - 1), FLASH_COLOURS.get(kind, WHITE), 10)
            for i, (_, kind, label) in enumerate(reversed(self.flashes[-3:])):
                _text(cv2, image, label, width // 2 - 110, 130 + 40 * i, FLASH_COLOURS.get(kind, WHITE), 1.1)

    def _draw_aus(self, cv2: Any, image: Any, values: dict, width: int, height: int) -> None:
        """Action units, bottom-right: one bar per AU (A toggles)."""
        from .aus import AU_DESCRIPTIONS

        aus = self.aus
        units = list(aus.units)
        x, bar_w = width - 390, 120
        y = height - 14 - 22 * (len(units) + 1)
        if aus.status != "running":
            _text(cv2, image, f"AUs {aus.status}   (A: hide)", x, height - 14, YELLOW, 0.5)
            if aus.reason:
                _text(cv2, image, aus.reason[:52], x - 200, height - 38, GREY, 0.45)
            return
        stale = values.get("au_stale")
        rate, infer = aus.achieved_rate(), aus.infer_ms()
        device = getattr(aus.detector, "device", "?")
        head = f"AUs {device}  {rate:.0f}/s  {_fmt(infer, '.0f')} ms" + ("  STALE" if stale else "")
        _text(cv2, image, head + "   (A: hide)", x, y, YELLOW if stale else GREY, 0.5)
        threshold = aus.au.active_threshold
        for i, unit in enumerate(units):
            value = values.get(f"au_{unit}")
            yy = y + 22 * (i + 1)
            cv2.rectangle(image, (x, yy - 12), (x + bar_w, yy + 2), (60, 60, 60), -1)
            if value is not None:
                fill = int(bar_w * max(0.0, min(1.0, value)))
                colour = GREEN if value >= threshold else (150, 150, 150)
                cv2.rectangle(image, (x, yy - 12), (x + fill, yy + 2), colour, -1)
                cv2.line(image, (x + int(bar_w * threshold), yy - 14), (x + int(bar_w * threshold), yy + 4),
                         WHITE, 1)
            label = f"{unit} {AU_DESCRIPTIONS.get(unit, '')}"
            _text(cv2, image, label, x + bar_w + 8, yy, WHITE if value is not None else GREY, 0.45)
