"""The desktop app's link to the Soul Simulator server: login token and API calls."""

import json
import os
import urllib.error
import urllib.request

from . import life

ACCOUNT_FILE = life.DATA / "account.json"
DEFAULT_SERVER = "http://127.0.0.1:8100"


class ServerError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def load() -> dict:
    return json.loads(ACCOUNT_FILE.read_text()) if ACCOUNT_FILE.exists() else {}


def save(account: dict) -> None:
    life.DATA.mkdir(exist_ok=True)
    ACCOUNT_FILE.write_text(json.dumps(account, indent=2))
    os.chmod(ACCOUNT_FILE, 0o600)  # the token is a password; keep it private to this user


def logged_in() -> bool:
    return bool(load().get("token"))


def server_url() -> str:
    return load().get("server") or os.environ.get("SOUL_SERVER", DEFAULT_SERVER)


def call(method: str, path: str, body: dict | None = None, server: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    token = load().get("token")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request((server or server_url()) + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            message = json.loads(e.read()).get("error", str(e))
        except ValueError:
            message = str(e)
        raise ServerError(message, e.code)
    except OSError:
        raise ServerError("Can't reach the Soul Simulator server. Is it running?")


def sign_in(server: str, email: str, password: str, new: bool) -> dict:
    result = call("POST", "/signup" if new else "/login", {"email": email, "password": password}, server)
    save({"server": server, "email": email.strip().lower(), "token": result["token"]})
    return call("GET", "/me")


def sign_out() -> None:
    try:
        call("POST", "/logout", {})
    except ServerError:
        pass
    save({"server": server_url()})
