"""Drawing the calibration for the person: one OpenCV window that changes shape.

    face      fullscreen: the camera view (with the blue box, laptop camera), the
              face's outline and what to fix
    gaze      fullscreen black with the dots (centre, corners); a countdown for
              the notes and looking away
    baseline  a SMALL window in a corner -- they are working normally meanwhile,
              so it must not cover their screen -- with a progress bar
    done      a short summary
"""

from __future__ import annotations

import time
from typing import Any, Optional

WINDOW = "EEPY calibration  (Q: stop)"
BLUE, GREEN, WHITE, GREY, AMBER = (255, 140, 40), (80, 220, 80), (235, 235, 235), (150, 150, 150), (0, 165, 255)


class CalibrationScreen:
    def __init__(self, cv2: Any, np: Any) -> None:
        self.cv2, self.np = cv2, np
        self._mode: Optional[str] = None
        cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
        self.width, self.height = 1280, 800
        self._fullscreen()

    def _fullscreen(self) -> None:
        cv2 = self.cv2
        cv2.setWindowProperty(WINDOW, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
        cv2.waitKey(200)
        try:
            _, _, w, h = cv2.getWindowImageRect(WINDOW)
            if w >= 400 and h >= 300:
                self.width, self.height = w, h
        except Exception:  # noqa: BLE001
            pass
        self._mode = "full"

    def _small(self) -> None:
        cv2 = self.cv2
        cv2.setWindowProperty(WINDOW, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WINDOW, 460, 150)
        cv2.moveWindow(WINDOW, max(0, self.width - 480), 40)
        self._mode = "small"

    def show(self, flow: Any, result: Any) -> int:
        """Draw the current state; returns the key pressed (or -1)."""
        want = "small" if flow.stage == "baseline" else "full"
        if want != self._mode:
            self._small() if want == "small" else self._fullscreen()
        if flow.stage == "face":
            image = self._face(flow, result)
        elif flow.stage == "gaze":
            image = self._gaze(flow)
        elif flow.stage == "baseline":
            image = self._baseline(flow)
        else:
            image = self._end(flow)
        self.cv2.imshow(WINDOW, image)
        return self.cv2.waitKey(1) & 0xFF

    def close(self) -> None:
        self.cv2.destroyWindow(WINDOW)
        self.cv2.waitKey(1)

    # --- the stages -----------------------------------------------------------------------

    def _canvas(self, width: Optional[int] = None, height: Optional[int] = None) -> Any:
        return self.np.zeros((height or self.height, width or self.width, 3), dtype=self.np.uint8)

    def _text(self, image: Any, text: str, y: int, scale: float = 0.8, colour: Any = WHITE,
              centred: bool = True, x: int = 20) -> None:
        cv2 = self.cv2
        (w, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
        x = (image.shape[1] - w) // 2 if centred else x
        cv2.putText(image, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, colour, 2, cv2.LINE_AA)

    def _header(self, image: Any, flow: Any) -> None:
        self._text(image, f"Calibration {flow.stage_number()}   {flow.instruction}", 50, 0.9)
        if flow.message:
            for i, line in enumerate(_wrap(flow.message, 90)):
                self._text(image, line, 90 + 32 * i, 0.7, AMBER)

    def _face(self, flow: Any, result: Any) -> Any:
        cv2, image = self.cv2, self._canvas()
        if result is not None and result.observation.frame is not None:
            frame = result.observation.frame
            picture = frame.full if frame.full is not None else frame.image
            picture = picture.copy()
            if frame.full is not None and frame.box is not None:
                b = frame.box
                cv2.rectangle(picture, (b.x, b.y), (b.x + b.w, b.y + b.h), (255, 0, 0), 4)
                ox, oy = b.x, b.y
            else:
                ox, oy = 0, 0
            box = result.observation.face_box
            if box is not None:
                colour = (80, 220, 80) if not flow.message or "Good" in flow.message else (0, 165, 255)
                cv2.rectangle(picture, (box.x + ox, box.y + oy), (box.x + box.w + ox, box.y + box.h + oy), colour, 3)
            scale = min(self.width / picture.shape[1], (self.height - 160) / picture.shape[0])
            picture = cv2.resize(picture, (int(picture.shape[1] * scale), int(picture.shape[0] * scale)))
            x0 = (self.width - picture.shape[1]) // 2
            image[150:150 + picture.shape[0], x0:x0 + picture.shape[1]] = picture
        self._header(image, flow)
        return image

    def _gaze(self, flow: Any) -> Any:
        cv2, image = self.cv2, self._canvas()
        step = flow.gaze_step
        if step is None:
            return image
        if step.target is not None:
            x, y = int(step.target[0] * self.width), int(step.target[1] * self.height)
            colour = GREEN if flow.recording else WHITE
            if not flow.recording:
                cv2.circle(image, (x, y), 40, GREY, 2)
            cv2.circle(image, (x, y), 12, colour, -1)
            cv2.circle(image, (x, y), 3, (0, 0, 0), -1)
            self._text(image, step.instruction, self.height // 2 + (80 if step.target[1] < 0.5 else -80), 0.8)
        else:
            self._text(image, step.instruction, self.height // 2 - 40, 1.0)
            self._text(image, "recording... keep looking until the beep" if flow.recording
                       else "get ready...", self.height // 2 + 20, 0.8, GREEN if flow.recording else GREY)
        self._text(image, f"Calibration {flow.stage_number()}  gaze  {int(flow.progress * 100)} %",
                   self.height - 30, 0.6, GREY)
        if flow.message:
            self._text(image, flow.message, self.height - 70, 0.7, AMBER)
        return image

    def _baseline(self, flow: Any) -> Any:
        cv2, image = self.cv2, self._canvas(460, 150)
        self._text(image, f"Calibration {flow.stage_number()}: work as usual", 35, 0.6, WHITE, centred=False)
        cv2.rectangle(image, (20, 55), (440, 80), GREY, 1)
        cv2.rectangle(image, (20, 55), (20 + int(420 * flow.progress), 80), GREEN, -1)
        for i, line in enumerate(_wrap(flow.message, 55)[:2]):
            self._text(image, line, 110 + 22 * i, 0.45, AMBER, centred=False)
        return image

    def _end(self, flow: Any) -> Any:
        image = self._canvas()
        if flow.failed:
            self._text(image, "Calibration did not succeed", self.height // 2 - 40, 1.1, AMBER)
            for i, line in enumerate(_wrap(flow.message, 90)):
                self._text(image, line, self.height // 2 + 10 + 32 * i, 0.7, WHITE)
        else:
            self._text(image, "Calibration done -- thank you!", self.height // 2, 1.1, GREEN)
        return image

    def linger(self, flow: Any, seconds: float = 3.0) -> None:
        """Show the end screen for a moment (fullscreen again)."""
        self._fullscreen()
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.cv2.imshow(WINDOW, self._end(flow))
            if self.cv2.waitKey(50) & 0xFF in (ord("q"), 27):
                break


def _wrap(text: str, width: int) -> list:
    words, lines, line = (text or "").split(), [], ""
    for word in words:
        if len(line) + len(word) + 1 > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return lines + ([line] if line else [])
