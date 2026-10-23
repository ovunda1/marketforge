"""API keys (M10). A key is `mf_<prefix>_<secret>`: 8 hex characters to find the account
row, then 32 random bytes. The database stores the prefix and the SHA-256 of the whole
key, never the key. A fast hash is enough because the key has 256 bits of entropy:
there is nothing to brute-force, unlike a human-chosen password, which would need a
slow, salted hash (argon2, bcrypt). Comparison is constant time."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from typing import Any

_KEY = re.compile(r"^mf_([0-9a-f]{8})_[A-Za-z0-9_-]{43}$")


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    """(key, prefix, sha256 hex). The key is shown once and never stored."""
    prefix = secrets.token_hex(4)
    key = f"mf_{prefix}_{secrets.token_urlsafe(32)}"
    return key, prefix, hash_api_key(key)


def split_api_key(key: str) -> str | None:
    """The prefix of a well-formed key, else None."""
    m = _KEY.match(key)
    return m.group(1) if m else None


def verify_api_key(key: str, digest: str) -> bool:
    return hmac.compare_digest(hash_api_key(key), digest)


def new_account_row(name: str, role: str) -> tuple[dict[str, Any], str]:
    """The `accounts` row for a new account (without its id) and the key to hand out."""
    if role not in ("trader", "admin"):
        raise ValueError(f"role must be trader or admin, not {role!r}")
    key, prefix, digest = generate_api_key()
    return {"name": name, "role": role, "api_key_prefix": prefix, "api_key_hash": digest}, key
