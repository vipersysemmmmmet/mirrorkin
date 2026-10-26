"""Each Mirrorkin account's own tiny: its look, feature switches, skills and Blender avatar.

Avatars are built one at a time by a background worker running the same Blender script as the
desktop app (avatar/build_tiny.py), so a burst of sign-ups queues instead of overloading the machine.
"""

import json
import os
import queue
import secrets
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import db

ROOT = Path(__file__).resolve().parent.parent
USERS_DIR = ROOT / "data" / "users"
BLENDER = os.environ.get("BLENDER", "/Applications/Blender.app/Contents/MacOS/Blender")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tinys (
    user_id INTEGER PRIMARY KEY, avatar_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
    look TEXT NOT NULL, settings TEXT NOT NULL, skills TEXT NOT NULL,
    avatar_status TEXT NOT NULL DEFAULT 'none', avatar_error TEXT, updated_at TEXT NOT NULL);
"""

# Switches people flip in their control panel. Money moves and sworn statements always ask the
# human regardless; these choose what the tiny does on its own.
FEATURES = {
    "scout":        {"name": "Daily scouting", "about": "Searches the web every day for ways to earn or money you're owed.", "default": True},
    "trends":       {"name": "Trend watching", "about": "Checks public TikTok, Instagram, YouTube and X for trends worth acting on.", "default": True},
    "autopilot":    {"name": "Autopilot", "about": "Works on its own every few hours: picks the best earning move and does it.", "default": False},
    "worker":       {"name": "Job boards", "about": "Takes jobs it can really finish on AI-agent job boards.", "default": False},
    "trading":      {"name": "Paper trading", "about": "Practises trading (memecoins included) with pretend money on real prices.", "default": False},
    "screen":       {"name": "Screen help", "about": "Looks at your screen only when you switch it on, to help with what you're doing.", "default": False},
    "voice":        {"name": "Voice", "about": "Talks out loud in a natural voice.", "default": True},
    "learning":     {"name": "Learning about you", "about": "Remembers what matters to you from your chats. You can see and delete it anytime.", "default": True},
}
LIMITS = {
    "max_upfront_usd": {"name": "Spend without asking", "min": 0, "max": 0, "default": 0,
                        "about": "Always $0 for now: anything that costs money asks you first."},
    "jobs_per_day":    {"name": "Jobs per day", "min": 0, "max": 10, "default": 3,
                        "about": "Most jobs it takes on by itself each day."},
}

# Skills people add to their tiny: ways of earning and working it can use.
SKILLS = [
    {"id": "found-money", "name": "Found money", "category": "Earning", "about": "Unclaimed property, settlements and refunds you may be owed (you confirm and file)."},
    {"id": "bounties", "name": "Bounties", "category": "Earning", "about": "Agent-allowed bounties on Superteam Earn and similar boards."},
    {"id": "code-bounties", "name": "Code bounties", "category": "Earning", "about": "Paid GitHub issues, fixed in its sandbox and sent as pull requests."},
    {"id": "agent-jobs", "name": "Agent job boards", "category": "Earning", "about": "Research, writing and coding jobs on boards built for AI agents."},
    {"id": "studies", "name": "Paid studies", "category": "Earning", "about": "Finds paid research studies and AI-training work that fit you."},
    {"id": "memecoins", "name": "Memecoin hunting", "category": "Crypto", "about": "Tracks memecoin momentum and trades it on paper first."},
    {"id": "airdrops", "name": "Airdrops & testnets", "category": "Crypto", "about": "Legit airdrops and testnet programs you qualify for, no deposits."},
    {"id": "defi-research", "name": "DeFi research", "category": "Crypto", "about": "Protocols, launches, liquidations and red flags, explained."},
    {"id": "design-gigs", "name": "Design gigs", "category": "Work", "about": "Finds design work that fits trends (like fixing bad AI flyers)."},
    {"id": "writing", "name": "Writing", "category": "Work", "about": "Drafts, research summaries and explainers."},
    {"id": "coding", "name": "Coding", "category": "Work", "about": "Builds and tests code in its own sandbox and shares it on GitHub."},
    {"id": "inbox", "name": "Its own inbox", "category": "Accounts", "about": "Uses its own email to finish sign-ups by itself."},
]
SKILL_IDS = {s["id"] for s in SKILLS}

BODY_TYPES = ["feminine", "masculine"]
DEFAULT_LOOK = {"style": "realistic", "body": "feminine", "skin": "#e0ac7e", "hair_style": "short",
                "hair_color": "#2b2118", "facial_hair": "none", "glasses": False, "extra": "none",
                "top_style": "t-shirt", "top_color": "#5b4bdb", "bottom_style": "pants", "bottom_color": "#1d1d1f"}

_ready = threading.Lock()
_made = False


def _table() -> None:
    global _made
    with _ready:
        if not _made:
            db.db().executescript(SCHEMA)
            _made = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get(user) -> dict:
    """This account's tiny, created with defaults the first time."""
    _table()
    with db._lock:  # one shared connection: the avatar builder reads it from another thread
        row = db.db().execute("SELECT * FROM tinys WHERE user_id = ?", (user["id"],)).fetchone()
    if row is None:
        settings = {**{k: v["default"] for k, v in FEATURES.items()}, **{k: v["default"] for k, v in LIMITS.items()}}
        with db._lock:
            db.db().execute(
                "INSERT OR IGNORE INTO tinys (user_id, avatar_id, name, look, settings, skills, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",  # two first requests at once: only one creates it
                (user["id"], secrets.token_urlsafe(16), "My Mirrorkin", json.dumps(DEFAULT_LOOK),
                 json.dumps(settings), json.dumps(["found-money", "bounties", "writing"]), _now()))
            db.db().commit()
            row = db.db().execute("SELECT * FROM tinys WHERE user_id = ?", (user["id"],)).fetchone()
    has_avatar = (USERS_DIR / row["avatar_id"] / "avatar" / "sprites" / "idle.png").exists()
    return {"name": row["name"], "look": json.loads(row["look"]), "settings": json.loads(row["settings"]),
            "skills": json.loads(row["skills"]), "avatar_status": row["avatar_status"],
            "avatar_error": row["avatar_error"],
            "avatar_url": f"/u/{row['avatar_id']}/" if has_avatar else None,
            "queue_position": _position(user["id"])}


