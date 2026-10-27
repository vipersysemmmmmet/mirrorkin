"""Real-money memecoin swaps from her own spending wallets, inside limits her human sets.

Off unless the human turns it on, and in dry-run (quote and check everything, never send)
until they switch that off too. The brain never sees a key and has no tool for this: her
trading session proposes trades, and this code decides, signs and sends.

Rules enforced here, not in her instructions:
- Swaps only: her base coin (SOL/ETH, or USDC) <-> a token, coming back to her own wallet. There
  is no way to send funds to another address, so a scam page can't talk her into a drain.
- Every buy must pass the rug check (tiny/rugcheck.py) with risk <= MAX_REAL_RISK.
- Transactions only go to the known swap routers (Jupiter on Solana, KyberSwap on EVM chains).
- Buys above `auto_per_trade_usd` pop up for the human's OK; nothing goes above
  `max_per_trade_usd`; buys stop for the day at `daily_usd`. The wallet's balance is the total
  cap: fund it only with what she may use.

Set in profile.json, e.g.
  "spending": {"enabled": true, "dry_run": true, "auto_per_trade_usd": 5, "max_per_trade_usd": 25,
               "daily_usd": 50, "chains": ["solana", "base"]}
"""

import base64
import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime

import keyring

from . import life, wallets

LEDGER = life.DATA / "spending.jsonl"
DEFAULTS = {"enabled": False, "dry_run": True, "hold": "native", "auto_per_trade_usd": 5, "max_per_trade_usd": 25,
            "daily_usd": 50, "chains": ["solana", "base", "ethereum", "arbitrum", "optimism", "polygon"]}
SLIPPAGE_BPS = 300  # memecoins are thin; worse fills than this are refused

USDC = {
    "solana": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "base": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "ethereum": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "arbitrum": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
    "optimism": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
    "polygon": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
}
EVM = {"base": (8453, "https://mainnet.base.org"), "ethereum": (1, "https://ethereum-rpc.publicnode.com"),
       "arbitrum": (42161, "https://arb1.arbitrum.io/rpc"), "optimism": (10, "https://mainnet.optimism.io"),
       "polygon": (137, "https://polygon-rpc.com")}
# What she trades from: "native" (SOL on Solana, ETH/POL on EVM chains: also pays the network fees,
# and most memecoins are paired with it) or "usdc" (steady $1, but needs a little native coin for fees)
NATIVE = {"solana": "So11111111111111111111111111111111111111112",
          **{c: "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE" for c in EVM}}  # routers' "native coin" address
MAX_REAL_RISK = 0.4    # real money only buys coins that pass the rug check with low risk
GAS_RESERVE_USD = 1.5  # buys never spend the native coin needed to pay for the next transactions
SOLANA_RPC = "https://api.mainnet-beta.solana.com"
JUPITER = "https://lite-api.jup.ag/swap/v1"
KYBER = "https://aggregator-api.kyberswap.com"
KYBER_ROUTER = "0x6131b5fae19ea4f9d964eac0408e4408b66337b5"  # MetaAggregationRouterV2, same on every chain
SPENDING_LABEL = "spending"
_lock = threading.Lock()  # one trade at a time, so limits can't be raced


class SpendError(Exception):
    pass


def settings() -> dict:
    return {**DEFAULTS, **(life.profile().get("spending") or {})}


# ---- wallets and keys (keys never leave this module) ----

def wallet_address(chain: str) -> str:
    """Her spending wallet for a chain. One EVM key serves every EVM chain."""
    if chain == "solana":
        return wallets.create_labeled("solana", SPENDING_LABEL)
    return wallets.create_labeled("base", SPENDING_LABEL)


def _secret(chain: str) -> str:
    key_chain = "solana" if chain == "solana" else "base"
    address = wallet_address(chain)
    secret = keyring.get_password(wallets.KEYCHAIN_SERVICE, f"{key_chain}:{address}")
    if not secret:
        raise SpendError("The spending wallet's key isn't in the Keychain.")
    return secret


# ---- limits ----

def _ledger() -> list[dict]:
    if not LEDGER.exists():
        return []
    return [json.loads(line) for line in LEDGER.read_text().splitlines() if line.strip()]


def spent_today(dry_run: bool) -> float:
    today = date.today().isoformat()
    return sum(e["usd"] for e in _ledger()
               if e["side"] == "buy" and e["time"].startswith(today) and e["dry_run"] == dry_run and e.get("ok"))


