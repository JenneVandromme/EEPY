"""The programs the launcher runs, and how to start, watch and stop each one.

A Part is one program (the simulator, the panel, the vision program): the command
that starts it, the port that tells whether it is up, and the last few thousand
lines it printed. Stopping sends Ctrl+C (SIGINT) to the program's whole process
group -- the panel ignores SIGTERM, and Ctrl+C is what lets it end the session
and put the robot back to neutral -- and only if it is still there after a grace
period, SIGKILL.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Deque, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
WINDOWS = os.name == "nt"
BACKENDS = ("dry", "sim", "real")
MODES = ("wizard", "rules", "hmm", "hmm_bandit")
CAMERAS = ("", "auto", "robot", "laptop", "fake")       # "": camera.source in config/eepy.yaml
DAEMON_PORT = 8000
SETTINGS_FILE = REPO / ".launcher.json"
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


@dataclass
class Settings:
    """The choices on the Developer tab. Saved in .launcher.json (not in git)."""

    backend: str = "sim"
    mode: str = "hmm"
    robot_host: str = ""                 # "": REACHY_MINI_HOST in .env, else the lab robot
    camera: str = ""
    panel_port: int = 8081               # 8080 is often taken (php on Jenne's Mac)
    live_port: int = 8770
    sim_extra: str = ""                  # extra arguments, for each program
    panel_extra: str = ""
    vision_extra: str = ""

    def update(self, values: Dict[str, object]) -> None:
        """Apply the page's changes; a bad value raises ValueError and changes nothing."""
        new = Settings(**{**asdict(self), **{k: v for k, v in values.items() if k in asdict(self)}})
        if new.backend not in BACKENDS:
            raise ValueError(f"backend must be one of {', '.join(BACKENDS)}")
        if new.mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        if new.camera not in CAMERAS:
            raise ValueError(f"camera must be one of {', '.join(c or 'default' for c in CAMERAS)}")
        for name in ("panel_port", "live_port"):
            port = int(getattr(new, name))
            if not 1024 <= port <= 65535:
                raise ValueError(f"{name} must be between 1024 and 65535")
            setattr(new, name, port)
        for name in ("sim_extra", "panel_extra", "vision_extra"):
            shlex.split(getattr(new, name))         # unbalanced quotes: ValueError
        new.robot_host = str(new.robot_host).strip()
        self.__dict__.update(asdict(new))

    @classmethod
    def load(cls, path: Path = SETTINGS_FILE) -> "Settings":
        settings = cls()
        try:
            settings.update(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            pass                                    # missing or broken: the defaults
        return settings

    def save(self, path: Path = SETTINGS_FILE) -> None:
        try:
            path.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass


def env_file_value(name: str, path: Path = REPO / ".env") -> str:
    """A value from .env (the same file the panel reads), or ""."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            key, _, value = line.strip().partition("=")
            if key.strip() == name and not line.lstrip().startswith("#"):
                return value.strip().strip("\"'")
    except OSError:
        pass
    return ""


def robot_host(settings: Settings) -> str:
    return settings.robot_host or os.environ.get("REACHY_MINI_HOST") or env_file_value("REACHY_MINI_HOST") \
        or "192.168.50.95"


def relay_port(path: Path = REPO / "config" / "eepy.yaml") -> int:
    """camera.relay_port from config/eepy.yaml (read without a YAML library): the port every
    Wizard panel holds while it runs, whatever port its page is on."""
    try:
        found = re.search(r"^\s+relay_port:\s*(\d+)", path.read_text(encoding="utf-8"), re.MULTILINE)
        return int(found.group(1)) if found else 8766
    except OSError:
        return 8766


def other_panel_message(settings: Settings) -> Optional[str]:
    """Why the panel must not start: another panel (or relay) runs already. The vision program
    would join THAT panel's session, and record into it."""
    port = relay_port()
    if port_open(port):
        return f"another Wizard panel (or camera relay) is already running -- port {port} is in use. " \
               "Stop it first (Ctrl+C in its terminal); otherwise vision records into that panel's session."
    return None


def venv_bin(venv: str, program: str) -> Path:
    if WINDOWS:
        return REPO / venv / "Scripts" / (program + ("" if program.endswith(".exe") else ".exe"))
    return REPO / venv / "bin" / program


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    """Does something answer on this port? For 127.0.0.1 also tries ::1 (see eepy/wizard/__main__.py)."""
    targets = [(socket.AF_INET, host)]
    if host == "127.0.0.1":
        targets.append((socket.AF_INET6, "::1"))
    for family, address in targets:
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.settimeout(timeout)
                if probe.connect_ex((address, port)) == 0:
                    return True
        except OSError:
            continue
    return False


# --- the commands -----------------------------------------------------------------

def sim_command(settings: Settings) -> List[str]:
    daemon = str(venv_bin(".venv-robot", "reachy-mini-daemon"))
    if sys.platform == "darwin":       # MuJoCo's window needs mjpython on macOS
        return [str(venv_bin(".venv-robot", "mjpython")), daemon, "--sim"] + shlex.split(settings.sim_extra)
    return [daemon, "--sim"] + shlex.split(settings.sim_extra)


def panel_command(settings: Settings) -> List[str]:
    cmd = [str(venv_bin(".venv-robot", "python")), "-m", "eepy.wizard", "--backend", settings.backend,
           "--mode", settings.mode, "--port", str(settings.panel_port)]
    if settings.backend == "real":
        cmd += ["--robot-host", robot_host(settings)]
    return cmd + shlex.split(settings.panel_extra)


def vision_command(settings: Settings) -> List[str]:
    cmd = [str(venv_bin(".venv-vision", "python")), "-m", "eepy.perception", "--live",
           "--live-port", str(settings.live_port), "--no-browser"]
    # The vision program's "auto" follows EEPY_BACKEND in .env, not the panel's --backend: say it.
    camera = settings.camera or ("robot" if settings.backend == "real" else "")
    if camera:
        cmd += ["--camera", camera]
    return cmd + shlex.split(settings.vision_extra)


def shell_text(cmd: List[str]) -> str:
    """The command as you would type it in a terminal in the repo folder (for the fallback)."""
    words = []
    for word in cmd:
        try:
            word = str(Path(word).relative_to(REPO))
        except ValueError:
            pass
        if word == "--no-browser":     # in a terminal you DO want the live page opened
            continue
        words.append(shlex.quote(word))
    return " ".join(words)


# --- one program ------------------------------------------------------------------

@dataclass
class LogBook:
    """Every part's output in one numbered stream, so the page can ask "what is new since n"."""

    size: int = 4000
    lines: Deque[Tuple[int, str, str]] = field(default_factory=deque)
    counter: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, part: str, text: str) -> None:
        text = _ANSI.sub("", text.rstrip("\r\n"))
        with self.lock:
            self.counter += 1
            self.lines.append((self.counter, part, text))
            while len(self.lines) > self.size:
                self.lines.popleft()

    def since(self, number: int, part: Optional[str] = None, limit: int = 1500) -> Tuple[List[list], int]:
        with self.lock:
            rows = [[n, p, t] for n, p, t in self.lines if n > number and (part is None or p == part)]
            return rows[-limit:], self.counter


