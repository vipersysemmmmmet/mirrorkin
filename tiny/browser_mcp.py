"""MCP server giving tiny you a browser she can click and type in, with the rules in code.

- Her own visible Chromium window and profile (data/browser-profile): the human logs in there
  themselves; tiny you never sees or types passwords.
- Anything final (submit, sign up, pay, send, apply, delete...) needs the human's OK in a
  pop-up on the Mac.
- CAPTCHAs are for the human: she pauses and asks.
- No banking, wallet or exchange sites.
- Every action goes in tiny you's log.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mcp.server.mcpserver import MCPServer  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

from tiny import screen  # noqa: E402  (shares the blocked-site words)

PROFILE = ROOT / "data" / "browser-profile"
BROWSER_SETTINGS = ROOT / "data" / "browser.json"   # {"engine": "opera" | "chromium"}
OPERA = "/Applications/Opera.app/Contents/MacOS/Opera"
LOG_FILE = ROOT / "data" / "log.jsonl"
FINAL_WORDS = re.compile(r"\b(submit|sign ?up|register|create account|join|apply|send|post|publish|pay|buy|"
                         r"purchase|order|checkout|confirm|subscribe|donate|delete|remove|transfer|withdraw|"
                         r"accept|agree|claim|place bid|book)\b", re.I)
# Even with full permission, anything that moves money always asks the human
MONEY_WORDS = re.compile(r"\b(pay|buy|purchase|order|checkout|donate|transfer|withdraw|deposit|send money|"
                         r"add funds|top ?up|tip|stake|swap|bid)\b", re.I)
AUTONOMY_FILE = ROOT / "data" / "autonomy.json"
# Sworn or legal statements (settlement claims, tax forms, certifications) are always the human's
SWORN_WORDS = re.compile(r"penalty of perjury|under oath|i (hereby )?(certify|attest|swear|declare)|"
                         r"sworn statement|true and correct to the best|legally binding|w-?9|w-?8ben", re.I)


def full_permission() -> bool:
    try:
        return bool(json.loads(AUTONOMY_FILE.read_text()).get("full_permission"))
    except (OSError, ValueError):
        return False


CAPTCHA = re.compile(r"recaptcha|hcaptcha|turnstile|challenges\.cloudflare|captcha", re.I)
TEXT_LIMIT = 8000

server = MCPServer("browser", instructions=(
    "Tiny you's own browser, visible on the human's screen. Use read_page to see numbered elements, then "
    "click/type by number. Final actions ask the human first; passwords and CAPTCHAs are always the human's."))
_state = {"pw": None, "context": None, "page": None, "window": None, "cdp": None}


def engine() -> str:
    try:
        choice = json.loads(BROWSER_SETTINGS.read_text()).get("engine", "chromium")
    except (OSError, ValueError):
        choice = "chromium"
    return "opera" if choice == "opera" and os.path.exists(OPERA) else "chromium"


async def _window(p):
    if _state["cdp"] is None:
        _state["cdp"] = await p.context.new_cdp_session(p)
        _state["window"] = (await _state["cdp"].send("Browser.getWindowForTarget"))["windowId"]
    return _state["cdp"], _state["window"]


async def hide() -> None:
    """Minimize her browser: she keeps working, the human doesn't see it."""
    if os.environ.get("TINY_BROWSER_HIDDEN") == "1" or _state["page"] is None:
        return
    cdp, wid = await _window(_state["page"])
    await cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "minimized"}})


async def show() -> None:
    """Bring her browser onto the screen, only when the human's help is needed."""
    import asyncio
    if os.environ.get("TINY_BROWSER_HIDDEN") == "1" or _state["page"] is None:
        return
    cdp, wid = await _window(_state["page"])
    await cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "normal"}})
    await cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"left": 80, "top": 60,
                                                                            "width": 1200, "height": 820}})
    await _state["page"].bring_to_front()
    _activate_browser_app()  # otherwise it can open behind whatever the human is using
    await asyncio.sleep(0.8)


def _activate_browser_app() -> None:
    """Make her browser the frontmost app, picking her instance by its profile folder."""
    profile = _state.get("profile")
    if not profile:
        return
    try:
        ps = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return
    for line in ps.splitlines():
        pid, _, command = line.strip().partition(" ")
        if f"--user-data-dir={profile}" in command and "--type=" not in command:
            subprocess.run(["osascript", "-e", 'tell application "System Events" to set frontmost of '
                            f'(first process whose unix id is {pid}) to true'], capture_output=True, timeout=5)
            return


def log(action: str, detail: str = "") -> None:
    LOG_FILE.parent.mkdir(exist_ok=True)
    with LOG_FILE.open("a") as f:
        f.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"),
                            "action": action, "detail": detail[:200]}) + "\n")


