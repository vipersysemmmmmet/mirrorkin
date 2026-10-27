"""Memecoin strategy lab: many strategies paper-trade the live market at once and evolve.

Every couple of minutes it pulls fresh launches, trending pools and promoted ("boosted")
tokens on Solana and Base, with their social links, buy/sell flow, liquidity and age. Each
strategy in the population trades its own $1,000 of pretend money on that live data, paying
fees, price impact and rug losses. Once a day the worst strategies are replaced with variations
of the best. Only results on live data after a strategy was created count, so it can't win by
fitting the past.

Runs as plain code with no AI calls, so testing costs no credit. Her hourly trading session
reads the leaderboard and can add strategy ideas of her own. Paper only: there is no wallet
or transaction code here.
"""

import json
import random
import threading
import time
import urllib.request
from datetime import datetime, timedelta

from . import life

LAB = life.DATA / "memelab"
STATE = LAB / "state.json"
TICK_S = 120
POPULATION = 40
EVOLVE_EVERY = timedelta(hours=24)
START = 1000.0
FEE = 0.003          # DEX swap fee per side
RUG_LOSS = 0.9       # a token that vanishes or loses its liquidity exits at -90%
CHAINS = ("solana", "base")

# Each setting's allowed range: strategies are points in this space. Starting ranges sit around
# what the live market actually shows (most of these tokens have $5k-$40k liquidity), so new
# strategies trade and produce evidence; evolution and her ideas move them from there.
GENES = {
    "min_liq": (2_000, 60_000), "max_age_h": (0.25, 720), "min_m5": (-3, 3), "min_h1": (-10, 15),
    "max_h1": (20, 600), "min_buy_ratio": (0.35, 0.65), "min_vol_liq": (0.0, 0.5),
    "need_boost": (0, 1), "need_socials": (0, 1), "size_pct": (3, 40), "stop": (5, 60),
    "take": (15, 400), "trail": (5, 50), "max_hold_min": (6, 2880), "max_pos": (1, 8),
}


