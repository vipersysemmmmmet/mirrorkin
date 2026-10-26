"""Tiny you's receive-only wallet: a public address on Base that she gets paid into.

Only the address is stored. The human holds the keys in their own wallet app, so tiny you
can never spend from it.
"""

import json
import re

from . import life

WALLET_FILE = life.DATA / "wallet.json"
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
BASE_RPC = "https://mainnet.base.org"
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
EXPLORER = "https://basescan.org/address/"


class WalletError(Exception):
    pass


def address() -> str:
    return json.loads(WALLET_FILE.read_text()).get("address", "") if WALLET_FILE.exists() else ""


def set_address(value: str) -> None:
    value = value.strip()
    if not ADDRESS_RE.match(value):
        raise WalletError("That isn't a wallet address (0x followed by 40 characters).")
    life.DATA.mkdir(exist_ok=True)
    WALLET_FILE.write_text(json.dumps({"address": value, "network": "base", "receive_only": True}, indent=2))
    life.log("linked wallet", value)


def balances() -> dict:
    """USDC and ETH held at the address on Base, read from the public blockchain."""
    from server.chain import PaymentError, rpc
    addr = address()
    if not addr:
        raise WalletError("No wallet linked yet.")
    try:
        data = "0x70a08231" + addr[2:].lower().rjust(64, "0")  # balanceOf(address)
        usdc = int(rpc(BASE_RPC, "eth_call", [{"to": USDC_BASE, "data": data}, "latest"]), 16) / 1e6
        eth = int(rpc(BASE_RPC, "eth_getBalance", [addr, "latest"]), 16) / 1e18
    except PaymentError as e:
        raise WalletError(str(e))
    return {"usdc": usdc, "eth": eth}
