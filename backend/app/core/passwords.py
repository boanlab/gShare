"""Local password hashing and verification, on stdlib pbkdf2_hmac with no external dependency.

Stored format: ``pbkdf2_sha256$<iters>$<b64 salt>$<b64 hash>``. Used by email-and-password login.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import os

_ITER = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITER)
    return f"pbkdf2_sha256${_ITER}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


async def hash_password_async(password: str) -> str:
    """hash_password off the event loop.

    One PBKDF2 evaluation is ~100ms of pure CPU; called inline it stalls every other request on
    the uvicorn worker, so request handlers must use these wrappers.
    """
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password: str, stored: str | None) -> bool:
    """verify_password off the event loop (see hash_password_async)."""
    return await asyncio.to_thread(verify_password, password, stored)


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iters))
        return hmac.compare_digest(dk, expected)
    except Exception:  # noqa: BLE001 — malformed hash never authenticates
        return False
