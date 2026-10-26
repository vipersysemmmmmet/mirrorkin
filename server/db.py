"""Accounts, sessions, payments and usage in SQLite."""

import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .config import PASS_DAYS, PLANS, TRIAL_DAYS

DB_FILE = Path(__file__).resolve().parent.parent / "data" / "server.db"
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, pw_hash BLOB NOT NULL, salt BLOB NOT NULL,
    wallet TEXT, plan TEXT NOT NULL DEFAULT 'free', expires_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS payments (
    tx_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, amount INTEGER NOT NULL,
    plan TEXT NOT NULL, days INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS usage (
    user_id INTEGER NOT NULL, day TEXT NOT NULL, chats INTEGER NOT NULL DEFAULT 0,
    scouts INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (user_id, day));
CREATE TABLE IF NOT EXISTS waitlist (
    email TEXT PRIMARY KEY, created_at TEXT NOT NULL);
"""


class AccountError(Exception):
    pass


def join_waitlist(email: str) -> None:
    email = email.strip().lower()
    if (len(email) > 200
            or not re.fullmatch(r"[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+", email)):
        raise AccountError("Enter a valid email address.")
    with _lock:
        db().execute(
            "INSERT OR IGNORE INTO waitlist (email, created_at) VALUES (?, ?)",
            (email, now().isoformat()))
        db().commit()


def now() -> datetime:
    return datetime.now(timezone.utc)


def connect() -> sqlite3.Connection:
    DB_FILE.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


_conn = None


def db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = connect()
    return _conn


def _hash_pw(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def signup(email: str, password: str) -> str:
    email = email.strip().lower()
    if "@" not in email or len(email) > 200:
        raise AccountError("Enter a valid email address.")
    if len(password) < 8:
        raise AccountError("Password must be at least 8 characters.")
    salt = secrets.token_bytes(16)
    with _lock:
        try:
            cur = db().execute(
                "INSERT INTO users (email, pw_hash, salt, created_at) VALUES (?, ?, ?, ?)",
                (email, _hash_pw(password, salt), salt, now().isoformat()))
        except sqlite3.IntegrityError:
            raise AccountError("An account with that email already exists.")
        db().commit()
    return _new_session(cur.lastrowid)


def login(email: str, password: str) -> str:
    row = db().execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
    # Same error either way, so nobody can probe which emails have accounts.
    if not row or not hmac.compare_digest(row["pw_hash"], _hash_pw(password, row["salt"])):
        raise AccountError("Wrong email or password.")
    return _new_session(row["id"])


def _new_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with _lock:
        db().execute("INSERT INTO sessions VALUES (?, ?, ?)",
                     (_token_hash(token), user_id, now().isoformat()))
        db().commit()
    return token


def logout(token: str) -> None:
    with _lock:
        db().execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
        db().commit()


def user_for_token(token: str) -> sqlite3.Row | None:
    return db().execute(
        "SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id "
        "WHERE sessions.token_hash = ?", (_token_hash(token),)).fetchone()


def user_by_id(user_id: int) -> sqlite3.Row:
    return db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def set_wallet(user_id: int, wallet: str) -> None:
    # TODO before real money: prove ownership with a wallet signature, not just uniqueness.
    taken = db().execute("SELECT id FROM users WHERE wallet = ? AND id != ?",
                         (wallet.lower(), user_id)).fetchone()
    if taken:
        raise AccountError("That wallet is already linked to another account.")
    with _lock:
        db().execute("UPDATE users SET wallet = ? WHERE id = ?", (wallet.lower(), user_id))
        db().commit()


def active_plan(user: sqlite3.Row) -> str:
    if user["plan"] != "free" and user["expires_at"] and datetime.fromisoformat(user["expires_at"]) > now():
        return user["plan"]
    return "free"


def record_payment(user: sqlite3.Row, tx_hash: str, amount: int, plan: str, days: int) -> str:
    """Save the payment and extend the pass. Returns the new expiry. Each transaction counts once."""
    current = active_plan(user)
    # Paying for the same plan adds time; switching plans starts the new pass now.
    start = datetime.fromisoformat(user["expires_at"]) if current == plan else now()
    expires = (start + timedelta(days=days)).isoformat()
    with _lock:
        try:
            db().execute("INSERT INTO payments VALUES (?, ?, ?, ?, ?, ?)",
                         (tx_hash.lower(), user["id"], amount, plan, days, now().isoformat()))
        except sqlite3.IntegrityError:
            raise AccountError("That payment has already been used.")
        db().execute("UPDATE users SET plan = ?, expires_at = ? WHERE id = ?", (plan, expires, user["id"]))
        db().commit()
    return expires


def _week_start() -> str:
    today = date.today()
    return (today - timedelta(days=today.weekday())).isoformat()


def usage(user_id: int) -> dict:
    today = date.today().isoformat()
    chats = db().execute("SELECT chats FROM usage WHERE user_id = ? AND day = ?",
                         (user_id, today)).fetchone()
    scouts = db().execute("SELECT COALESCE(SUM(scouts), 0) FROM usage WHERE user_id = ? AND day >= ?",
                          (user_id, _week_start())).fetchone()[0]
    return {"chats_today": chats["chats"] if chats else 0, "scouts_this_week": scouts}


def use(user: sqlite3.Row, kind: str) -> None:
    """Count one chat or scout run, or raise if the plan's limit is reached."""
    plan = active_plan(user)
    if plan == "free":
        trial_expires = datetime.fromisoformat(user["created_at"]) + timedelta(days=TRIAL_DAYS)
        if trial_expires <= now():
            raise AccountError(
                f"Your {TRIAL_DAYS}-day Starter trial has ended. Upgrade to a paid pass to continue.")
    limits = PLANS[plan]
    used = usage(user["id"])
    if kind == "chats" and used["chats_today"] >= limits["chats_per_day"]:
        raise AccountError(f"You've used today's {limits['chats_per_day']} chats on the "
                           f"{limits['name']} plan. Upgrade for more.")
    if kind == "scouts" and used["scouts_this_week"] >= limits["scouts_per_week"]:
        raise AccountError(f"You've used this week's {limits['scouts_per_week']} Scout runs on the "
                           f"{limits['name']} plan. Upgrade for more.")
    with _lock:
        db().execute(f"INSERT INTO usage (user_id, day, {kind}) VALUES (?, ?, 1) "
                     f"ON CONFLICT (user_id, day) DO UPDATE SET {kind} = {kind} + 1",
                     (user["id"], date.today().isoformat()))
        db().commit()


def account_info(user: sqlite3.Row) -> dict:
    plan = active_plan(user)
    trial_expires = datetime.fromisoformat(user["created_at"]) + timedelta(days=TRIAL_DAYS)
    return {
        "email": user["email"], "wallet": user["wallet"], "plan": plan,
        "plan_name": PLANS[plan]["name"],
        "expires_at": user["expires_at"] if plan != "free" else None,
        "trial_expires_at": trial_expires.isoformat() if plan == "free" else None,
        "trial_active": plan == "free" and trial_expires > now(),
        "limits": PLANS[plan], "usage": usage(user["id"]),
    }