def _get(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "mirrorkin-lab"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


# ---- market data ----

def discover() -> dict[str, set]:
    """Token addresses worth watching right now, per chain."""
    found = {c: set() for c in CHAINS}
    for url in ("https://api.dexscreener.com/token-boosts/latest/v1",
                "https://api.dexscreener.com/token-boosts/top/v1",
                "https://api.dexscreener.com/token-profiles/latest/v1"):
        try:
            for t in _get(url):
                if t.get("chainId") in found:
                    found[t["chainId"]].add(t["tokenAddress"])
        except Exception:
            pass
    for chain in CHAINS:
        for kind in ("new_pools", "trending_pools"):
            try:
                for p in _get(f"https://api.geckoterminal.com/api/v2/networks/{chain}/{kind}?page=1")["data"]:
                    base = p["relationships"]["base_token"]["data"]["id"]  # "solana_<address>"
                    found[chain].add(base.split("_", 1)[1])
            except Exception:
                pass
    return found


def quotes(tokens: dict[str, set]) -> dict[str, dict]:
    """Live numbers for each token, from its most liquid pool."""
    out = {}
    for chain, addrs in tokens.items():
        addrs = list(addrs)
        for i in range(0, len(addrs), 30):
            try:
                pairs = _get(f"https://api.dexscreener.com/tokens/v1/{chain}/{','.join(addrs[i:i + 30])}")
            except Exception:
                continue
            for p in pairs:
                key = f"{chain}:{p['baseToken']['address']}"
                liq = (p.get("liquidity") or {}).get("usd") or 0
                if key in out and out[key]["liq"] >= liq:
                    continue
                tx = p.get("txns") or {}
                h1 = tx.get("h1") or {}
                buys, sells = h1.get("buys", 0), h1.get("sells", 0)
                change = p.get("priceChange") or {}
                created = p.get("pairCreatedAt")
                try:
                    price = float(p.get("priceUsd") or 0)
                except ValueError:
                    price = 0
                if price <= 0:
                    continue
                out[key] = {
                    "symbol": p["baseToken"].get("symbol", "?"), "price": price, "liq": liq,
                    "age_h": (time.time() * 1000 - created) / 3.6e6 if created else 9999,
                    "m5": change.get("m5") or 0, "h1": change.get("h1") or 0, "h6": change.get("h6") or 0,
                    "buy_ratio": buys / (buys + sells) if buys + sells else 0.5,
                    "vol_liq": ((p.get("volume") or {}).get("h1") or 0) / liq if liq else 0,
                    "boosted": bool((p.get("boosts") or {}).get("active")),
                    "socials": len((p.get("info") or {}).get("socials") or []),
                    "url": p.get("url", ""),
                }
    return out


# ---- strategies ----

def random_genome() -> dict:
    g = {k: random.uniform(lo, hi) for k, (lo, hi) in GENES.items()}
    return _clean(g)


def _clean(g: dict) -> dict:
    out = {}
    for k, (lo, hi) in GENES.items():
        v = min(hi, max(lo, float(g.get(k, (lo + hi) / 2))))
        out[k] = round(v) if k in ("need_boost", "need_socials", "max_pos") else round(v, 3)
    out["max_h1"] = max(out["max_h1"], out["min_h1"] + 5)
    return out


def mutate(g: dict) -> dict:
    child = dict(g)
    for k in random.sample(list(GENES), 3):
        lo, hi = GENES[k]
        child[k] = g[k] + random.gauss(0, (hi - lo) * 0.15)
    return _clean(child)


def new_strategy(genome: dict, origin: str) -> dict:
    return {"id": f"s{random.randrange(16**6):06x}", "genome": _clean(genome), "origin": origin,
            "born": datetime.now().isoformat(timespec="seconds"), "cash": START, "pos": {},
            "trades": 0, "wins": 0, "peak": START, "worst_dd": 0.0, "value": START}


def _impact(usd: float, liq: float) -> float:
    """Fee plus price impact: bigger orders in thinner pools fill worse."""
    return FEE + min(0.25, usd / max(liq, 1) * 2)


def wants(g: dict, q: dict) -> bool:
    return (q["liq"] >= g["min_liq"] and q["age_h"] <= g["max_age_h"] and q["m5"] >= g["min_m5"]
            and g["min_h1"] <= q["h1"] <= g["max_h1"] and q["buy_ratio"] >= g["min_buy_ratio"]
            and q["vol_liq"] >= g["min_vol_liq"] and (q["boosted"] or not g["need_boost"])
            and (q["socials"] > 0 or not g["need_socials"]))


def step(s: dict, market: dict, now: datetime) -> None:
    g = s["genome"]
    # Exits first
    for key, p in list(s["pos"].items()):
        q = market.get(key)
        if q is None or q["liq"] < p["entry_liq"] * 0.2:      # pulled liquidity or delisted: a rug
            s["cash"] += p["usd"] * (1 - RUG_LOSS)
            _close(s, p, -RUG_LOSS)
            del s["pos"][key]
            continue
        p["high"] = max(p["high"], q["price"])
        ret = q["price"] / p["entry"] - 1
        held = (now - datetime.fromisoformat(p["at"])).total_seconds() / 60
        if (ret <= -g["stop"] / 100 or ret >= g["take"] / 100
                or q["price"] <= p["high"] * (1 - g["trail"] / 100) and ret > 0
                or held >= g["max_hold_min"]):
            value = p["usd"] * (1 + ret)
            s["cash"] += value * (1 - _impact(value, q["liq"]))
            _close(s, p, ret)
            del s["pos"][key]
    # Entries
    for key, q in sorted(market.items(), key=lambda kv: -kv[1]["vol_liq"]):
        if len(s["pos"]) >= g["max_pos"]:
            break
        if key in s["pos"] or not wants(g, q):
            continue
        usd = min(s["cash"], (s["cash"] + _held(s, market)) * g["size_pct"] / 100)
        if usd < 5:
            break
        s["cash"] -= usd
        s["pos"][key] = {"symbol": q["symbol"], "usd": usd * (1 - _impact(usd, q["liq"])),
                         "entry": q["price"], "high": q["price"], "entry_liq": q["liq"],
                         "at": now.isoformat(timespec="seconds")}
    s["value"] = round(s["cash"] + _held(s, market), 2)
    s["peak"] = max(s["peak"], s["value"])
    s["worst_dd"] = round(min(s["worst_dd"], s["value"] / s["peak"] - 1), 4)


def _held(s: dict, market: dict) -> float:
    total = 0.0
    for key, p in s["pos"].items():
        q = market.get(key)
        total += p["usd"] * (q["price"] / p["entry"]) if q else p["usd"] * (1 - RUG_LOSS)
    return total


def _close(s: dict, p: dict, ret: float) -> None:
    s["trades"] += 1
    s["wins"] += ret > 0


# ---- the lab ----

def load() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"strategies": [new_strategy(random_genome(), "random") for _ in range(POPULATION)],
            "generation": 1, "evolved_at": datetime.now().isoformat(timespec="seconds"),
            "hall_of_fame": [], "ticks": 0}