def _record(entry: dict) -> None:
    life.DATA.mkdir(exist_ok=True)
    with LEDGER.open("a") as f:
        f.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), **entry}) + "\n")


def _ask_human(question: str) -> bool:
    """macOS pop-up; anything but a click on Approve (including no answer in 5 minutes) is no."""
    script = (f'display dialog {json.dumps(question)} with title "Mirrorkin wants to trade" '
              'buttons {"Decline", "Approve"} default button "Decline" with icon caution giving up after 300')
    try:
        out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=330)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "button returned:Approve" in out.stdout


def status() -> dict:
    """What her trading session is told about real-money trading."""
    s = settings()
    if not s["enabled"]:
        return {"enabled": False}
    return {"enabled": True, "dry_run": s["dry_run"], "chains": s["chains"],
            "trades_from": "SOL on Solana, ETH (POL on Polygon) elsewhere" if s["hold"] == "native" else "USDC",
            "auto_per_trade_usd": s["auto_per_trade_usd"], "max_per_trade_usd": s["max_per_trade_usd"],
            "daily_usd": s["daily_usd"], "spent_today_usd": round(spent_today(s["dry_run"]), 2),
            "wallets": {c: wallet_address(c) for c in s["chains"]}}


# ---- the one entry point ----

def execute(trade: dict) -> str:
    """Check a proposed swap against every rule, then quote, sign and send it (or just quote,
    in dry-run). `trade`: chain, token_address, side ('buy' with usd, or 'sell' with sell_pct)."""
    s = settings()
    chain = str(trade.get("chain", "")).lower()
    token = str(trade.get("token_address", "")).strip()
    side = trade.get("side")
    entry = {"chain": chain, "token": token, "symbol": trade.get("symbol", "?"), "side": side,
             "dry_run": s["dry_run"], "why": str(trade.get("why", ""))[:300]}
    with _lock:
        try:
            if not s["enabled"]:
                raise SpendError("real-money trading is off")
            if chain not in s["chains"] or chain not in USDC:
                raise SpendError(f"{chain or 'that chain'} isn't one of her trading chains")
            base, decimals = _base(chain, s)
            if not token or token.lower() in (USDC[chain].lower(), NATIVE[chain].lower()):
                raise SpendError("needs a memecoin's address, not USDC or the chain's own coin")
            price = _usd_price(chain, base)
            if side == "buy":
                usd = round(float(trade.get("usd", 0)), 2)
                entry["usd"] = usd
                if usd < 1:
                    raise SpendError("buy amount must be at least $1")
                if usd > s["max_per_trade_usd"]:
                    raise SpendError(f"${usd} is over the ${s['max_per_trade_usd']} per-trade limit")
                if spent_today(s["dry_run"]) + usd > s["daily_usd"]:
                    raise SpendError(f"would pass today's ${s['daily_usd']} limit")
                from . import rugcheck
                safe = rugcheck.safety(chain, token)
                if safe is None:
                    raise SpendError("couldn't run the rug check, so no buy")
                if not safe["ok"] or safe["risk"] > MAX_REAL_RISK:
                    raise SpendError(f"failed the rug check (risk {safe['risk']}): {'; '.join(safe['flags'][:3])}")
                entry["risk"] = safe["risk"]
                if usd > s["auto_per_trade_usd"] and not _ask_human(
                        f"Buy ${usd} of {entry['symbol']} on {chain}?\n\nWhy: {entry['why']}\n\n"
                        f"Over your ${s['auto_per_trade_usd']} auto limit, so it needs your OK."
                        + (" (Dry run: nothing will be sent.)" if s["dry_run"] else "")):
                    raise SpendError("you declined it")
                amount_in, token_in, token_out = int(usd / price * 10**decimals), base, token
                if not s["dry_run"] and base == NATIVE[chain] and (_native_balance(chain) - amount_in) * price / 10**decimals < GAS_RESERVE_USD:
                    raise SpendError(f"not enough left after keeping ${GAS_RESERVE_USD} for network fees")
            elif side == "sell":
                pct = min(100.0, max(1.0, float(trade.get("sell_pct", 100))))
                held = _token_balance(chain, token)
                amount_in, token_in, token_out = int(held * pct / 100), token, base
                if amount_in <= 0:
                    raise SpendError("she doesn't hold that token")
                entry["sell_pct"] = pct
            else:
                raise SpendError("side must be buy or sell")
            if chain == "solana":
                result = _swap_solana(token_in, token_out, amount_in, s["dry_run"])
            else:
                result = _swap_evm(chain, token_in, token_out, amount_in, s["dry_run"])
            entry["hold"] = s["hold"]
            if side == "sell":
                entry["usd"] = round(int(result["out"]) / 10**decimals * price, 2)
            entry.update(ok=True, **result)
            _record(entry)
            word = "Dry run, would have" if s["dry_run"] else "Done:"
            verb = "bought" if side == "buy" else "sold"
            return (f"{word} {verb} {entry['symbol']} for ${entry['usd']:.2f}"
                    + (f" (tx {result['tx']})" if result.get("tx") else ""))
        except SpendError as e:
            _record({**entry, "ok": False, "error": str(e)})
            return f"not done: {e}"
        except Exception as e:  # a failed quote or network error is a skipped trade, never a crash
            _record({**entry, "ok": False, "error": str(e)[:300]})
            return f"not done: {str(e)[:200]}"


