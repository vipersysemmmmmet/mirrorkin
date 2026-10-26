"""Scout command line.

  python -m scout ui               open the web page at http://localhost:8000
  python -m scout run              find and rank opportunities, write the daily text
  python -m scout run FILE.json    same, but read the brain's results from a file (no API call)
  python -m scout review           approve or reject the latest finds so the clone learns
  python -m scout memory           show what the clone remembers
"""

import json
import os
import sys
from datetime import date
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from . import memory as mem
from .brain import ScoutError, run_scout
from .pipeline import ROOT, load_profile, process_result

NO_PROFILE = "No profile.json yet. Copy profile.example.json to profile.json and fill it in."


def scout_with_api(profile: dict, today: str) -> dict:
    """One live brain run. Raises RuntimeError with a readable message on failure."""
    from tiny import claude_code
    if not os.environ.get("ANTHROPIC_API_KEY") and not claude_code.available():
        raise RuntimeError("No Anthropic API key or Claude Code. Add ANTHROPIC_API_KEY to .env, "
                           "or run from a results file.")
    try:
        return run_scout(profile, mem.summary(mem.load()), today)
    except ScoutError as e:
        raise RuntimeError(f"Scout run failed: {e}")
    except anthropic.AuthenticationError:
        raise RuntimeError("Missing or invalid Anthropic API key. Check ANTHROPIC_API_KEY in .env.")
    except anthropic.RateLimitError:
        raise RuntimeError("Rate limited by the API. Try again in a minute.")
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"API error ({e.status_code}): {e.message}")
    except anthropic.APIConnectionError:
        raise RuntimeError("Network error. Check your internet connection.")


def cmd_run(results_file: str | None = None) -> None:
    profile = load_profile() or sys.exit(NO_PROFILE)
    today = date.today().isoformat()
    if results_file:
        result = json.loads(Path(results_file).read_text())
    else:
        print("Tiny you is out scouting (this can take a few minutes)...")
        try:
            result = scout_with_api(profile, today)
        except RuntimeError as e:
            sys.exit(str(e))
    path, run = process_result(profile, today, result)

    print("\n--- Text from tiny you ---\n")
    print(run["daily_text"])
    print("\n--- Ideas it came up with ---")
    for i in run["ideas"]:
        print(f"- {i['idea']}: {i['why_it_fits']}")
    print(f"\n--- {len(run['opportunities'])} opportunities, best first ---")
    for n, o in enumerate(run["opportunities"], 1):
        print(f"\n{n}. {o['title']}  [{o['kind']}]")
        money = (f"pay ${o['pay_usd']:.0f} x {o['success_chance']:.0%} - cost ${o['cost_usd']:.0f}"
                 f" = ${o['expected_profit']:.0f} expected") if o["pay_usd"] > 0 else "pay amount unknown"
        print(f"   {money} | your time {o['human_hours']}h | done by {o['who_does_it']}")
        if o["deadline"]:
            print(f"   deadline: {o['deadline']}")
        print(f"   {o['summary']}")
        print(f"   next: {o['next_step']}")
        print(f"   {o['url']}")
    if run["blocked"]:
        print(f"\n--- {len(run['blocked'])} blocked by the policy checker ---")
        for o in run["blocked"]:
            print(f"- {o['title']}: {', '.join(o['blocked_because'])}")
    u = run["usage"]
    print(f"\nSaved to {path} | tokens in {u['input_tokens']}, out {u['output_tokens']}")
    print("Run `python -m scout review` to teach it what you like.")


def cmd_review() -> None:
    found = mem.latest_run()
    if not found:
        sys.exit("No runs yet. Run `python -m scout run` first.")
    path, run = found
    memory = mem.load()
    today = date.today().isoformat()
    todo = [o for o in run["opportunities"] if "decision" not in o]
    if not todo:
        print("Everything in the latest run is already reviewed.")
        return
    print("For each find: y = approve, n = reject, s = skip, q = quit. Then an optional reason.\n")
    for o in todo:
        print(f"{o['title']}  [{o['kind']}]  ~${o['expected_profit']:.0f}\n  {o['url']}")
        answer = input("  y/n/s/q? ").strip().lower()
        if answer == "q":
            break
        if answer not in ("y", "n"):
            continue
        reason = input("  why (optional)? ").strip()
        o["decision"] = {"approved": answer == "y", "reason": reason}
        mem.record_feedback(memory, o, answer == "y", reason, today)
    mem.save(memory)
    path.write_text(json.dumps(run, indent=2))
    print("\nThanks! Tiny you will use this on the next run.")


def cmd_memory() -> None:
    print(mem.summary(mem.load()))


def cmd_ui() -> None:
    from .web import serve
    serve()


def main() -> None:
    load_dotenv(ROOT / ".env")
    commands = {"run": cmd_run, "review": cmd_review, "memory": cmd_memory, "ui": cmd_ui}
    args = sys.argv[1:]
    if args[:1] == ["run"] and len(args) == 2:
        cmd_run(args[1])
    elif len(args) == 1 and args[0] in commands:
        commands[args[0]]()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
