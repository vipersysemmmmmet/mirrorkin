"""Reads the blockchain to confirm USDC payments. Read-only: the server never holds keys."""

import json
import re
import urllib.request

from .config import CONFIRMATIONS

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
TX_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")


class PaymentError(Exception):
    pass


def rpc(url: str, method: str, params: list):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    # Public endpoints reject Python's default User-Agent.
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json", "User-Agent": "soul-simulator/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            reply = json.loads(r.read())
    except OSError as e:
        raise PaymentError(f"Couldn't reach the blockchain: {e}")
    if "error" in reply:
        raise PaymentError(f"Blockchain error: {reply['error'].get('message', reply['error'])}")
    return reply["result"]


def _topic_address(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def verify_usdc_payment(net: dict, tx_hash: str, sender: str, pay_to: str) -> int:
    """Return the USDC amount (in micro-USDC) `sender` paid `pay_to` in this transaction.

    Raises PaymentError if the transaction isn't a confirmed USDC transfer between them.
    """
    if not TX_RE.match(tx_hash):
        raise PaymentError("That doesn't look like a transaction hash (0x followed by 64 characters).")
    receipt = rpc(net["rpc"], "eth_getTransactionReceipt", [tx_hash])
    if receipt is None:
        raise PaymentError("Transaction not found yet. Wait a minute and try again.")
    if receipt.get("status") != "0x1":
        raise PaymentError("That transaction failed on the blockchain.")
    latest = int(rpc(net["rpc"], "eth_blockNumber", []), 16)
    if latest - int(receipt["blockNumber"], 16) < CONFIRMATIONS:
        raise PaymentError("Payment found but not confirmed yet. Try again in a few seconds.")

    total = 0
    for entry in receipt["logs"]:
        topics = entry.get("topics", [])
        if (entry["address"].lower() == net["usdc"].lower() and len(topics) == 3
                and topics[0] == TRANSFER_TOPIC
                and _topic_address(topics[1]) == sender.lower()
                and _topic_address(topics[2]) == pay_to.lower()):
            total += int(entry["data"], 16)
    if total == 0:
        raise PaymentError("No USDC transfer from your wallet to Soul Simulator in that transaction.")
    return total