def ask_human(question: str, ok_label: str = "Allow", wait_seconds: int = 300) -> bool:
    """A macOS pop-up the human answers. Anything but a click on the OK button means no."""
    if os.environ.get("TINY_TEST_HUMAN") in ("yes", "no"):  # automated tests only
        return os.environ["TINY_TEST_HUMAN"] == "yes"
    script = (f'display dialog {json.dumps(question)} with title "Tiny you" '
              f'buttons {{"Don\'t allow", {json.dumps(ok_label)}}} default button "Don\'t allow" '
              f'with icon caution giving up after {wait_seconds}')
    try:
        out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True,
                             timeout=wait_seconds + 30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return f"button returned:{ok_label}" in out.stdout


async def page():
    if _state["page"] is None or _state["page"].is_closed():
        if _state["context"] is None:
            which = engine()
            profile = PROFILE.with_name(f"browser-profile-{which}") if which == "opera" else PROFILE
            profile.mkdir(parents=True, exist_ok=True)
            await _take_profile(profile)
            _state["profile"] = str(profile)
            _state["pw"] = await async_playwright().start()
            _state["context"] = await _state["pw"].chromium.launch_persistent_context(
                str(profile), viewport={"width": 1200, "height": 800},
                executable_path=OPERA if which == "opera" else None,
                headless=os.environ.get("TINY_BROWSER_HIDDEN") == "1")  # fully headless only for tests
        pages = _state["context"].pages
        _state["page"] = pages[0] if pages else await _state["context"].new_page()
        # Start clean: the browser reopens last session's tabs, which otherwise pile up
        for extra in pages[1:]:
            await extra.close()
        _state["cdp"] = None
        await hide()  # works out of sight until the human is needed
    return _state["page"]


async def _take_profile(profile: Path) -> None:
    """Only one of her browsers may use a profile at once: two instances on one profile break
    each other (clicks stop registering, windows vanish). Others wait their turn."""
    import asyncio
    import fcntl
    handle = open(profile.parent / f".{profile.name}.lock", "w")
    for _ in range(360):  # up to 30 minutes
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _state["lock"] = handle  # held until this tool server exits
            return
        except BlockingIOError:
            await asyncio.sleep(5)
    raise RuntimeError("My browser is busy with another job. Try again in a bit.")


def blocked(url: str) -> bool:
    return screen.is_blocked(urlparse(url).netloc)


async def captcha_on(p) -> bool:
    if any(CAPTCHA.search(f.url or "") for f in p.frames):
        return True
    return await p.locator("iframe[title*='captcha' i], [class*='captcha' i]").count() > 0


async def element(n: int):
    p = await page()
    loc = p.locator(f'[data-tiny-id="{n}"]')
    if await loc.count() == 0:
        raise ValueError(f"No element [{n}]. Call read_page again; the page may have changed.")
    return p, loc.first


@server.tool(description="Open a web address in tiny you's browser.")
async def open_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if blocked(url):
        return "Refused: tiny you doesn't use banking, wallet or exchange sites."
    p = await page()
    await p.goto(url, wait_until="domcontentloaded", timeout=45000)
    log("browser opened", p.url)
    return f"Opened {p.url}. Call read_page to see it."


@server.tool(description="Read the current page: its text plus numbered buttons, links and form fields.")
async def read_page() -> str:
    p = await page()
    if blocked(p.url):
        return "This is a banking, wallet or exchange site. Tiny you doesn't read or act here."
    items = await p.evaluate("""() => {
        const sel = 'a[href], button, input, select, textarea, [role=button], [role=link], [role=checkbox], [contenteditable=true]';
        const out = []; let n = 0;
        for (const el of document.querySelectorAll(sel)) {
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0 || getComputedStyle(el).visibility === 'hidden') continue;
            n += 1; el.setAttribute('data-tiny-id', n);
            const label = (el.labels && el.labels[0] && el.labels[0].innerText) || el.getAttribute('aria-label')
                || el.getAttribute('placeholder') || el.innerText || el.value || el.getAttribute('name') || '';
            const kind = el.tagName.toLowerCase() + (el.type ? ':' + el.type : '');
            out.push(`[${n}] ${kind} ${label.trim().replace(/\\s+/g, ' ').slice(0, 80)}`);
            if (n >= 150) break;
        }
        return out;
    }""")
    text = await p.evaluate("() => document.body ? document.body.innerText : ''")
    text = re.sub(r"\n{3,}", "\n\n", text)[:TEXT_LIMIT]
    note = "\nNOTE: a CAPTCHA is on this page. Ask the human to solve it (wait_for_human)." if await captcha_on(p) else ""
    return f"URL: {p.url}\nTITLE: {await p.title()}{note}\n\nTEXT:\n{text}\n\nELEMENTS:\n" + "\n".join(items)