# ---- network helpers ----

def _http(url: str, body: dict | None = None, headers: dict | None = None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "Accept": "application/json",
                                          "User-Agent": "mirrorkin", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:  # say why (e.g. "no route found"), not just "400"
        detail = e.read().decode(errors="replace")[:200]
        raise SpendError(f"{url.split('?')[0].rsplit('/', 2)[-2]} said: {detail or e.reason}")


def _rpc(url: str, method: str, params: list):
    reply = _http(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if "error" in reply:
        raise SpendError(f"chain error: {reply['error'].get('message', reply['error'])}")
    return reply["result"]


def _token_balance(chain: str, token: str) -> int:
    owner = wallet_address(chain)
    if chain == "solana":
        accounts = _rpc(SOLANA_RPC, "getTokenAccountsByOwner",
                        [owner, {"mint": token}, {"encoding": "jsonParsed"}])["value"]
        return sum(int(a["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"]) for a in accounts)
    data = "0x70a08231" + owner[2:].lower().rjust(64, "0")
    return int(_rpc(EVM[chain][1], "eth_call", [{"to": token, "data": data}, "latest"]), 16)


def _base(chain: str, s: dict) -> tuple[str, int]:
    """The coin she trades from on this chain, and its decimals."""
    if s["hold"] == "usdc":
        return USDC[chain], 6
    return NATIVE[chain], 9 if chain == "solana" else 18


def _usd_price(chain: str, base: str) -> float:
    """Dollars per whole coin, from the same router she trades on."""
    if base == USDC[chain]:
        return 1.0
    if chain == "solana":
        q = _http(f"{JUPITER}/quote?inputMint={base}&outputMint={USDC[chain]}&amount=1000000000&slippageBps=50")
        return int(q["outAmount"]) / 1e6
    r = _http(f"{KYBER}/{chain}/api/v1/routes?tokenIn={base}&tokenOut={USDC[chain]}&amountIn={10**18}",
              headers={"X-Client-Id": "mirrorkin"})["data"]["routeSummary"]
    return int(r["amountOut"]) / 1e6


def _native_balance(chain: str) -> int:
    owner = wallet_address(chain)
    if chain == "solana":
        return int(_rpc(SOLANA_RPC, "getBalance", [owner])["value"])
    return int(_rpc(EVM[chain][1], "eth_getBalance", [owner, "latest"]), 16)


# ---- Solana: Jupiter ----

def _swap_solana(token_in: str, token_out: str, amount: int, dry_run: bool) -> dict:
    from solders.keypair import Keypair
    from solders.transaction import VersionedTransaction
    owner = wallet_address("solana")
    quote = _http(f"{JUPITER}/quote?inputMint={token_in}&outputMint={token_out}&amount={amount}"
                  f"&slippageBps={SLIPPAGE_BPS}")
    if dry_run:
        return {"out": quote["outAmount"], "route": "jupiter"}
    built = _http(f"{JUPITER}/swap", {"quoteResponse": quote, "userPublicKey": owner,
                                      "dynamicComputeUnitLimit": True, "prioritizationFeeLamports": "auto"})
    tx = VersionedTransaction.from_bytes(base64.b64decode(built["swapTransaction"]))
    keypair = Keypair.from_base58_string(_secret("solana"))
    # Only her wallet signs and pays; anything else means the transaction isn't what was asked for
    signers = tx.message.header.num_required_signatures
    if str(tx.message.account_keys[0]) != owner or signers != 1:
        raise SpendError("the swap transaction didn't match her wallet; refused")
    signed = VersionedTransaction(tx.message, [keypair])
    sig = _rpc(SOLANA_RPC, "sendTransaction", [base64.b64encode(bytes(signed)).decode(),
                                               {"encoding": "base64", "maxRetries": 3}])
    for _ in range(30):
        st = _rpc(SOLANA_RPC, "getSignatureStatuses", [[sig]])["value"][0]
        if st and st.get("confirmationStatus") in ("confirmed", "finalized"):
            if st.get("err"):
                raise SpendError(f"swap failed on-chain ({sig})")
            return {"out": quote["outAmount"], "tx": sig, "route": "jupiter"}
        time.sleep(2)
    raise SpendError(f"swap sent but not confirmed yet ({sig}); check it before trading again")


# ---- EVM chains: KyberSwap ----

def _swap_evm(chain: str, token_in: str, token_out: str, amount: int, dry_run: bool) -> dict:
    owner = wallet_address(chain)
    head = {"X-Client-Id": "mirrorkin"}
    route = _http(f"{KYBER}/{chain}/api/v1/routes?tokenIn={token_in}&tokenOut={token_out}&amountIn={amount}",
                  headers=head)["data"]
    out = route["routeSummary"]["amountOut"]
    if dry_run:
        return {"out": out, "route": "kyberswap"}
    built = _http(f"{KYBER}/{chain}/api/v1/route/build",
                  {"routeSummary": route["routeSummary"], "sender": owner, "recipient": owner,
                   "slippageTolerance": SLIPPAGE_BPS}, headers=head)["data"]
    router = built["routerAddress"].lower()
    if router != KYBER_ROUTER:
        raise SpendError("the swap router wasn't the known KyberSwap router; refused")
    allowance = 0 if token_in == NATIVE[chain] else int(_rpc(EVM[chain][1], "eth_call", [{"to": token_in, "data": "0xdd62ed3e"
                    + owner[2:].lower().rjust(64, "0") + router[2:].rjust(64, "0")}, "latest"]), 16)
    native_in = token_in == NATIVE[chain]
    if not native_in and allowance < amount:  # approve exactly this swap's amount, never unlimited
        _send_evm(chain, token_in, "0x095ea7b3" + router[2:].rjust(64, "0") + hex(amount)[2:].rjust(64, "0"))
    tx = _send_evm(chain, router, built["data"], value=amount if native_in else 0)
    return {"out": out, "tx": tx, "route": "kyberswap"}


def _send_evm(chain: str, to: str, data: str, value: int = 0) -> str:
    from eth_account import Account
    chain_id, rpc = EVM[chain]
    owner = wallet_address(chain)
    nonce = int(_rpc(rpc, "eth_getTransactionCount", [owner, "pending"]), 16)
    gas = int(int(_rpc(rpc, "eth_estimateGas", [{"from": owner, "to": to, "data": data, "value": hex(value)}]), 16) * 1.3)
    base_fee = int(_rpc(rpc, "eth_getBlockByNumber", ["latest", False])["baseFeePerGas"], 16)
    tip = int(_rpc(rpc, "eth_maxPriorityFeePerGas", []), 16)
    signed = Account.sign_transaction({"chainId": chain_id, "nonce": nonce, "to": wallets.checksum(to),
                                       "value": value, "data": data, "gas": gas, "maxFeePerGas": base_fee * 2 + tip,
                                       "maxPriorityFeePerGas": tip, "type": 2}, _secret(chain))
    tx_hash = _rpc(rpc, "eth_sendRawTransaction", ["0x" + signed.raw_transaction.hex().removeprefix("0x")])
    for _ in range(60):
        receipt = _rpc(rpc, "eth_getTransactionReceipt", [tx_hash])
        if receipt:
            if receipt["status"] != "0x1":
                raise SpendError(f"transaction failed on-chain ({tx_hash})")
            return tx_hash
        time.sleep(2)
    raise SpendError(f"transaction sent but not confirmed yet ({tx_hash}); check it before trading again")