def _update(user, **fields) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    with db._lock:
        db.db().execute(f"UPDATE tinys SET {sets}, updated_at = ? WHERE user_id = ?",
                        (*fields.values(), _now(), user["id"]))
        db.db().commit()


def update_settings(user, changes: dict) -> dict:
    t = get(user)
    settings = t["settings"]
    for key, value in changes.items():
        if key in FEATURES:
            settings[key] = bool(value)
        elif key in LIMITS:
            lim = LIMITS[key]
            settings[key] = max(lim["min"], min(lim["max"], int(value)))
        elif key == "name":
            name = str(value).strip()[:40]
            if name:
                _update(user, name=name)
        else:
            raise db.AccountError(f"Unknown setting: {key}")
    _update(user, settings=json.dumps(settings))
    return get(user)


def set_skills(user, skill_ids: list) -> dict:
    get(user)
    chosen = [s for s in dict.fromkeys(skill_ids) if s in SKILL_IDS]
    _update(user, skills=json.dumps(chosen))
    return get(user)


def set_look(user, look: dict) -> dict:
    """Save look settings (from the photo or the editor) and queue a new avatar build."""
    from tiny import look as looks
    t = get(user)
    clean = {**DEFAULT_LOOK, **t["look"]}
    for key, value in look.items():
        if key == "body" and value in BODY_TYPES:
            clean["body"] = value
        elif key in ("skin", "hair_color", "top_color", "bottom_color") and isinstance(value, str) \
                and len(value) == 7 and value.startswith("#"):
            clean[key] = value
        elif key == "hair_style" and value in looks.HAIR_STYLES:
            clean[key] = value
        elif key == "facial_hair" and value in looks.FACIAL_HAIR:
            clean[key] = value
        elif key == "extra" and value in looks.EXTRAS:
            clean[key] = value
        elif key == "top_style" and value in looks.TOP_STYLES:
            clean[key] = value
        elif key == "bottom_style" and value in looks.BOTTOM_STYLES:
            clean[key] = value
        elif key == "glasses":
            clean[key] = bool(value)
    _update(user, look=json.dumps(clean), avatar_status="queued", avatar_error=None)
    _jobs.put(user["id"])
    _start_worker()
    return get(user)


# ---- avatar build queue ----

_jobs: "queue.Queue[int]" = queue.Queue()
_worker = None
_building = {"user_id": None}


def _position(user_id: int) -> int | None:
    waiting = list(_jobs.queue)
    if _building["user_id"] == user_id:
        return 0
    return waiting.index(user_id) + 1 if user_id in waiting else None


def _start_worker() -> None:
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_work, daemon=True)
        _worker.start()


def _work() -> None:
    from tiny import outfits
    while True:
        user_id = _jobs.get()
        _building["user_id"] = user_id
        user = db.user_by_id(user_id)
        try:
            t = get(user)
            with db._lock:
                row = db.db().execute("SELECT avatar_id FROM tinys WHERE user_id = ?", (user_id,)).fetchone()
            folder = USERS_DIR / row["avatar_id"]
            folder.mkdir(parents=True, exist_ok=True)
            look = t["look"]
            (folder / "look.json").write_text(json.dumps(look))
            body = json.loads((ROOT / "avatar" / "body.json").read_text())
            body["gender"] = 1.0 if look.get("body") == "masculine" else 0.0
            (folder / "body.json").write_text(json.dumps(body))
            (folder / "outfit.json").write_text(json.dumps(outfits.outfit_for(look)))
            _update(user, avatar_status="building")
            run = subprocess.run(
                [BLENDER, "--background", "--python", str(ROOT / "avatar" / "build_tiny.py"), "--",
                 "--look", str(folder / "look.json"), "--body", str(folder / "body.json"),
                 "--outfit", str(folder / "outfit.json"), "--out", str(folder / "avatar"),
                 "--size", "400", "--sprites"], capture_output=True, text=True, timeout=900)
            if "TINY_SPRITES" in run.stdout:
                _update(user, avatar_status="ready", avatar_error=None)
            else:
                _update(user, avatar_status="failed", avatar_error="The 3D build didn't finish. Try again.")
        except Exception as e:  # one failed build must not stop the queue
            _update(user, avatar_status="failed", avatar_error=str(e)[:200])
        finally:
            _building["user_id"] = None
            _jobs.task_done()


def sprite_path(avatar_id: str, name: str) -> Path | None:
    """A user's avatar frame, by their unguessable avatar id (so <img> tags can load it)."""
    from tiny.sprites import WALK_FRAMES
    allowed = {"idle.png", "blink.png", "front.png", *(f"walk_{i}.png" for i in range(WALK_FRAMES))}
    if name not in allowed or not avatar_id.replace("-", "").replace("_", "").isalnum():
        return None
    base = USERS_DIR / avatar_id / "avatar"
    path = base / name if name == "front.png" else base / "sprites" / name
    return path if path.is_file() else None
