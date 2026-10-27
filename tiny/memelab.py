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
POPULATION = 100   # all share the same market data, so more strategies cost nothing extra
EVOLVE_EVERY = timedelta(hours=3)  # eight generations a day
START = 1000.0
FEE = 0.003          # DEX swap fee per side
RUG_LOSS = 0.9       # a rugged token exits at -90% (or worse, if its price fell further)
RUG_PRICE_DROP = 0.8  # price down 80%+ from entry counts as a rug
CHAINS = ("solana", "base")

# Each setting's allowed range: strategies are points in this space. Starting ranges sit around
# what the live market actually shows (most of these tokens have $5k-$40k liquidity), so new
# strategies trade and produce evidence; evolution and her ideas move them from there.
GENES = {
    "min_liq": (2_000, 60_000), "max_age_h": (0.25, 720), "min_m5": (-3, 3), "min_h1": (-10, 15),
    "max_h1": (20, 600), "min_buy_ratio": (0.35, 0.65), "min_vol_liq": (0.0, 0.5),
    "need_boost": (0, 1), "need_socials": (0, 1), "size_pct": (3, 40), "stop": (5, 60),
    "take": (15, 400), "trail": (5, 50), "max_hold_min": (6, 2880), "max_pos": (1, 8),
    "max_risk": (0.1, 0.8),  # how much rug risk (0 clean - 1 very risky) a strategy accepts
}
SAFETY_CHECKS_PER_TICK = 20   # free safety services: stay well inside their limits
JOURNAL = LAB / "journal.json"
TRADES = LAB / "trades.jsonl"   # every lab buy and sell, for the trading log page
_trade_log: list = []
TRACK_H = 24                  # follow each new coin this long to learn how it turned out


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
    """Price, flow and social filters, then the rug check: a coin that fails the hard safety floor
    or is riskier than this strategy accepts is never bought."""
    safe = q.get("safety")
    return (looks_good(g, q) and safe is not None and safe["ok"] and safe["risk"] <= g["max_risk"])


def looks_good(g: dict, q: dict) -> bool:
    return (q["liq"] >= g["min_liq"] and q["age_h"] <= g["max_age_h"] and q["m5"] >= g["min_m5"]
            and g["min_h1"] <= q["h1"] <= g["max_h1"] and q["buy_ratio"] >= g["min_buy_ratio"]
            and q["vol_liq"] >= g["min_vol_liq"] and (q["boosted"] or not g["need_boost"])
            and (q["socials"] > 0 or not g["need_socials"]))


def step(s: dict, market: dict, now: datetime) -> None:
    g = s["genome"]
    # Exits first
    for key, p in list(s["pos"].items()):
        q = market.get(key)
        # A rug is judged by price (collapsed 80%+) or the coin vanishing for 10+ minutes. A $0
        # liquidity reading alone is not a rug: DexScreener shows that while a coin moves from its
        # launch curve to a regular pool.
        if q is None:
            p["missing"] = p.get("missing", 0) + 1
            if p["missing"] * TICK_S < 600:
                continue
        collapsed = q is not None and q["price"] <= p["entry"] * (1 - RUG_PRICE_DROP)
        if q is None or collapsed:
            ret = min(-RUG_LOSS, q["price"] / p["entry"] - 1) if q else -RUG_LOSS
            s["cash"] += p["usd"] * (1 + ret)
            _close(s, p, ret)
            _log("sell", s, key, p["symbol"], ret=ret, now=now,
                 reason="rugged: price collapsed" if q else "rugged: disappeared from the market")
            del s["pos"][key]
            continue
        p["missing"], p["last"] = 0, q["price"]
        p["high"] = max(p["high"], q["price"])
        ret = q["price"] / p["entry"] - 1
        held = (now - datetime.fromisoformat(p["at"])).total_seconds() / 60
        reason = ("stop loss" if ret <= -g["stop"] / 100 else "take profit" if ret >= g["take"] / 100
                  else "trailing stop" if q["price"] <= p["high"] * (1 - g["trail"] / 100) and ret > 0
                  else "time limit" if held >= g["max_hold_min"] else None)
        if reason:
            value = p["usd"] * (1 + ret)
            s["cash"] += value * (1 - _impact(value, q["liq"] or p["entry_liq"]))
            _close(s, p, ret)
            _log("sell", s, key, p["symbol"], ret=ret, reason=reason, now=now, usd=value)
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
        _log("buy", s, key, q["symbol"], now=now, usd=usd, price=q["price"], risk=(q.get("safety") or {}).get("risk"),
             reason=f"age {q['age_h']:.1f}h, 1h {q['h1']:+.0f}%, buyers {q['buy_ratio']:.0%}")
    s["value"] = round(s["cash"] + _held(s, market), 2)
    s["peak"] = max(s["peak"], s["value"])
    s["worst_dd"] = round(min(s["worst_dd"], s["value"] / s["peak"] - 1), 4)


