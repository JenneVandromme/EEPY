"""Start the launcher page:  python3 launch.py  (or python3 -m eepy.launcher)

    python3 launch.py                  # opens http://127.0.0.1:8090 in the browser
    python3 launch.py --port 8091      # if 8090 is taken
    python3 launch.py --no-browser

Ctrl+C here stops everything the launcher started (not what runs in other terminals).
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from .parts import DAEMON_PORT, LogBook, Part, Settings, make_parts, port_open, relay_port, robot_host

PAGE = Path(__file__).with_name("static") / "index.html"


class Launcher:
    """The parts, the settings, and "start everything in the right order"."""

    def __init__(self, settings: Optional[Settings] = None, parts: Optional[Dict[str, Part]] = None,
                 logs: Optional[LogBook] = None, save: bool = True) -> None:
        self.logs = logs or LogBook()
        self.settings = settings or Settings.load()
        self.parts = parts or make_parts(self.logs)
        self._save = save
        self.step = ""                  # what "Start all" is doing right now, for the page
        self.message = ""               # the last thing that happened
        self._sequence: Optional[threading.Thread] = None
        self._cancel = threading.Event()

    def note(self, text: str) -> str:
        self.message = text
        self.logs.add("launcher", text)
        return text

    def needed(self) -> List[str]:
        """The parts this backend uses, in start order: no simulator for dry or real."""
        return [name for name in self.parts if name != "sim" or self.settings.backend == "sim"]

    def status(self) -> Dict[str, object]:
        s = self.settings
        robot = None
        if s.backend == "real":
            host = robot_host(s)
            robot = {"host": host, "reachable": port_open(DAEMON_PORT, host, timeout=0.5)}
        # A panel we did not start (a terminal, or another port): vision would join its session.
        other_panel = not self.parts["panel"].running() and port_open(relay_port())
        return {"settings": s.__dict__, "other_panel": other_panel, "robot_host": robot_host(s), "robot": robot,
                "needed": self.needed(),
                "parts": {name: part.status(s) for name, part in self.parts.items()},
                "busy": self.busy(), "step": self.step, "message": self.message}

    def busy(self) -> bool:
        return self._sequence is not None and self._sequence.is_alive()

    def update_settings(self, values: Dict[str, object]) -> str:
        self.settings.update(values)
        if self._save:
            self.settings.save()
        running = [p.label for p in self.parts.values() if p.running()]
        later = f" Restart {', '.join(running)} to use them." if running else ""
        return self.note(f"Settings saved.{later}")

    def start(self, name: str) -> str:
        return self.note(self.parts[name].start(self.settings))

    def stop(self, name: str) -> str:
        return self.note(self.parts[name].stop())

    def restart(self, name: str) -> str:
        self.parts[name].stop()
        return self.start(name)

    # -- everything at once, in the background (the daemon takes a while) --

    def start_all(self, wait_timeout: float = 90.0) -> str:
        if self.busy():
            return self.note("Already busy starting or stopping.")
        self._cancel.clear()
        self._sequence = threading.Thread(target=self._start_all, args=(wait_timeout,), daemon=True)
        self._sequence.start()
        return self.note("Starting everything...")

    def _start_all(self, wait_timeout: float) -> None:
        try:
            for name in self.needed():
                part = self.parts[name]
                state = part.state(self.settings)
                if state in ("running", "external"):
                    self.note(f"{part.label}: already {'running' if state == 'running' else 'running in a terminal'}.")
                    continue
                if not part.running():
                    self.step = f"Starting {part.label}..."
                    result = self.note(part.start(self.settings))
                    if not part.running():
                        self.note(f"Stopped: {result}")
                        return
                self.step = f"Waiting for {part.label} to come up..."
                if not self._wait_up(part, wait_timeout):
                    return
            self.note("Everything is running.")
        finally:
            self.step = ""

    def _wait_up(self, part: Part, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._cancel.is_set():
                self.note("Start-up cancelled.")
                return False
            state = part.state(self.settings)
            if state in ("running", "external"):
                return True
            if not part.running():
                self.note(f"{part.label} stopped during start-up (see its log). The rest was not started.")
                return False
            time.sleep(0.5)
        self.note(f"{part.label} did not come up within {timeout:.0f} s (see its log). The rest was not started.")
        return False

    def stop_all(self) -> str:
        """Last-started first: vision, then the panel (ends the session), then the simulator."""
        self._cancel.set()
        if self._sequence is not None:
            self._sequence.join(5)
        self.step = "Stopping everything..."
        try:
            stopped = [self.parts[name].stop() for name in reversed(list(self.parts)) if self.parts[name].running()]
        finally:
            self.step = ""
        return self.note("Stopped everything the launcher started." if stopped else "Nothing to stop (from here).")


def _handler(launcher: Launcher):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:   # quiet: the page polls every second
            pass

        def _send(self, code: int, body: bytes, kind: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data: object, code: int = 200) -> None:
            self._send(code, json.dumps(data).encode("utf-8"))

        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path == "/":
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/status":
                self._json(launcher.status())
            elif url.path == "/api/rewards/sessions":
                from . import rewards
                self._json({"sessions": rewards.sessions()})
            elif url.path == "/api/rewards/live":
                from . import rewards
                self._json(rewards.live())
            elif url.path == "/api/logs":
                query = parse_qs(url.query)
                since = int(query.get("since", ["0"])[0] or 0)
                rows, last = launcher.logs.since(since, query.get("part", [None])[0] or None)
                self._json({"lines": rows, "last": last})
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
                part = body.get("part")
                if path in ("/api/start", "/api/stop", "/api/restart") and part not in launcher.parts:
                    raise ValueError(f"unknown part: {part!r}")
                if path == "/api/start":
                    message = launcher.start(part)
                elif path == "/api/stop":
                    message = launcher.stop(part)
                elif path == "/api/restart":
                    message = launcher.restart(part)
                elif path == "/api/start-all":
                    message = launcher.start_all()
                elif path == "/api/stop-all":
                    message = launcher.stop_all()
                elif path == "/api/settings":
                    message = launcher.update_settings(body)
                elif path == "/api/rewards/run":           # A22: the reward tool, report back as JSON
                    from . import rewards
                    self._json(rewards.run(body))
                    return
                else:
                    self._json({"error": "not found"}, 404)
                    return
                self._json({"ok": True, "message": message})
            except (ValueError, TypeError) as exc:
                self._json({"ok": False, "message": str(exc)}, 400)

    return Handler


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 launch.py", description="EEPY's launcher page.")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--no-browser", action="store_true", help="do not open the page in the browser")
    args = parser.parse_args(argv)

    launcher = Launcher()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), _handler(launcher))
    except OSError as exc:
        print(f"\n  Cannot start: port {args.port} is busy ({exc}). Try --port {args.port + 1}.", file=sys.stderr)
        return 2
    server.daemon_threads = True
    url = f"http://127.0.0.1:{args.port}/"
    print(f"\n  EEPY launcher: {url}\n  Ctrl+C here stops everything it started.\n")
    launcher.note(f"Launcher ready on {url}")
    if not args.no_browser:
        webbrowser.open(url)

    def stop(*_: object) -> None:
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("  Stopping everything the launcher started...")
    finally:
        server.server_close()
        launcher.stop_all()
        print("  Launcher stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
