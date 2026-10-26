"""Tiny you creates her own receive-only wallets.

The private key goes straight into the Mac's Keychain and is never returned by any function
the brain can reach: tiny you only ever learns the public address. Nothing here can sign or
spend. The human can reveal a key (after the Mac asks for their password) to import the
wallet into an app like Phantom.
"""

import json
import re

import keyring
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import life

KEYCHAIN_SERVICE = "Soul Simulator tiny wallets"
WALLETS_FILE = life.DATA / "wallets.json"  # public addresses only
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(data) - len(data.lstrip(b"\0"))) + out


def wallets() -> dict:
    """{chain: public address} for every wallet tiny you has made."""
    return json.loads(WALLETS_FILE.read_text()) if WALLETS_FILE.exists() else {}


def create(chain: str) -> str:
    """Make a new wallet for `chain` (or return the existing one). Returns only the public address."""
    return create_labeled(chain, "")


CHAINS = {"solana": "solana", "base": "evm", "ethereum": "evm", "polygon": "evm", "arbitrum": "evm", "optimism": "evm"}


def _evm_keypair() -> tuple[str, str]:
    """(checksummed address, hex private key) for Base/Ethereum-style chains (MetaMask imports the key)."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from Crypto.Hash import keccak
    key = ec.generate_private_key(ec.SECP256K1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    public = key.public_key().public_bytes(serialization.Encoding.X962,
                                           serialization.PublicFormat.UncompressedPoint)[1:]
    raw = keccak.new(digest_bits=256, data=public).hexdigest()[-40:]
    return checksum(raw), "0x" + private.hex()


def checksum(address_hex: str) -> str:
    """EIP-55 mixed-case checksum, so wallets and explorers accept the address."""
    from Crypto.Hash import keccak
    a = address_hex.lower().removeprefix("0x")
    h = keccak.new(digest_bits=256, data=a.encode()).hexdigest()
    return "0x" + "".join(c.upper() if int(h[i], 16) >= 8 else c for i, c in enumerate(a))


def create_labeled(chain: str, label: str = "") -> str:
    """Make a wallet on `chain` (solana, base, ethereum...), optionally labelled ("trading", "bounties").
    Returns only the public address; the key goes straight to the Keychain. Same name returns the same wallet."""
    chain = chain.strip().lower()
    if chain not in CHAINS:
        raise ValueError(f"I can make wallets on: {', '.join(CHAINS)}.")
    label = re.sub(r"[^a-z0-9-]+", "-", label.strip().lower()).strip("-")[:30]
    name = f"{chain}:{label}" if label else chain
    existing = wallets()
    if name in existing:
        return existing[name]
    if CHAINS[chain] == "solana":
        key = Ed25519PrivateKey.generate()
        seed = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                 serialization.NoEncryption())
        public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        address = b58encode(public)
        secret = b58encode(seed + public)  # Phantom and Solflare import the 64-byte keypair in base58
    else:
        address, secret = _evm_keypair()
    keyring.set_password(KEYCHAIN_SERVICE, f"{chain}:{address}", secret)
    life.DATA.mkdir(exist_ok=True)
    WALLETS_FILE.write_text(json.dumps({**existing, name: address}, indent=2))
    life.log("created wallet", f"{name} {address}")
    return address


def reveal_for_human(name: str) -> str:
    """Only for the human's own 'show key' window, never for the brain."""
    address = wallets().get(name)
    if not address:
        raise ValueError(f"No wallet called {name}.")
    secret = keyring.get_password(KEYCHAIN_SERVICE, f"{name.split(':')[0]}:{address}")
    if not secret:
        raise ValueError("The key isn't in the Keychain.")
    return secret
