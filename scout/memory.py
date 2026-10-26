"""What the clone remembers between runs: sources, feedback and lessons."""

import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

DATA = Path(__file__).resolve().parent.parent / "data"
MEMORY_FILE = DATA / "memory.json"
RUNS = DATA / "runs"


def load() -> dict:
    if MEMORY_FILE.exists():
        return json.loads(MEMORY_FILE.read_text())
    return {"sources": {}, "feedback": [], "lessons": []}


def save(memory: dict) -> None:
    DATA.mkdir(exist_ok=True)
    MEMORY_FILE.write_text(json.dumps(memory, indent=2))


def domain(url: str) -> str:
    return urlparse(url).netloc.removeprefix("www.") or url


def _source(memory: dict, url: str, what: str = "") -> dict:
    src = memory["sources"].setdefault(
        domain(url), {"what": what, "found": 0, "approved": 0, "rejected": 0})
    if what and not src["what"]:
        src["what"] = what
    return src


def record_run(memory: dict, result: dict) -> None:
    for s in result["new_sources"]:
        _source(memory, s["url"], s["what"])
    for opp in result["opportunities"]:
        _source(memory, opp["url"])["found"] += 1
    # Keep recent lessons only, so memory stays short and current.
    memory["lessons"] = (memory["lessons"] + result["lessons"])[-20:]


def record_feedback(memory: dict, opp: dict, approved: bool, reason: str, today: str) -> None:
    memory["feedback"].append({
        "date": today, "title": opp["title"], "kind": opp["kind"], "idea": opp["idea"],
        "source": domain(opp["url"]), "approved": approved, "reason": reason,
    })
    _source(memory, opp["url"])["approved" if approved else "rejected"] += 1


def summary(memory: dict) -> str:
    """Short text version of memory for the brain's prompt."""
    if not (memory["sources"] or memory["feedback"]):
        return "First run: no memory yet."
    lines = []
    fb = memory["feedback"]
    if fb:
        liked = Counter(f["kind"] for f in fb if f["approved"])
        disliked = Counter(f["kind"] for f in fb if not f["approved"])
        lines.append(f"Kinds my human approved: {dict(liked.most_common(8))}")
        lines.append(f"Kinds my human rejected: {dict(disliked.most_common(8))}")
        lines.append("Recent decisions:")
        for f in fb[-15:]:
            verdict = "approved" if f["approved"] else "rejected"
            why = f" ({f['reason']})" if f["reason"] else ""
            lines.append(f"- {verdict}: {f['title']} [{f['kind']}, {f['source']}]{why}")
    ranked = sorted(memory["sources"].items(),
                    key=lambda kv: kv[1]["approved"] - kv[1]["rejected"], reverse=True)
    lines.append("Known sources (approved/rejected/found):")
    for name, s in ranked[:20]:
        lines.append(f"- {name}: {s['approved']}/{s['rejected']}/{s['found']} {s['what']}")
    if memory["lessons"]:
        lines.append("My lessons so far:")
        lines += [f"- {l}" for l in memory["lessons"][-10:]]
    return "\n".join(lines)


def save_run(today: str, run: dict) -> Path:
    RUNS.mkdir(parents=True, exist_ok=True)
    path = RUNS / f"{today}.json"
    path.write_text(json.dumps(run, indent=2))
    return path


def latest_run() -> tuple[Path, dict] | None:
    runs = sorted(RUNS.glob("*.json")) if RUNS.exists() else []
    if not runs:
        return None
    return runs[-1], json.loads(runs[-1].read_text())
