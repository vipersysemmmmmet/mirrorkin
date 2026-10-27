"""Soul Simulator server: accounts, USDC passes, plan limits, and the AI brain behind them.

Desktop apps talk to this server; only the server holds the AI provider keys.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import credits, db, tinys
from .chain import ADDRESS_RE, PaymentError, verify_usdc_payment
from .config import PASS_DAYS, PLANS, TRIAL_DAYS, USDC_DECIMALS, network, pay_to, port

PAGE = Path(__file__).with_name("landing.html")
PRICING_PAGE = Path(__file__).with_name("pricing.html")
SPRITES = Path(__file__).resolve().parent.parent / "data" / "avatar" / "sprites"
SPRITE_FILES = {"idle.png", "blink.png", *(f"walk_{i}.png" for i in range(8))}
MAX_MESSAGES = 40
MAX_CHARS = 4000
MAX_SYSTEM_CHARS = 8000
MAX_IMAGE_B64 = 2_000_000  # about 1.5 MB of JPEG: one shrunk screenshot


class HTTPError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def price_micro(plan: str) -> int:
    return round(PLANS[plan]["price"] * 10**USDC_DECIMALS)


# ---- endpoints: each takes (user or None, body) and returns a dict ----

def pricing(_user, _body) -> dict:
    net = network()
    return {"plans": PLANS, "pass_days": PASS_DAYS, "trial_days": TRIAL_DAYS, "currency": "USDC",
            "network": net["id"], "network_label": net["label"],
            "usdc_contract": net["usdc"], "pay_to": pay_to(), "explorer": net["explorer"]}


def signup(_user, body) -> dict:
    return {"token": db.signup(str(body.get("email", "")), str(body.get("password", "")))}


def join_waitlist(_user, body) -> dict:
    db.join_waitlist(str(body.get("email", "")))
    return {"ok": True, "message": "You're on the list. We'll be in touch."}


def login(_user, body) -> dict:
    return {"token": db.login(str(body.get("email", "")), str(body.get("password", "")))}


def me(user, _body) -> dict:
    return {**db.account_info(user), "credits": credits.balance(user["id"])}


def get_credits(user, _body) -> dict:
    return credits.info(user)


def buy_credits(user, body) -> dict:
    return credits.buy(user, str(body.get("tx_hash", "")))


def set_wallet(user, body) -> dict:
    wallet = str(body.get("wallet", "")).strip()
    if not ADDRESS_RE.match(wallet):
        raise HTTPError(400, "That isn't a wallet address (0x followed by 40 characters).")
    db.set_wallet(user["id"], wallet)
    return db.account_info(db.user_by_id(user["id"]))


def pay(user, body) -> dict:
    plan = str(body.get("plan", ""))
    if plan not in PLANS or plan == "free":
        raise HTTPError(400, "Choose the Plus or Pro plan.")
    if not user["wallet"]:
        raise HTTPError(400, "Add the wallet you'll pay from first, so we can match your payment.")
    if not pay_to():
        raise HTTPError(503, "Payments aren't set up on this server yet (SOUL_PAY_TO is missing).")
    amount = verify_usdc_payment(network(), str(body.get("tx_hash", "")).strip(), user["wallet"], pay_to())
    passes = amount // price_micro(plan)
    if passes < 1:
        raise HTTPError(400, f"That payment was {amount / 10**USDC_DECIMALS:.2f} USDC; "
                             f"{PLANS[plan]['name']} costs {PLANS[plan]['price']} USDC.")
    db.record_payment(user, body["tx_hash"].strip(), amount, plan, PASS_DAYS * passes)
    return db.account_info(db.user_by_id(user["id"]))


def chat(user, body) -> dict:
    messages, system = body.get("messages"), str(body.get("system", ""))
    if (not isinstance(messages, list) or not messages or len(messages) > MAX_MESSAGES
            or len(system) > MAX_SYSTEM_CHARS
            or any(not isinstance(m, dict) or m.get("role") not in ("user", "assistant")
                   or not isinstance(m.get("content"), str) or len(m["content"]) > MAX_CHARS
                   for m in messages)):
        raise HTTPError(400, "Invalid chat request.")
    image = body.get("image")
    if image is not None and (not isinstance(image, str) or len(image) > MAX_IMAGE_B64
                              or messages[-1]["role"] != "user"):
        raise HTTPError(400, "Invalid screenshot.")
    credits.use(user, "chats")  # plan allowance first, then credits
    import base64
    import binascii
    from tiny.brain import BrainError, ClaudeBrain
    try:
        jpeg = base64.b64decode(image, validate=True) if image else None
    except binascii.Error:
        raise HTTPError(400, "Invalid screenshot.")
    try:
        reply = ClaudeBrain().reply(system, [{"role": m["role"], "content": m["content"]} for m in messages], jpeg)
    except BrainError as e:
        raise HTTPError(502, str(e))
    return {"reply": reply, "usage": db.usage(user["id"])}


def logout(user, body) -> dict:
    db.logout(body["_token"])
    return {"ok": True}


# ---- each account's own tiny (control panel) ----

def catalogue(_user, _body) -> dict:
    from tiny import look as looks
    return {"features": tinys.FEATURES, "limits": tinys.LIMITS, "skills": tinys.SKILLS,
            "body_types": tinys.BODY_TYPES,
            "look_options": {"hair_style": looks.HAIR_STYLES, "top_style": looks.TOP_STYLES,
                             "bottom_style": looks.BOTTOM_STYLES, "build": looks.BUILDS}}


def get_tiny(user, _body) -> dict:
    return tinys.get(user)


def tiny_settings(user, body) -> dict:
    changes = body.get("changes")
    if not isinstance(changes, dict):
        raise HTTPError(400, "Send the settings to change.")
    return tinys.update_settings(user, changes)


def tiny_skills(user, body) -> dict:
    skills = body.get("skills")
    if not isinstance(skills, list):
        raise HTTPError(400, "Send the list of skills.")
    return tinys.set_skills(user, [str(s) for s in skills])


def tiny_look(user, body) -> dict:
    look = body.get("look")
    if not isinstance(look, dict):
        raise HTTPError(400, "Send the look settings.")
    return tinys.set_look(user, look)


def tiny_photo(user, body) -> dict:
    """Photo -> look settings -> queued Blender build. The photo itself is never stored."""
    import base64
    import binascii
    from tiny import look as looks
    image = body.get("image")
    if not isinstance(image, str) or len(image) > MAX_IMAGE_B64:
        raise HTTPError(400, "Send one photo (JPEG, under about 1.5 MB).")
    try:
        jpeg = base64.b64decode(image, validate=True)
    except binascii.Error:
        raise HTTPError(400, "That photo couldn't be read.")
    try:
        found = looks.look_from_photo(jpeg)
    except looks.LookError as e:
        raise HTTPError(400, str(e))
    if body.get("body") in tinys.BODY_TYPES:  # chosen by the person, never guessed from the photo
        found["body"] = body["body"]
    return tinys.set_look(user, found)


PUBLIC = {("GET", "/pricing"): pricing, ("GET", "/api/pricing"): pricing,
          ("POST", "/signup"): signup, ("POST", "/login"): login,
          ("POST", "/waitlist"): join_waitlist, ("GET", "/tiny/catalogue"): catalogue}
PRIVATE = {("GET", "/me"): me, ("POST", "/wallet"): set_wallet, ("POST", "/pay"): pay,
           ("POST", "/chat"): chat, ("POST", "/logout"): logout,
           ("GET", "/tiny"): get_tiny, ("POST", "/tiny/settings"): tiny_settings,
           ("POST", "/tiny/skills"): tiny_skills, ("POST", "/tiny/look"): tiny_look,
           ("POST", "/tiny/photo"): tiny_photo,
           ("GET", "/credits"): get_credits, ("POST", "/credits/buy"): buy_credits}
APP_PAGE = Path(__file__).with_name("app.html")


class Handler(BaseHTTPRequestHandler):
    def _reply(self, status: int, obj: dict) -> None:
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self, method: str) -> None:
        key = (method, self.path)
        try:
            if method == "GET" and self.path.startswith("/avatar/"):
                name = self.path.removeprefix("/avatar/")
                if name not in SPRITE_FILES:
                    raise HTTPError(404, "Not found.")
                sprite = SPRITES / name
                if not sprite.is_file():
                    raise HTTPError(404, "Avatar animation isn't available.")
                data = sprite.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(data)
                return
            if method == "GET" and self.path.startswith("/u/"):
                # A user's own avatar frames, by their unguessable avatar id
                parts = self.path.split("/")
                sprite = tinys.sprite_path(parts[2], parts[3]) if len(parts) == 4 else None
                if sprite is None:
                    raise HTTPError(404, "Not found.")
                data = sprite.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(data)
                return
            wants_pricing_page = (method == "GET" and self.path == "/pricing"
                                  and "text/html" in self.headers.get("Accept", ""))
            if key in (("GET", "/"), ("GET", "/app")) or wants_pricing_page:
                page = PRICING_PAGE if wants_pricing_page else APP_PAGE if self.path == "/app" else PAGE
                data = page.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_IMAGE_B64 + 300_000:  # one screenshot plus the conversation
                raise HTTPError(413, "Request too large.")
            body = json.loads(self.rfile.read(length) or "{}") if method == "POST" else {}
            if not isinstance(body, dict):
                raise HTTPError(400, "Invalid request.")
            if key in PUBLIC:
                return self._reply(200, PUBLIC[key](None, body))
            if key not in PRIVATE:
                raise HTTPError(404, "Not found.")
            token = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            user = db.user_for_token(token) if token else None
            if user is None:
                raise HTTPError(401, "Please log in.")
            self._reply(200, PRIVATE[key](user, {**body, "_token": token}))
        except HTTPError as e:
            self._reply(e.status, {"error": str(e)})
        except (db.AccountError, PaymentError) as e:
            self._reply(402 if "Upgrade" in str(e) else 400, {"error": str(e)})
        except json.JSONDecodeError:
            self._reply(400, {"error": "Invalid JSON."})
        except Exception as e:
            self._reply(500, {"error": f"Server error: {e}"})

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def log_message(self, fmt, *args) -> None:
        pass


def serve() -> None:
    host = os.environ.get("SOUL_HOST", "127.0.0.1")
    net = network()
    credits.fee_bps()  # stop at start-up if the fee setting is invalid
    print(f"Credits: pool {credits.pool_address() or 'NOT SET'} | team fee {credits.fee_bps() / 100:g}% "
          f"to {credits.team_address() or 'NOT SET (SOUL_TEAM_ADDRESS)'}")
    print(f"Soul Simulator server on http://{host}:{port()} | {net['label']} | "
          f"payments to {pay_to() or 'NOT SET (add SOUL_PAY_TO to .env)'}")
    ThreadingHTTPServer((host, port()), Handler).serve_forever()