def _log(side: str, s: dict, key: str, symbol: str, *, now: datetime, reason: str, ret: float | None = None,
         usd: float | None = None, risk: float | None = None, price: float | None = None) -> None:
    _trade_log.append({"time": now.isoformat(timespec="seconds"), "side": side, "strategy": s["id"], "token": key,
                       "symbol": symbol, "usd": round(usd, 2) if usd is not None else None,
                       "ret_pct": round(ret * 100, 1) if ret is not None else None, "risk": risk, "reason": reason,
                       "price": price})


def _flush_trades() -> None:
    if not _trade_log:
        return
    LAB.mkdir(parents=True, exist_ok=True)
    with TRADES.open("a") as f:
        f.writelines(json.dumps(t) + "\n" for t in _trade_log)
    _trade_log.clear()
    if TRADES.stat().st_size > 5_000_000:  # keep the newest trades
        lines = TRADES.read_text().splitlines()[-20000:]
        TRADES.write_text("\n".join(lines) + "\n")


def recent_trades(n: int = 100) -> list[dict]:
    if not TRADES.exists():
        return []
    with TRADES.open() as f:
        lines = f.readlines()[-n:]
    return [json.loads(line) for line in reversed(lines) if line.strip()]


def _held(s: dict, market: dict) -> float:
    total = 0.0
    for key, p in s["pos"].items():
        q = market.get(key)
        price = q["price"] if q else p.get("last", p["entry"])
        total += p["usd"] * (price / p["entry"])
    return total


def _close(s: dict, p: dict, ret: float) -> None:
    s["trades"] += 1
    s["wins"] += ret > 0


# ---- the lab ----

