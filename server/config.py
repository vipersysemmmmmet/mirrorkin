"""Server settings, from .env."""

import os

NETWORKS = {
    # Test network: free test USDC from Circle's faucet, no real money.
    "base-sepolia": {
        "label": "Base Sepolia (test network)",
        "rpc": "https://sepolia.base.org",
        "usdc": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "explorer": "https://sepolia.basescan.org/tx/",
    },
    "base": {
        "label": "Base",
        "rpc": "https://mainnet.base.org",
        "usdc": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "explorer": "https://basescan.org/tx/",
    },
}

USDC_DECIMALS = 6
CONFIRMATIONS = 3
PASS_DAYS = 30
TRIAL_DAYS = 2

# Prices in USDC per 30-day pass. Limits apply per user.
PLANS = {
    "free": {"name": "Starter", "price": 0, "chats_per_day": 10, "scouts_per_week": 1},
    "plus": {"name": "Plus", "price": 30, "chats_per_day": 20, "scouts_per_week": 7},
    "pro": {"name": "Pro", "price": 100, "chats_per_day": 60, "scouts_per_week": 30},
    "pro_max": {"name": "Pro Max", "price": 200, "chats_per_day": 120, "scouts_per_week": 60},
}


def network() -> dict:
    name = os.environ.get("SOUL_NETWORK", "base-sepolia")
    if name not in NETWORKS:
        raise SystemExit(f"Unknown SOUL_NETWORK '{name}'. Options: {', '.join(NETWORKS)}")
    net = {"id": name, **NETWORKS[name]}
    # Public endpoints are rate limited; set SOUL_RPC to a provider URL for production.
    net["rpc"] = os.environ.get("SOUL_RPC", net["rpc"])
    return net


def pay_to() -> str:
    """The wallet that receives subscription payments. Only its public address is needed."""
    return os.environ.get("SOUL_PAY_TO", "").strip()


def port() -> int:
    return int(os.environ.get("SOUL_PORT", "8100"))