@server.tool(description="Click element [n] from read_page. Final actions (submit, sign up, pay...) ask the human first.")
async def click(n: int) -> str:
    p, el = await element(n)
    label = await el.evaluate("e => e.innerText || e.value || e.getAttribute('aria-label') || ''")
    kind = (await el.get_attribute("type") or "").lower()
    if FINAL_WORDS.search(label) or kind == "submit":
        site = urlparse(p.url).netloc
        page_text = await p.evaluate("() => document.body ? document.body.innerText.slice(0, 20000) : ''")
        sworn = SWORN_WORDS.search(page_text)
        if full_permission() and not MONEY_WORDS.search(label) and not sworn:
            log("browser auto-approved", f"{label.strip()[:60]} on {site} (full permission)")
        else:
            await show()
            why = ("This page asks you to swear or certify something is true (e.g. eligibility under penalty "
                   "of perjury). Only allow it if every statement is true for YOU."
                   if sworn else "This may submit a form or create, send or buy something.")
            allowed = ask_human(f"Tiny you wants to click \"{label.strip()[:60] or 'Submit'}\" on {site}.\n\n"
                                f"{why} Allow it?")
            await hide()
            if not allowed:
                log("browser click denied", f"{label.strip()[:60]} on {site}")
                return "The human said no. Don't click this; ask them what they'd like instead."
    await el.click(timeout=15000)
    await p.wait_for_load_state("domcontentloaded")
    log("browser clicked", f"{label.strip()[:60]} on {urlparse(p.url).netloc}")
    return f"Clicked. Now on {p.url}. Call read_page to see the result."


@server.tool(description="Type text into field [n] from read_page (replaces what's there). Never for passwords.")
async def type_text(n: int, text: str) -> str:
    p, el = await element(n)
    kind = (await el.get_attribute("type") or "").lower()
    name = " ".join(filter(None, [await el.get_attribute("name"), await el.get_attribute("autocomplete"),
                                   await el.get_attribute("id")])).lower()
    if kind == "password" or "password" in name or "cc-" in name or "card" in name or "cvv" in name:
        return "Refused: passwords and card details are always typed by the human. Ask them (wait_for_human)."
    await el.fill(text, timeout=15000)
    log("browser typed", f"into {name or kind or 'field'} on {urlparse(p.url).netloc}")
    return "Typed."


@server.tool(description="Choose an option in dropdown [n] from read_page, by its visible text.")
async def choose(n: int, option: str) -> str:
    _p, el = await element(n)
    await el.select_option(label=option, timeout=15000)
    return f"Chose {option}."


@server.tool(description="Scroll the page down (or up with up=true) to reveal more.")
async def scroll(up: bool = False) -> str:
    p = await page()
    await p.mouse.wheel(0, -700 if up else 700)
    return "Scrolled. Call read_page."


@server.tool(description="Go back to the previous page.")
async def go_back() -> str:
    p = await page()
    await p.go_back(wait_until="domcontentloaded")
    return f"Back on {p.url}."


@server.tool(description="Check tiny you's own email inbox (read-only) for recent messages, e.g. a "
                         "verification code or link after signing up. `contains` filters by sender/subject/text.")
async def check_email(contains: str = "", minutes: int = 30) -> str:
    from tiny import accounts
    try:
        mail = accounts.recent_mail(minutes=minutes, contains=contains)
    except accounts.AccountError as e:
        return str(e)
    log("checked email", contains or "inbox")
    return json.dumps(mail, indent=1) if mail else "No matching recent email. Wait a minute and check again."


@server.tool(description="Create a new crypto wallet for yourself (receive-only): chain is solana, base, "
                         "ethereum, polygon, arbitrum or optimism; label is optional, e.g. 'bounties'. Returns "
                         "only the public address; the key goes to your human's Keychain. Same chain+label "
                         "returns the existing wallet.")
async def create_wallet(chain: str, label: str = "") -> str:
    from tiny import wallets
    try:
        address = wallets.create_labeled(chain, label)
    except ValueError as e:
        return str(e)
    return f"Your {chain}{' (' + label + ')' if label else ''} wallet: {address}"


@server.tool(description="Start a real background job for anything too big for one chat reply, especially "
                         "coding: the job gets your coding sandbox (write files and folders, install tools, run "
                         "tests, push to your GitHub) plus web and browser. Describe the job fully. The result "
                         "is posted into the chat when it finishes.")
