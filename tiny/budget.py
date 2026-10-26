"""Saver mode: the same abilities on less Claude usage.

On by default. It picks smaller models for light work, loads tools only when a message needs
them, learns from chats in batches, and spaces out Autopilot rounds and screen glances.
"""

import json
import re

from . import life

SETTINGS_FILE = life.DATA / "budget.json"

AUTOPILOT_EVERY_H = 3        # hours between Autopilot rounds in saver mode (1 otherwise)
WATCH_EVERY_S = 180          # seconds between screen glances in saver mode (60 otherwise)
LEARN_EVERY = 4              # learn from chats every Nth message in saver mode (every message otherwise)

# Messages that need her browser, inbox or wallets (otherwise chat runs without loading them)
NEEDS_TOOLS = re.compile(
    r"\b(open|go to|visit|browse|click|sign ?up|register|log ?in|fill|form|apply|submit|site|website|page|link|"
    r"url|email|inbox|mail|code|verif|reset|wallet|address|create|make (me|yourself)|account|download|"
    r"github|repo|bounty|job|search for|look up|check)\b|https?://|www\.|\.com\b", re.I)


def saver() -> bool:
    try:
        return bool(json.loads(SETTINGS_FILE.read_text()).get("saver", True))
    except (OSError, ValueError):
        return True


def set_saver(on: bool) -> None:
    life.DATA.mkdir(exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps({"saver": on}))


def model_for(kind: str) -> str | None:
    """Which Claude model a kind of work uses. None means Claude Code's default (the strongest)."""
    if not saver():
        return None
    return {"learn": "haiku", "chat": "sonnet", "judge": "sonnet", "trends": "sonnet"}.get(kind)


def chat_needs_tools(message: str) -> bool:
    return not saver() or bool(NEEDS_TOOLS.search(message))
