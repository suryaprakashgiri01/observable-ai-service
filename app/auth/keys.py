"""API key generation and hashing.

Keys are 256-bit random tokens, so a plain SHA-256 is enough: there is nothing
to brute-force that a slow hash (bcrypt/argon2) would protect, and a fast hash
keeps the per-request lookup to one indexed equality query.
"""

from __future__ import annotations

import hashlib
import secrets

KEY_PREFIX = "oas_"
DISPLAY_PREFIX_LEN = 10


def generate_api_key() -> str:
    return f"{KEY_PREFIX}{secrets.token_urlsafe(32)}"


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def display_prefix(key: str) -> str:
    return key[:DISPLAY_PREFIX_LEN]