def load() -> dict:
    if STATE.exists():
        lab = json.loads(STATE.read_text())
        for s in lab["strategies"]:   # strategies from before a setting existed get its middle value
            s["genome"] = _clean(s["genome"])
        return lab
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
    keep = ranked[: len(ranked) * 7 // 10]   # drop the worst 30%, then refill to POPULATION
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
    journal = _load_journal()
    for key in journal:                             # keep following coins to see how they turn out
        chain, addr = key.split(":", 1)
        if not journal[key].get("done"):
            watched.setdefault(chain, set()).add(addr)
    market = quotes(watched)
    if not market:
        return lab
    now = datetime.now()
    _check_safety(lab, market)
    for s in lab["strategies"]:
        step(s, market, now)
    _update_journal(journal, market, now)
    _flush_trades()
    lab["ticks"] += 1
    lab["last_tick"] = now.isoformat(timespec="seconds")
    lab["tokens_seen"] = len(market)
    if now - datetime.fromisoformat(lab["evolved_at"]) >= EVOLVE_EVERY:
        evolve(lab)
        life.log("memelab evolved", f"generation {lab['generation']}")
    save(lab)
    return lab


def _check_safety(lab: dict, market: dict) -> None:
    """Attach rug checks: cached ones for everything, fresh ones for the coins strategies want most,
    plus brand-new coins for the journal."""
    from . import rugcheck
    wanted, fresh = [], []
    for key, q in market.items():
        chain, addr = key.split(":", 1)
        q["safety"] = rugcheck.safety(chain, addr, cached_only=True)
        if q["safety"] is None:
            if any(looks_good(s["genome"], q) for s in lab["strategies"]):
                wanted.append(key)
            elif q["age_h"] < 6:
                fresh.append(key)
    for key in (wanted + fresh)[:SAFETY_CHECKS_PER_TICK]:
        chain, addr = key.split(":", 1)
        market[key]["safety"] = rugcheck.safety(chain, addr)


# ---- coin journal: what early signs actually predicted runners and rugs ----

def _load_journal() -> dict:
    try:
        return json.loads(JOURNAL.read_text())
    except (OSError, ValueError):
        return {}


def _update_journal(journal: dict, market: dict, now: datetime) -> None:
    for key, q in market.items():
        e = journal.get(key)
        if e is None:
            if q["age_h"] > 72:          # only learn from coins caught early
                continue
            journal[key] = e = {"symbol": q["symbol"], "seen": now.isoformat(timespec="seconds"),
                                "first": {k: q[k] for k in ("price", "liq", "age_h", "m5", "h1", "buy_ratio",
                                                            "vol_liq", "boosted", "socials")},
                                "peak": 0.0, "low": 0.0, "last": 0.0, "at": {}}
        if e.get("done"):
            continue
        if q.get("safety") and "safety" not in e:
            e["safety"] = {k: q["safety"][k] for k in ("ok", "risk", "top10_pct", "lp_locked_pct", "insiders")}
        ret = q["price"] / e["first"]["price"] - 1
        e["peak"], e["low"], e["last"] = round(max(e["peak"], ret), 3), round(min(e["low"], ret), 3), round(ret, 3)
        hours = (now - datetime.fromisoformat(e["seen"])).total_seconds() / 3600
        for mark in (1, 6, 24):
            if hours >= mark and str(mark) not in e["at"]:
                e["at"][str(mark)] = round(ret, 3)
        if ret <= -RUG_PRICE_DROP:   # judged by price; a $0 liquidity reading is often just a pool move
            e["rugged"] = True
        if hours >= TRACK_H or e.get("rugged"):
            e["done"] = True
    # Coins that vanish from the market for a day count as dead
    for e in journal.values():
        if not e.get("done") and (now - datetime.fromisoformat(e["seen"])).total_seconds() > TRACK_H * 3600 * 1.5:
            e["done"], e["rugged"] = True, True
    # Keep the newest few thousand
    if len(journal) > 4000:
        for key in sorted(journal, key=lambda k: journal[k]["seen"])[:len(journal) - 4000]:
            del journal[key]
    LAB.mkdir(parents=True, exist_ok=True)
    tmp = JOURNAL.with_suffix(".tmp")
    tmp.write_text(json.dumps(journal))
    tmp.replace(JOURNAL)


def _outcome(e: dict) -> str:
    if e.get("rugged") or e["last"] <= -0.8:
        return "rug"
    return "runner" if e["peak"] >= 0.5 else "flat"


def lessons() -> dict:
    """What early signs went with runners (+50% within a day) and rugs, from coins she followed."""
    done = [e for e in _load_journal().values() if e.get("done")]
    if len(done) < 20:
        return {"coins_followed": len(done), "note": "Still collecting: lessons start after 20 finished coins."}
    groups = {
        "passed rug check (risk <= 0.3)": lambda e: e.get("safety") and e["safety"]["ok"] and e["safety"]["risk"] <= 0.3,
        "failed rug check or risky": lambda e: e.get("safety") and (not e["safety"]["ok"] or e["safety"]["risk"] > 0.5),
        "top wallets hold > 30%": lambda e: e.get("safety") and e["safety"]["top10_pct"] > 30,
        "liquidity locked/burned": lambda e: e.get("safety") and e["safety"]["lp_locked_pct"] >= 90,
        "caught under 1h old": lambda e: e["first"]["age_h"] < 1,
        "caught 1-6h old": lambda e: 1 <= e["first"]["age_h"] < 6,
        "buyers > 60% of trades": lambda e: e["first"]["buy_ratio"] > 0.6,
        "already up > 30% in the hour": lambda e: e["first"]["h1"] > 30,
        "heavy volume (vol/liq > 0.5)": lambda e: e["first"]["vol_liq"] > 0.5,
        "boosted (paid promotion)": lambda e: e["first"]["boosted"],
        "has socials": lambda e: e["first"]["socials"] > 0,
        "no socials": lambda e: e["first"]["socials"] == 0,
        "liquidity under $10k": lambda e: e["first"]["liq"] < 10_000,
        "liquidity over $50k": lambda e: e["first"]["liq"] >= 50_000,
    }
    base = {o: sum(_outcome(e) == o for e in done) / len(done) for o in ("runner", "rug")}
    rows = []
    for name, test in groups.items():
        members = [e for e in done if test(e)]
        if len(members) >= 8:
            rows.append({"signal": name, "coins": len(members),
                         "runner_pct": round(sum(_outcome(e) == "runner" for e in members) / len(members) * 100),
                         "rug_pct": round(sum(_outcome(e) == "rug" for e in members) / len(members) * 100)})
    rows.sort(key=lambda r: r["runner_pct"] - r["rug_pct"], reverse=True)
    return {"coins_followed": len(done), "all_coins": {"runner_pct": round(base["runner"] * 100),
                                                       "rug_pct": round(base["rug"] * 100)}, "signals": rows}


def watchlist(n: int = 8) -> list[dict]:
    """Fresh coins (under 6h old when found, still being followed) that passed the rug check, best early signs first."""
    out = []
    for key, e in _load_journal().items():
        safe = e.get("safety")
        if (e.get("done") or not safe or not safe["ok"] or safe["risk"] > 0.3 or e["first"]["age_h"] >= 6
                or e["first"]["liq"] < 3_000):  # still on a launch curve: not reliably buyable yet
            continue
        f = e["first"]
        out.append({"token": key, "symbol": e["symbol"], "found": e["seen"], "since_found_pct": round(e["last"] * 100, 1),
                    "peak_pct": round(e["peak"] * 100, 1), "risk": safe["risk"], "top10_pct": safe["top10_pct"],
                    "buy_ratio": round(f["buy_ratio"], 2), "liq": round(f["liq"]), "socials": f["socials"]})
    out.sort(key=lambda w: (w["buy_ratio"], -w["risk"]), reverse=True)
    return out[:n]


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
        "lessons": lessons(), "watchlist_safe_fresh_coins": watchlist(),
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
