"""Is this token a rug or a trap? Free safety checks before she buys anything.

Solana tokens go through RugCheck (mint/freeze powers, locked liquidity, holder concentration,
insider wallet networks, creator holdings); EVM tokens through GoPlus (honeypots, buy/sell taxes,
hidden owners, owners who can change balances). Each result has:
  ok     - False means never buy, at any size (the hard floor, also enforced for real money)
  risk   - 0 (clean) to 1 (very risky): how much of the soft danger is present
  flags  - plain-English reasons
Results are cached for 20 minutes, since holders and liquidity change fast on new coins.
"""

import json
import time
import urllib.request

from . import life

CACHE = life.DATA / "memelab" / "safety.json"
TTL_S = 20 * 60
GOPLUS_CHAIN = {"base": 8453, "ethereum": 1, "arbitrum": 42161, "optimism": 10, "polygon": 137}
_cache: dict | None = None


def _get(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "mirrorkin"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CACHE.read_text())
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _save() -> None:
    now = time.time()
    keep = {k: v for k, v in _load().items() if now - v["at"] < TTL_S * 6}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(keep))


def safety(chain: str, address: str, cached_only: bool = False) -> dict | None:
    """The token's safety verdict (or None if it couldn't be checked, which callers treat as unsafe)."""
    key = f"{chain}:{address}"
    hit = _load().get(key)
    if hit and time.time() - hit["at"] < TTL_S:
        return hit["result"]
    if cached_only:
        return None
    try:
        result = _solana(address) if chain == "solana" else _evm(chain, address)
    except Exception:
        return None
    _load()[key] = {"at": time.time(), "result": result}
    _save()
    return result


def _verdict(hard: list[str], soft: list[tuple[str, float]], extra: dict) -> dict:
    return {"ok": not hard, "risk": round(min(1.0, sum(w for _, w in soft)), 2),
            "flags": hard + [f for f, _ in soft], **extra}


def _solana(mint: str) -> dict:
    r = _get(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report")
    hard, soft = [], []
    if r.get("rugged"):
        hard.append("already rugged")
    if r.get("mintAuthority"):
        hard.append("creator can still mint more coins")
    if r.get("freezeAuthority"):
        hard.append("creator can freeze holders' coins")
    fee = ((r.get("transferFee") or {}).get("pct") or 0)
    if fee > 5:
        hard.append(f"{fee:.0f}% transfer tax")
    for risk in r.get("risks") or []:
        name = risk.get("name", "")
        if "rugged" in name.lower() and "creator" in name.lower():
            hard.append("creator has rugged tokens before")  # serial ruggers do it again
        elif risk.get("level") == "danger" and "LP" not in name:
            soft.append((f"RugCheck: {name}", 0.25))
    # Pools and lockers hold big chunks legitimately; only count the wallets of people
    pools = set(r.get("knownAccounts") or {})
    for m in r.get("markets") or []:
        pools |= {m.get("pubkey"), m.get("liquidityA"), m.get("liquidityB")}
    people = [h for h in r.get("topHolders") or [] if h.get("address") not in pools and h.get("owner") not in pools]
    top10 = min(100.0, sum(h.get("pct", 0) for h in people[:10]))
    if top10 > 50:
        soft.append((f"top wallets hold {top10:.0f}% of supply", 0.4))
    elif top10 > 30:
        soft.append((f"top wallets hold {top10:.0f}% of supply", 0.2))
    if r.get("graphInsidersDetected"):
        soft.append((f"{r['graphInsidersDetected']} linked insider wallets", 0.3))
    supply = ((r.get("token") or {}).get("supply") or 0)
    creator_pct = (r.get("creatorBalance") or 0) / supply * 100 if supply else 0
    if creator_pct > 5:
        soft.append((f"creator still holds {creator_pct:.0f}%", 0.2))
    lp_locked = max([(m.get("lp") or {}).get("lpLockedPct", 0) or 0 for m in r.get("markets") or []] or [0])
    if lp_locked < 50:
        soft.append((f"only {lp_locked:.0f}% of liquidity locked or burned", 0.3))
    holders_n = r.get("totalHolders") or 0
    if holders_n and holders_n < 100:
        soft.append((f"only {holders_n} holders", 0.1))
    return _verdict(hard, soft, {"top10_pct": round(top10, 1), "holders": holders_n,
                                 "lp_locked_pct": round(lp_locked, 1), "insiders": r.get("graphInsidersDetected") or 0})


def _evm(chain: str, address: str) -> dict:
    data = _get(f"https://api.gopluslabs.io/api/v1/token_security/{GOPLUS_CHAIN[chain]}"
                f"?contract_addresses={address}")["result"]
    t = data.get(address.lower()) or next(iter(data.values()), None)
    if not t:
        raise ValueError("not indexed yet")
    hard, soft = [], []
    yes = lambda k: str(t.get(k, "0")) == "1"
    if yes("is_honeypot"):
        hard.append("honeypot: buyers can't sell")
    if yes("cannot_sell_all"):
        hard.append("holders can't sell everything")
    if yes("owner_change_balance"):
        hard.append("owner can change balances")
    if yes("hidden_owner"):
        hard.append("hidden owner")
    for side in ("buy_tax", "sell_tax"):
        try:
            tax = float(t.get(side) or 0) * 100
        except ValueError:
            tax = 0
        if tax > 10:
            hard.append(f"{tax:.0f}% {side.replace('_', ' ')}")
        elif tax > 3:
            soft.append((f"{tax:.0f}% {side.replace('_', ' ')}", 0.2))
    if yes("is_mintable"):
        soft.append(("owner can mint more", 0.3))
    if not yes("is_open_source"):
        soft.append(("contract code not published", 0.3))
    holders = [h for h in t.get("holders") or [] if not str(h.get("is_contract")) == "1" and not str(h.get("is_locked")) == "1"]
    top10 = sum(float(h.get("percent") or 0) for h in holders[:10]) * 100
    if top10 > 50:
        soft.append((f"top wallets hold {top10:.0f}% of supply", 0.4))
    elif top10 > 30:
        soft.append((f"top wallets hold {top10:.0f}% of supply", 0.2))
    lp_locked = sum(float(h.get("percent") or 0) for h in t.get("lp_holders") or []
                    if str(h.get("is_locked")) == "1" or (h.get("address") or "").lower().startswith("0x000000000000000000000000000000000000dead")) * 100
    if t.get("lp_holders") and lp_locked < 50:
        soft.append((f"only {lp_locked:.0f}% of liquidity locked", 0.2))
    holders_n = int(t.get("holder_count") or 0)
    if holders_n and holders_n < 100:
        soft.append((f"only {holders_n} holders", 0.1))
    return _verdict(hard, soft, {"top10_pct": round(top10, 1), "holders": holders_n,
                                 "lp_locked_pct": round(lp_locked, 1), "insiders": 0})
