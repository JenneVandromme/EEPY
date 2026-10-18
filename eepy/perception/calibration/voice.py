"""Speaking the calibration's instructions.

With the Wizard panel running, a line ID goes to the panel (camera relay, SAY),
which speaks it like any other line: the robot's voice, the designers' words
from behaviors/lines.yaml. Without a panel, this computer's own voice says the
line's first wording (macOS `say`, Windows speech, Linux espeak), if there is
one. The words are always on screen too, so nothing depends on sound.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

LINES_PATH = Path(__file__).resolve().parents[3] / "behaviors" / "lines.yaml"


def line_text(line_id: str, path: Path = LINES_PATH) -> Optional[str]:
    """The first hedged wording of a line (without pydantic: the vision environment)."""
    try:
        import yaml

        lines: Dict[str, Any] = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("lines", {})
        variants = lines[line_id]["variants"]
        options = variants.get("hedged") or variants.get("direct") or []
        return str(options[0]) if options else None
    except Exception:  # noqa: BLE001 -- a missing line must never stop a calibration
        return None


class Voice:
    def __init__(self, relay: Any = None, local: bool = True) -> None:
        self.relay = relay              # a RelayClient, or None
        self.local = local
        self._process: Optional[subprocess.Popen] = None
        self.last: str = ""

    def say(self, line_id: str) -> str:
        """Speak a line; returns how ("panel", "computer", or "text only")."""
        self.last = line_id
        if self.relay is not None:
            try:
                if self.relay.say(line_id):
                    return "panel"
            except Exception:  # noqa: BLE001 -- no panel: fall through to this computer
                pass
        text = line_text(line_id)
        if self.local and text and self._speak_here(text):
            return "computer"
        return "text only"

    def _speak_here(self, text: str) -> bool:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()                  # newer instruction wins
        if sys.platform == "darwin" and shutil.which("say"):
            cmd = ["say", text]
        elif sys.platform.startswith("win"):
            escaped = text.replace("'", "''")
            cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                   "Add-Type -AssemblyName System.Speech; "
                   f"(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('{escaped}')"]
        elif shutil.which("espeak-ng") or shutil.which("espeak"):
            cmd = [shutil.which("espeak-ng") or shutil.which("espeak"), text]
        else:
            return False
        try:
            self._process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError:
            return False

    def close(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
