"""Tiny you's own accounts: an email inbox and a GitHub machine account.

The human creates both (GitHub allows one "machine account" per person for automation; Google
requires a human to create Gmail accounts). Credentials live in the Mac's Keychain. Tiny you uses:
- email: read-only access to find verification codes/links and job emails (never sends mail)
- GitHub: a fine-grained token for her own account, handed only to her job sandbox
"""

import email
import email.policy
import imaplib
import json
import re
from datetime import datetime, timedelta

import keyring

from . import life

KEYCHAIN = "Soul Simulator tiny accounts"
STATE_FILE = life.DATA / "accounts.json"   # addresses and usernames only, no secrets
IMAP_HOSTS = {"gmail.com": "imap.gmail.com", "googlemail.com": "imap.gmail.com", "outlook.com": "outlook.office365.com",
              "hotmail.com": "outlook.office365.com", "icloud.com": "imap.mail.me.com", "proton.me": "127.0.0.1"}


class AccountError(Exception):
    pass


def state() -> dict:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def _save(**values) -> None:
    life.DATA.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps({**state(), **values}, indent=2))


# ---- email ----

def connect_email(address: str, app_password: str) -> None:
    address = address.strip().lower()
    host = IMAP_HOSTS.get(address.split("@")[-1])
    if not host:
        raise AccountError("I can connect Gmail, Outlook or iCloud addresses.")
    try:
        with imaplib.IMAP4_SSL(host) as box:
            box.login(address, app_password.replace(" ", ""))
    except imaplib.IMAP4.error:
        raise AccountError("That login didn't work. For Gmail, use an App Password, not the normal password.")
    keyring.set_password(KEYCHAIN, f"email:{address}", app_password.replace(" ", ""))
    _save(email=address, imap=host)
    life.log("connected email", address)


def recent_mail(minutes: int = 30, contains: str = "", limit: int = 10) -> list[dict]:
    """Newest messages (read-only): sender, subject, a text preview, and any codes or links found."""
    s = state()
    if not s.get("email"):
        raise AccountError("No inbox connected yet.")
    password = keyring.get_password(KEYCHAIN, f"email:{s['email']}")
    since = (datetime.now() - timedelta(minutes=minutes)).strftime("%d-%b-%Y")
    out = []
    with imaplib.IMAP4_SSL(s["imap"]) as box:
        box.login(s["email"], password)
        box.select("INBOX", readonly=True)
        _, ids = box.search(None, "SINCE", since)
        for num in reversed(ids[0].split()[-limit * 3:]):
            _, data = box.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(data[0][1], policy=email.policy.default)
            part = msg.get_body(preferencelist=("plain", "html"))
            body = re.sub(r"<[^>]+>", " ", part.get_content() if part else "")
            body = re.sub(r"\s+", " ", body)
            item = {"from": str(msg["from"]), "subject": str(msg["subject"]), "preview": body[:600],
                    "codes": re.findall(r"\b\d{4,8}\b", body)[:3],
                    "links": [l for l in re.findall(r"https?://\S+", body) if re.search(r"verif|confirm|activate|magic|login", l, re.I)][:3]}
            if contains.lower() in (item["from"] + item["subject"] + body).lower():
                out.append(item)
            if len(out) >= limit:
                break
    return out


# ---- GitHub ----

def connect_github(username: str, token: str) -> None:
    import urllib.request
    req = urllib.request.Request("https://api.github.com/user", headers={
        "Authorization": f"Bearer {token.strip()}", "User-Agent": "soul-simulator-tiny"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            login = json.loads(r.read())["login"]
    except Exception:
        raise AccountError("GitHub didn't accept that token.")
    if login.lower() != username.strip().lower():
        raise AccountError(f"That token belongs to {login}, not {username}.")
    keyring.set_password(KEYCHAIN, f"github:{login}", token.strip())
    _save(github=login)
    life.log("connected GitHub", login)


def github_for_sandbox() -> dict:
    """Environment for her job sandbox: her own GitHub identity, nothing of the human's."""
    login = state().get("github")
    token = keyring.get_password(KEYCHAIN, f"github:{login}") if login else None
    if not token:
        return {}
    return {"GH_TOKEN": token, "GITHUB_USER": login,
            "GIT_AUTHOR_NAME": login, "GIT_COMMITTER_NAME": login,
            "GIT_AUTHOR_EMAIL": f"{login}@users.noreply.github.com",
            "GIT_COMMITTER_EMAIL": f"{login}@users.noreply.github.com"}
