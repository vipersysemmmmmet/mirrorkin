"""Local web UI. Listens on 127.0.0.1 only, so nothing outside this computer can reach it."""

import json
import os
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tiny import claude_code
from . import memory as mem
from .pipeline import load_profile, process_result, give_feedback, result_files

HOST, PORT = "127.0.0.1", 8000
PAGE = Path(__file__).with_name("ui.html")


def state() -> dict:
    latest = mem.latest_run()
    memory = mem.load()
    return {
        "has_profile": load_profile() is not None,
        "has_key": bool(os.environ.get("ANTHROPIC_API_KEY")) or claude_code.available(),
        "result_files": [p.name for p in result_files()],
        "run": latest[1] if latest else None,
        "run_date": latest[0].stem if latest else None,
        "memory": memory,
    }


def run(body: dict) -> dict:
    profile = load_profile()
    if profile is None:
        raise RuntimeError("No profile.json yet. Copy profile.example.json to profile.json and fill it in.")
    today = date.today().isoformat()
    name = body.get("file")
    if name:
        # Only files listed by result_files(), so the page can't read anything else on disk.
        match = [p for p in result_files() if p.name == name]
        if not match:
            raise RuntimeError(f"Unknown results file: {name}")
        result = json.loads(match[0].read_text())
    else:
        from .__main__ import scout_with_api
        result = scout_with_api(profile, today)
    process_result(profile, today, result)
    return state()


def feedback(body: dict) -> dict:
    give_feedback(int(body["index"]), bool(body["approved"]),
                  str(body.get("reason", "")).strip(), date.today().isoformat())
    return state()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, data: bytes, kind: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self) -> None:
        if self.path == "/":
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._json(200, state())
        else:
            self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        routes = {"/api/run": run, "/api/feedback": feedback}
        if self.path not in routes:
            return self._json(404, {"error": "Not found"})
        # Reject requests from other websites open in the browser.
        if self.headers.get("Origin") not in (None, f"http://{HOST}:{PORT}", f"http://localhost:{PORT}"):
            return self._json(403, {"error": "Forbidden"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or "{}")
            self._json(200, routes[self.path](body))
        except (RuntimeError, ValueError, KeyError, IndexError) as e:
            self._json(400, {"error": str(e)})
        except Exception as e:
            self._json(500, {"error": f"Unexpected error: {e}"})

    def log_message(self, *args) -> None:
        pass


URL = f"http://localhost:{PORT}"


def serve() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Soul Simulator Scout is running at {URL}  (Ctrl+C to stop)")
    webbrowser.open(URL)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


def serve_in_background() -> bool:
    """Start the page on a background thread. False if the port is taken (e.g. already running)."""
    import threading
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        return False
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return True
