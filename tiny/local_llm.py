"""A free brain that runs on this Mac (Ollama), so tiny you keeps working without Claude credit.

Much less capable than Claude and offline: it can't browse, so it only gets jobs it can do
with what's put in front of it (chat, learning, trading decisions, judging, reading photos).
Anything that needs her browser or coding sandbox waits for Claude instead of letting a small
model guess at actions it can't really take.
"""

import base64
import json
import os
import urllib.error
import urllib.request

HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
MODEL = os.environ.get("TINY_LOCAL_MODEL", "gemma3:4b")
OFFLINE_NOTE = ("\n\n(You're running on the free local brain: you can't search the web or open pages "
                "right now. Work only from the information given here, and say so if you'd need to look "
                "something up.)")


class LocalError(Exception):
    pass


def available() -> bool:
    try:
        with urllib.request.urlopen(f"{HOST}/api/tags", timeout=3) as r:
            return any(m.get("name", "").startswith(MODEL) for m in json.load(r).get("models", []))
    except (urllib.error.URLError, OSError, ValueError):
        return False


def ask(prompt: str, system: str, *, image: bytes | None = None, schema: dict | None = None,
        offline_note: bool = False, timeout: int = 600):
    """One request. Returns text, or a dict when `schema` is given (same as claude_code.ask)."""
    user = {"role": "user", "content": prompt + (OFFLINE_NOTE if offline_note else "")}
    if image:
        user["images"] = [base64.standard_b64encode(image).decode()]
    body = {"model": MODEL, "stream": False, "messages": [{"role": "system", "content": system}, user],
            "options": {"temperature": 0.4, "num_ctx": 8192}}
    if schema:
        body["format"] = schema  # Ollama constrains the output to this JSON schema
    req = urllib.request.Request(f"{HOST}/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = json.load(r)["message"]["content"].strip()
    except (urllib.error.URLError, OSError) as e:
        raise LocalError(f"The free local brain isn't running (start Ollama): {e}")
    if not schema:
        return text
    try:
        result = json.loads(text)
    except ValueError:
        raise LocalError("The local brain didn't return the expected format.")
    if not isinstance(result, dict):
        raise LocalError("The local brain didn't return the expected format.")
    return result
