# Mirrorkin: a personal AI companion

Tiny you's first job: work out how *you* could earn or find money, search the web for real opportunities, rank them, and text you about the best ones. It moves no money and takes no actions. It only finds and reports.

## Setup (once)

1. Get an Anthropic API key at console.anthropic.com (add about $10 credit).
2. Copy `.env.example` to `.env` and paste your key into it. Never share or commit `.env`.
3. Copy `profile.example.json` to `profile.json` and describe yourself. Any fields are fine; the clone decides what's useful.

## Use

```bash
.venv/bin/python -m scout run      # scout, rank, and write tiny you's daily text
.venv/bin/python -m scout review   # approve or reject finds so it learns your taste
.venv/bin/python -m scout memory   # see what it remembers
```

No API credit yet? Feed it a results file instead. The safety rules, ranking, review and memory all work the same:

```bash
.venv/bin/python -m scout run examples/sample_results.json
```

## How it works

- `scout/brain.py`: Claude brainstorms ideas from your profile and memory, searches the web, and returns structured finds.
- `scout/policy.py`: plain-code rules the AI can't override. Blocks low-legitimacy finds, up-front payments, trading or gambling (unless `allow_trading` is true), impersonation, banned practices, and anything likely to cost more than it pays.
- `scout/memory.py`: sources, your approvals and rejections, and the clone's lessons, stored in `data/`.

Ranking = expected profit (pay × success chance − cost) × fit × legitimacy ÷ (1 + hours of your time).

## Desktop tiny you (PC)

```bash
.venv/bin/python -m tiny
```

Tiny you walks along the bottom of your screen. Click it for the menu (chat, finds, deadlines, Scout page, activity log, nap, go home). Drag it anywhere. "Go home" hides it to the menu-bar or tray icon. Every action is logged to `data/log.jsonl`. Chat needs `ANTHROPIC_API_KEY`; everything else works without it. Click "🎨 Change my look" to make tiny you look like you: pick skin, hair, glasses and outfit, or "Fill from my photo" (needs an API key; only the settings are saved, never the photo).

The Top and Bottoms style fields are editable. You can choose a preset or type an installed MPFB clothing asset name (for example, `toigo_basic_tucked_t-shirt`); unknown assets are rejected without replacing your saved look.

To plug in another AI model, add a class with a `reply` method in `tiny/brain.py`, register it in `BRAINS`, and set `TINY_BRAIN` in `.env`.

## Server: accounts, USDC passes and plan limits

```bash
.venv/bin/python -m server      # runs at http://127.0.0.1:8100
```

The server root serves the Mirrorkin landing page, with planned prices and an AI usage-cost estimate at `/pricing`. The Starter plan is a 2-day trial with 10 chats/day; paid 30-day passes are Plus (30 USDC), Pro (100 USDC), and Pro Max (200 USDC). Visitors can join the early-access waitlist with their email; entries are stored in the server database. The desktop app gets machine-readable plan/payment details from `/api/pricing`.

Users can buy 30-day passes (Plus 30 USDC, Pro 100 USDC, Pro Max 200 USDC) on Base. The server confirms each payment on the blockchain itself (read-only: it never holds keys or funds), counts usage, and enforces each plan's limits. Each payment can be used once, and only from the wallet linked to the account. In the desktop app, open **Account & plan** from Mirrorkin's menu.

Settings are in `.env` (see `.env.example`). Start on `base-sepolia` with free test USDC from Circle's faucet; switch `SOUL_NETWORK=base` for real money.

Before real money: prove wallet ownership with a signature, move to a production web framework and host, add login rate limiting, and get legal and tax advice.

## Realistic tiny you (Blender)

Tiny you is built as a realistic miniature in Blender with MPFB (MakeHuman), then rendered as animation frames (idle, blink, sleep, walk) that the desktop app plays.

One-time setup: install MPFB (`Blender --online-mode --command extension install mpfb --enable`), download the asset packs into `assets/makehuman_packs/`, then `Blender --background --python avatar/install_packs.py -- assets/makehuman_packs/*.zip`.

Saving a look in **🎨 Change my look** with style **Realistic** rebuilds tiny you in Blender automatically (about 20 seconds). To build by hand:

```bash
/Applications/Blender.app/Contents/MacOS/Blender --background --python avatar/build_tiny.py -- \
  --look data/look.json --body avatar/body.json --outfit avatar/outfit.json --out data/avatar --sprites
```

- `avatar/body.json`: body shape sliders (MPFB macros)
- `avatar/outfit.json`: which skin, hair, glasses and clothes assets to use
- `avatar/CREDITS.md`: asset creators and licenses (some hair is CC-BY and needs credit)

## Screen help (opt-in)

From tiny you's menu: **👀 Help with my screen** (a one-off look) or **👁 Watch my screen** (glances about once a minute when the screen changes, speaks only when it can help, and switches off after 30 minutes). An 👀 badge shows whenever watching is on. Tiny you never looks while a password, banking or crypto app is in front, and screenshots are never saved. macOS asks for Screen Recording permission the first time.

## No API key yet? Claude Code stands in

When `ANTHROPIC_API_KEY` isn't set and you're not logged in to a server, every AI part (chat, learning, screen help, photo look, and the Scout) runs through the `claude` command-line tool on your own Claude subscription (`tiny/claude_code.py`). Each call gets only the tools it needs: none for chat, web search and page reading for the Scout, and never file or shell access. This is for building and testing; real users' tinys go through the server and the API.

Tiny you also **learns**: after each chat it saves lasting facts about you (`data/learned.json`) and uses them from then on. See and delete them in **🧠 What I know about you**.
