"""Shared steps for the command line and the web UI."""

import json
from pathlib import Path

from . import memory as mem
from . import policy

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "profile.json"
# Folders the UI may load saved brain results from.
RESULT_DIRS = [ROOT / "examples", mem.DATA / "imports"]


def load_profile() -> dict | None:
    return json.loads(PROFILE.read_text()) if PROFILE.exists() else None


def result_files() -> list[Path]:
    return sorted(p for d in RESULT_DIRS if d.exists() for p in d.glob("*.json"))


def process_result(profile: dict, today: str, result: dict) -> tuple[Path, dict]:
    """Run the brain's finds through the policy checker, rank them, and save the run."""
    result.setdefault("usage", {"input_tokens": 0, "output_tokens": 0})
    kept, blocked = [], []
    for opp in result["opportunities"]:
        reasons = policy.check(opp, profile)
        if reasons:
            blocked.append({**opp, "blocked_because": reasons})
        else:
            kept.append({**opp, "expected_profit": round(policy.expected_profit(opp), 2),
                         "score": round(policy.score(opp), 2)})
    kept.sort(key=lambda o: o["score"], reverse=True)

    memory = mem.load()
    mem.record_run(memory, result)
    mem.save(memory)
    run = {**result, "opportunities": kept, "blocked": blocked}
    return mem.save_run(today, run), run


def give_feedback(index: int, approved: bool, reason: str, today: str) -> None:
    """Record an approve or reject on an opportunity in the latest run."""
    path, run = mem.latest_run()
    opp = run["opportunities"][index]
    memory = mem.load()
    if "decision" in opp:
        raise ValueError("Already reviewed.")
    opp["decision"] = {"approved": approved, "reason": reason}
    mem.record_feedback(memory, opp, approved, reason, today)
    mem.save(memory)
    path.write_text(json.dumps(run, indent=2))