def save(lab: dict) -> None:
    LAB.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(lab))
    tmp.replace(STATE)


def score(s: dict) -> float:
    """Return, punished for deep drawdowns; strategies that never traded rank last."""
    if s["trades"] < 3:
        return -999
    return (s["value"] / START - 1) * 100 + s["worst_dd"] * 50


def evolve(lab: dict) -> None:
    ranked = sorted(lab["strategies"], key=score, reverse=True)
    keep = ranked[: POPULATION * 7 // 10]
    best = ranked[: max(3, POPULATION // 5)]
    if best and score(best[0]) > -999:
        lab["hall_of_fame"] = (lab["hall_of_fame"] + [{
            "generation": lab["generation"], "id": best[0]["id"], "genome": best[0]["genome"],
            "return_pct": round((best[0]["value"] / START - 1) * 100, 1), "trades": best[0]["trades"],
            "win_rate": round(best[0]["wins"] / best[0]["trades"] * 100), "worst_dd_pct": round(best[0]["worst_dd"] * 100, 1),
        }])[-30:]
    children = [new_strategy(mutate(random.choice(best)["genome"]), "mutation")
                for _ in range(POPULATION - len(keep) - 2)]
    lab["strategies"] = keep + children + [new_strategy(random_genome(), "random") for _ in range(2)]
    lab["generation"] += 1
    lab["evolved_at"] = datetime.now().isoformat(timespec="seconds")


def tick() -> dict:
    lab = load()
    watched = discover()
    for s in lab["strategies"]:                     # always re-price what's held
        for key in s["pos"]:
            chain, addr = key.split(":", 1)
            watched.setdefault(chain, set()).add(addr)
    market = quotes(watched)
    if not market:
        return lab
    now = datetime.now()
    for s in lab["strategies"]:
        step(s, market, now)
    lab["ticks"] += 1
    lab["last_tick"] = now.isoformat(timespec="seconds")
    lab["tokens_seen"] = len(market)
    if now - datetime.fromisoformat(lab["evolved_at"]) >= EVOLVE_EVERY:
        evolve(lab)
        life.log("memelab evolved", f"generation {lab['generation']}")
    save(lab)
    return lab


def add_ideas(ideas: list) -> int:
    """Strategies she designs herself join the population (replacing the current worst)."""
    lab = load()
    added = 0
    for idea in ideas[:5]:
        if isinstance(idea, dict):
            lab["strategies"].sort(key=score)
            lab["strategies"][0] = new_strategy(idea, "her idea")
            added += 1
    if added:
        save(lab)
    return added


def leaderboard(n: int = 5) -> dict:
    """Short summary for her trading session and for the human."""
    if not STATE.exists():
        return {}
    lab = load()
    ranked = sorted(lab["strategies"], key=score, reverse=True)
    return {
        "generation": lab["generation"], "ticks": lab["ticks"], "tokens_seen": lab.get("tokens_seen"),
        "benchmark": "Holding USDC = 0%. A strategy must beat that after fees and rugs.",
        "top": [{"id": s["id"], "origin": s["origin"], "return_pct": round((s["value"] / START - 1) * 100, 1),
                 "trades": s["trades"], "win_rate": round(s["wins"] / s["trades"] * 100) if s["trades"] else None,
                 "worst_dd_pct": round(s["worst_dd"] * 100, 1), "genome": s["genome"]} for s in ranked[:n]],
        "past_champions": lab["hall_of_fame"][-5:],
        "settings": {k: list(v) for k, v in GENES.items()},
    }


_thread = None


def start() -> None:
    """Run the lab in the background for as long as the app is open."""
    global _thread
    if _thread and _thread.is_alive():
        return

    def run():
        while True:
            try:
                tick()
            except Exception as e:  # a bad response must never stop the lab
                life.log("memelab tick failed", str(e)[:200])
            time.sleep(TICK_S)

    _thread = threading.Thread(target=run, daemon=True, name="memelab")
    _thread.start()
