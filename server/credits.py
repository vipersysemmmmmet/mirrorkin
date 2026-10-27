"""Tiny credits, bought with USDC sent to the pool.

Each purchase is verified on-chain, then split in the books: the team fee (a percentage set
in .env) is owed to the team address, and the rest becomes credits at a fixed rate. The fee
and team address in force at the time are saved with every purchase, so changing them in .env
only affects new purchases. Moving the team's share out of the pool is done by the team from
its own wallet; the server never holds keys.
"""

import os
import sqlite3

from . import db
from .chain import ADDRESS_RE, verify_usdc_payment
from .config import USDC_DECIMALS, network, pay_to

SCHEMA = """
CREATE TABLE IF NOT EXISTS credit_purchases (
    tx_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, amount INTEGER NOT NULL,
    fee_bps INTEGER NOT NULL, fee INTEGER NOT NULL, team_address TEXT NOT NULL,
    pool_address TEXT NOT NULL, credits INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS credit_balances (
    user_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS credit_spends (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, credits INTEGER NOT NULL,
    reason TEXT NOT NULL, created_at TEXT NOT NULL);
"""

# What things cost once a plan's free allowance for the day or week is used up
COSTS = {"chats": 1, "scouts": 10}
MAX_FEE_PCT = 50


class CreditError(db.AccountError):
    pass


def pool_address() -> str:
    """Where people send USDC for credits (falls back to the subscription address)."""
    return os.environ.get("SOUL_POOL_ADDRESS", "").strip() or pay_to()


def team_address() -> str:
    return os.environ.get("SOUL_TEAM_ADDRESS", "").strip()


def fee_bps() -> int:
    """The team fee in basis points (1% = 100), from SOUL_TEAM_FEE_PCT, e.g. 10 or 2.5."""
    raw = os.environ.get("SOUL_TEAM_FEE_PCT", "10").strip()
    try:
        pct = float(raw)
    except ValueError:
        raise SystemExit(f"SOUL_TEAM_FEE_PCT must be a number like 10 or 2.5, not '{raw}'.")
    if not 0 <= pct <= MAX_FEE_PCT:
        raise SystemExit(f"SOUL_TEAM_FEE_PCT must be between 0 and {MAX_FEE_PCT}.")
    return round(pct * 100)


def credits_per_usdc() -> int:
    return int(os.environ.get("SOUL_CREDITS_PER_USDC", "100"))


def _table() -> None:
    db.db().executescript(SCHEMA)


def quote(amount: int) -> dict:
    """How a payment of `amount` (USDC base units) splits between the team fee and credits."""
    bps = fee_bps()
    fee = amount * bps // 10_000
    return {"fee_bps": bps, "fee": fee, "net": amount - fee,
            "credits": (amount - fee) * credits_per_usdc() // 10**USDC_DECIMALS}


def info(user) -> dict:
    """Everything the panel needs to show the balance and how to buy more."""
    return {"balance": balance(user["id"]), "pool_address": pool_address(),
            "fee_pct": fee_bps() / 100, "credits_per_usdc": credits_per_usdc(), "costs": COSTS,
            "network": network()["id"], "network_label": network()["label"],
            "usdc_contract": network()["usdc"]}


def balance(user_id: int) -> int:
    with db._lock:
        _table()
        row = db.db().execute("SELECT balance FROM credit_balances WHERE user_id = ?", (user_id,)).fetchone()
    return row["balance"] if row else 0


def buy(user, tx_hash: str) -> dict:
    """Credit a USDC transfer from the user's wallet to the pool. Each transaction counts once."""
    if not user["wallet"]:
        raise CreditError("Add the wallet you'll pay from first, so we can match your payment.")
    pool, team = pool_address(), team_address()
    if not pool:
        raise CreditError("Credits aren't set up on this server yet (SOUL_POOL_ADDRESS is missing).")
    if not ADDRESS_RE.match(team):
        raise CreditError("Credits aren't set up on this server yet (SOUL_TEAM_ADDRESS is missing).")
    tx_hash = tx_hash.strip().lower()
    amount = verify_usdc_payment(network(), tx_hash, user["wallet"], pool)
    q = quote(amount)
    if q["credits"] < 1:
        raise CreditError("That payment is too small to buy any credits.")
    with db._lock:
        _table()
        if db.db().execute("SELECT 1 FROM payments WHERE tx_hash = ?", (tx_hash,)).fetchone():
            raise CreditError("That payment has already been used for a pass.")
        try:
            db.db().execute("INSERT INTO credit_purchases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (tx_hash, user["id"], amount, q["fee_bps"], q["fee"], team.lower(),
                             pool.lower(), q["credits"], db.now().isoformat()))
        except sqlite3.IntegrityError:
            raise CreditError("That payment has already been used.")
        _add(user["id"], q["credits"])
        db.db().commit()
    return {**info(user), "bought": q["credits"], "paid": amount / 10**USDC_DECIMALS,
            "fee": q["fee"] / 10**USDC_DECIMALS}


def _add(user_id: int, credits: int) -> None:
    db.db().execute("INSERT INTO credit_balances (user_id, balance) VALUES (?, ?) "
                    "ON CONFLICT (user_id) DO UPDATE SET balance = balance + excluded.balance",
                    (user_id, credits))


def spend(user, credits: int, reason: str) -> None:
    with db._lock:
        _table()
        moved = db.db().execute("UPDATE credit_balances SET balance = balance - ? "
                                "WHERE user_id = ? AND balance >= ?", (credits, user["id"], credits))
        if moved.rowcount != 1:
            raise CreditError(f"Not enough credits: this needs {credits}. Buy more to keep going.")
        db.db().execute("INSERT INTO credit_spends (user_id, credits, reason, created_at) VALUES (?, ?, ?, ?)",
                        (user["id"], credits, reason, db.now().isoformat()))
        db.db().commit()


def use(user, kind: str) -> None:
    """Count a chat or Scout run against the plan; once the plan's allowance is used up (or the
    trial has ended), pay for it with credits instead."""
    try:
        db.use(user, kind)
    except db.AccountError as plan_limit:
        try:
            spend(user, COSTS[kind], kind)
        except CreditError:
            raise db.AccountError(f"{plan_limit} Or buy credits to keep going.")


def team_fees() -> list[dict]:
    """What the team is owed from the pool, per team address it was booked to."""
    with db._lock:
        _table()
        rows = db.db().execute(
            "SELECT team_address, COUNT(*) AS purchases, SUM(amount) AS paid, SUM(fee) AS fees "
            "FROM credit_purchases GROUP BY team_address").fetchall()
    return [{"team_address": r["team_address"], "purchases": r["purchases"],
             "paid_usdc": r["paid"] / 10**USDC_DECIMALS, "fees_usdc": r["fees"] / 10**USDC_DECIMALS}
            for r in rows]
