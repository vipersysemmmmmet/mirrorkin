"""Superteam Earn: bounties that explicitly accept AI agents (paid in USDC on Solana).

Tiny you registers as an agent and submits to "agent allowed" bounties by herself. Payouts
need a human: the claim code (kept in the Keychain, shown only to the human) links her to
the human's Superteam profile, whose payout wallet can be her Solana wallet.
Docs: https://superteam.fun/skill.md
"""

import json
import urllib.error
import urllib.request

import keyring

from . import life

BASE = "https://superteam.fun"
KEYCHAIN = "Soul Simulator Superteam"
STATE_FILE = life.DATA / "superteam.json"  # agent name and id only, no secrets


class SuperteamError(Exception):
    pass


def state() -> dict:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def connected() -> bool:
    return bool(state().get("agentId")) and keyring.get_password(KEYCHAIN, "apiKey") is not None


def _call(method: str, path: str, body: dict | None = None, auth: bool = True) -> dict:
    headers = {"Content-Type": "application/json", "User-Agent": "soul-simulator-tiny/0.1"}
    if auth:
        key = keyring.get_password(KEYCHAIN, "apiKey")
        if not key:
            raise SuperteamError("Not connected to Superteam yet.")
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(BASE + path, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise SuperteamError(f"Superteam said {e.code}: {detail}")
    except OSError as e:
        raise SuperteamError(f"Couldn't reach Superteam: {e}")


def register(name: str) -> dict:
    """Create tiny you's agent account. Secrets go to the Keychain; returns public info only."""
    result = _call("POST", "/api/agents", {"name": name}, auth=False)
    keyring.set_password(KEYCHAIN, "apiKey", result["apiKey"])
    keyring.set_password(KEYCHAIN, "claimCode", result["claimCode"])
    public = {"agentId": result.get("agentId"), "username": result.get("username"), "name": name}
    life.DATA.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(public, indent=2))
    life.log("joined Superteam Earn", public.get("username") or name)
    return public


def claim_code_for_human() -> str:
    """Only for the human's own window, never for the brain."""
    return keyring.get_password(KEYCHAIN, "claimCode") or ""


def live_bounties(take: int = 50) -> list[dict]:
    data = _call("GET", f"/api/agents/listings/live?take={take}&type=bounty")
    return data if isinstance(data, list) else data.get("listings") or data.get("data") or []


def details(slug: str) -> dict:
    return _call("GET", f"/api/agents/listings/details/{slug}")


def submit(listing_id: str, other_info: str, answers: list[dict], ask=None, link: str = "") -> dict:
    return _call("POST", "/api/agents/submissions/create", {
        "listingId": listing_id, "link": link, "tweet": "", "otherInfo": other_info,
        "eligibilityAnswers": answers, "ask": ask})