class Part:
    """One program the launcher can start and stop."""

    def __init__(self, name: str, label: str, command: Callable[[Settings], List[str]],
                 port: Callable[[Settings], int], logs: LogBook, url: Optional[Callable[[Settings], str]] = None,
                 stop_grace: float = 10.0, blocked: Optional[Callable[[Settings], Optional[str]]] = None) -> None:
        self.name, self.label, self._command, self._port, self.logs = name, label, command, port, logs
        self._url, self.stop_grace, self._blocked = url, stop_grace, blocked
        self.process: Optional[subprocess.Popen] = None
        self.exit_code: Optional[int] = None
        self.started_at: Optional[float] = None
        self.stopping = False
        self.stopped_on_purpose = False
        self.lock = threading.Lock()

    def command(self, settings: Settings) -> List[str]:
        return self._command(settings)

    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def state(self, settings: Settings) -> str:
        """stopped / starting / running / stopping / crashed / external (started in a terminal)."""
        up = port_open(self._port(settings))
        if self.running():
            return "stopping" if self.stopping else ("running" if up else "starting")
        if up:
            return "external"
        if self.exit_code not in (None, 0) and not self.stopped_on_purpose:
            return "crashed"
        return "stopped"

    def status(self, settings: Settings) -> Dict[str, object]:
        cmd = self.command(settings)
        return {"label": self.label, "state": self.state(settings),
                "pid": self.process.pid if self.running() else None, "exit_code": self.exit_code,
                "port": self._port(settings), "url": self._url(settings) if self._url else None,
                "uptime": round(time.time() - self.started_at) if self.running() and self.started_at else None,
                "command": shell_text(cmd), "missing": [] if Path(cmd[0]).exists() else [shell_text(cmd[:1])]}

    def start(self, settings: Settings) -> str:
        """Start it; returns what happened, as a sentence for the page."""
        with self.lock:
            if self.running():
                return f"{self.label} is already running."
            port = self._port(settings)
            if port_open(port):
                return f"{self.label}: port {port} is already in use -- probably started in a terminal. " \
                       "Stop that one first, or leave it running."
            reason = self._blocked(settings) if self._blocked else None
            if reason:
                return f"{self.label} not started: {reason}"
            cmd = self.command(settings)
            if not Path(cmd[0]).exists():
                return f"{self.label}: {shell_text(cmd[:1])} does not exist. Set up the virtualenvs (README, step 1)."
            env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
            kwargs: Dict[str, object] = {}
            if WINDOWS:
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            else:
                kwargs["start_new_session"] = True          # its own group: Ctrl+C reaches its children too
            self.logs.add(self.name, f"$ {shell_text(cmd)}")
            try:
                self.process = subprocess.Popen(cmd, cwd=str(REPO), env=env, stdin=subprocess.DEVNULL,
                                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)
            except OSError as exc:
                self.logs.add(self.name, f"[launcher] could not start: {exc}")
                return f"{self.label} could not start: {exc}"
            self.exit_code, self.started_at = None, time.time()
            self.stopping = self.stopped_on_purpose = False
            threading.Thread(target=self._read, args=(self.process,), name=f"log-{self.name}", daemon=True).start()
            return f"{self.label} started."

    def _read(self, process: subprocess.Popen) -> None:
        assert process.stdout is not None
        for raw in iter(process.stdout.readline, b""):
            self.logs.add(self.name, raw.decode("utf-8", errors="replace"))
        code = process.wait()
        if process is self.process:
            self.exit_code = code
        self.logs.add(self.name, f"[launcher] {self.label} exited with code {code}.")

    def stop(self) -> str:
        """Ctrl+C, wait, then kill. Blocks until it is gone (at most stop_grace + a few seconds)."""
        with self.lock:
            process = self.process
            if process is None or process.poll() is not None:
                return f"{self.label} is not running (from here)."
            self.stopping = self.stopped_on_purpose = True
            self.logs.add(self.name, f"[launcher] stopping {self.label} (Ctrl+C)...")
            try:
                _signal_group(process, interrupt=True)
                try:
                    process.wait(self.stop_grace)
                except subprocess.TimeoutExpired:
                    self.logs.add(self.name, f"[launcher] still running after {self.stop_grace:.0f} s: killing it.")
                    _signal_group(process, interrupt=False)
                    process.wait(5)
            except (OSError, subprocess.TimeoutExpired) as exc:
                self.logs.add(self.name, f"[launcher] could not stop it: {exc}")
                return f"{self.label} could not be stopped: {exc}"
            finally:
                self.stopping = False
            return f"{self.label} stopped."


def _signal_group(process: subprocess.Popen, interrupt: bool) -> None:
    if WINDOWS:
        if interrupt:
            process.send_signal(signal.CTRL_BREAK_EVENT)   # type: ignore[attr-defined]
        else:
            process.kill()
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGINT if interrupt else signal.SIGKILL)
    except ProcessLookupError:
        pass


def make_parts(logs: LogBook) -> Dict[str, Part]:
    """The three programs, in the order they have to start."""
    return {
        "sim": Part("sim", "Simulator", sim_command, lambda s: DAEMON_PORT, logs, stop_grace=8),
        "panel": Part("panel", "Wizard panel", panel_command, lambda s: s.panel_port, logs,
                      url=lambda s: f"http://127.0.0.1:{s.panel_port}/", stop_grace=12, blocked=other_panel_message),
        "vision": Part("vision", "Vision + live HMM", vision_command, lambda s: s.live_port, logs,
                       url=lambda s: f"http://127.0.0.1:{s.live_port}/", stop_grace=8),
    }
