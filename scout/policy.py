"""Policy checker: plain rules outside the AI.

The brain can only suggest opportunities. Nothing reaches the human unless it
passes these checks, and nothing the clone reads on the web can change them.
"""

MIN_LEGITIMACY = 0.6

# Backstop for things the brain should never suggest, even if it mislabels them.
BLOCKED_TERMS = [
    "multiple accounts", "sybil", "airdrop farm", "click farm", "paid to click",
    "fake review", "referral spam", "mass dm", "cold spam",
    "send a deposit", "pay to join", "starter kit fee",
]
# Allowed only when the human opts in with allow_trading.
TRADING_TERMS = ["casino", "betting", "sports bet", "memecoin", "pump.fun", "leverage trading"]


def check(opp: dict, profile: dict) -> list[str]:
    """Return the reasons this opportunity is blocked; empty means allowed."""
    reasons = []
    if not opp["url"].startswith(("http://", "https://")):
        reasons.append("no source link")
    if opp["legitimacy"] < MIN_LEGITIMACY:
        reasons.append(f"legitimacy {opp['legitimacy']:.1f} below {MIN_LEGITIMACY}")
    if opp["requires_upfront_payment"]:
        # Only within the human's own limit, and only for clearly legitimate things
        limit = profile.get("max_upfront_usd", 0)
        if opp["cost_usd"] > limit or opp["legitimacy"] < 0.8:
            reasons.append(f"asks for money up front (your limit is ${limit})")
    if opp["involves_trading_or_gambling"] and not profile.get("allow_trading", False):
        reasons.append("trading or gambling not opted in")
    if opp["clone_would_pose_as_human"]:
        reasons.append("clone would pose as its human")
    text = f"{opp['title']} {opp['kind']} {opp['summary']}".lower()
    terms = BLOCKED_TERMS if profile.get("allow_trading", False) else BLOCKED_TERMS + TRADING_TERMS
    reasons += [f"blocked term: {t}" for t in terms if t in text]
    # Pay of 0 means "unknown" (e.g. a pro-rata settlement share, an empty job board today),
    # not "pays nothing", so profit can't be judged. Only block a known loss.
    if not pay_unknown(opp) and opp["cost_usd"] > 0 and expected_profit(opp) <= 0:
        reasons.append("costs more than it is likely to pay")
    return reasons


def pay_unknown(opp: dict) -> bool:
    return opp["pay_usd"] <= 0


# Used only to rank finds whose pay is unknown; never shown as a promised amount.
UNKNOWN_PAY_FOR_RANKING = 20


def expected_profit(opp: dict) -> float:
    return opp["pay_usd"] * opp["success_chance"] - opp["cost_usd"]


def score(opp: dict) -> float:
    """Rank: likely profit, weighted by fit and legitimacy, per hour of the human's time."""
    pay = UNKNOWN_PAY_FOR_RANKING if pay_unknown(opp) else opp["pay_usd"]
    profit = pay * opp["success_chance"] - opp["cost_usd"]
    return profit * (opp["fit"] / 10) * opp["legitimacy"] / (1 + opp["human_hours"])