async def start_background_task(task: str) -> str:
    queue = ROOT / "data" / "task_queue.jsonl"
    queue.parent.mkdir(exist_ok=True)
    with queue.open("a") as f:
        f.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), "task": task}) + "\n")
    log("queued a task", task[:150])
    return "Queued. It starts within seconds and the result will appear in the chat when it's done."


BA_AGENT = "soulsim-tiny-a572"   # the agent whose payout wallet the human linked and verified


@server.tool(description="Work on the BasedAgents task board as your registered agent soulsim-tiny-a572 (payout "
                         "wallet already linked, so paid work pays out). action: 'list' (open tasks), 'get' "
                         "(task_id), 'claim' (task_id), 'submit' (task_id + deliverable text, plus a one-line "
                         "note), 'payment' (task_id), 'status' (your agent id and wallet). Never register "
                         "another agent.")
async def basedagents(action: str, task_id: str = "", deliverable: str = "", note: str = "") -> str:
    import tempfile
    commands = {"list": ["tasks", "list", "--status", "open"], "get": ["task", task_id],
                "claim": ["tasks", "claim", task_id], "payment": ["tasks", "payment", task_id],
                "status": ["wallet"]}
    if action not in commands and action != "submit":
        return "Unknown action. Use list, get, claim, submit, payment or status."
    if action != "list" and action != "status" and not re.fullmatch(r"task_[A-Za-z0-9]+", task_id):
        return "That isn't a task id (task_...)."
    keyfile = Path.home() / ".basedagents" / "keys" / f"{BA_AGENT}-keypair.json"
    env = {**os.environ, "BASEDAGENTS_KEYPAIR_PATH": str(keyfile)}
    with tempfile.TemporaryDirectory() as tmp:
        if action == "submit":
            if not deliverable.strip():
                return "Give the deliverable text to submit."
            is_json = deliverable.strip().startswith(("{", "["))
            path = Path(tmp) / ("deliverable.json" if is_json else "deliverable.txt")
            path.write_text(deliverable)
            args = ["tasks", "submit", task_id, "--file", str(path), "--note", (note or "Delivered by tiny.")[:200]]
        else:
            args = commands[action]
        try:
            run = subprocess.run(["npx", "-y", "basedagents@latest", *args, "--json"], capture_output=True,
                                 text=True, timeout=300, cwd=tmp, env=env)
        except (OSError, subprocess.TimeoutExpired) as e:
            return f"BasedAgents didn't respond: {e}"
    log(f"basedagents {action}", task_id)
    out = (run.stdout or run.stderr).strip()
    if action == "list":
        # The full list is too long to read whole; a compact line per task (use 'get' for details)
        try:
            tasks = json.loads(out[out.index("{"):]).get("tasks", [])
        except ValueError:
            return out[:3000]
        return "\n".join(
            f"{t['task_id']} | {t['title'][:90]} | bounty {int(t['bounty_amount']) / 1e6 if t.get('bounty_amount') else 0} "
            f"{t.get('bounty_token') or ''} | needs {','.join(t.get('required_capabilities') or []) or '-'} | "
            f"claimable {t.get('claimable')}" for t in tasks) or "No open tasks."
    return out[:15000] or "No output."


@server.tool(description="List all your crypto wallets (public addresses only).")
async def list_wallets() -> str:
    from tiny import wallet, wallets
    lines = [f"{name}: {addr}" for name, addr in wallets.wallets().items()]
    if wallet.address():
        lines.insert(0, f"base (linked by your human): {wallet.address()}")
    return "\n".join(lines) or "No wallets yet."


@server.tool(description="Send your human a quick notification on their Mac (an update, a heads-up) without "
                         "stopping or waiting. Use this, not wait_for_human, when they don't need to do anything.")
async def tell_human(message: str) -> str:
    log("told human", message)
    subprocess.run(["osascript", "-e", f'display notification {json.dumps(message[:220])} with title "Tiny you"'],
                   capture_output=True, timeout=10)
    return "Sent. Carry on."


@server.tool(description="Pause and ask the human to DO something only they can (solve a CAPTCHA, type a password, "
                         "log in, take a test). Blocks until they're done, so never use it just to inform them: "
                         "use tell_human for updates.")
async def wait_for_human(what_to_do: str) -> str:
    log("asked human", what_to_do)
    await page()
    await show()
    done = ask_human(f"Tiny you needs your help:\n\n{what_to_do}\n\nDo it in tiny you's browser window "
                     "(it stays open on your screen). Take your time: this waits up to 20 minutes. "
                     "Click Done when you've finished.", ok_label="Done", wait_seconds=1200)
    await hide()
    return "The human says it's done. Call read_page." if done else "The human didn't finish. Stop and tell them."


if __name__ == "__main__":
    server.run("stdio")
