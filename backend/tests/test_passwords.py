"""Unit tests for local password hashing and verification (app.core.passwords).

This is the trust anchor of email-and-password login, so the tests cover the round trip (the correct
password verifies), rejection of a wrong password, rejection of corrupt or malformed stored values,
and that a random salt makes the same password hash differently every time.

The implementation is pure stdlib (pbkdf2_hmac), so no database or fixture is needed.
"""
from __future__ import annotations

import pytest

from app.core.passwords import hash_password, verify_password


def test_hash_verify_roundtrip():
    """A hashed password verifies against the same plaintext."""
    stored = hash_password("S3cret-pass!")
    assert stored.startswith("pbkdf2_sha256$")
    assert verify_password("S3cret-pass!", stored) is True


def test_wrong_password_rejected():
    """A different plaintext does not verify."""
    stored = hash_password("correct-horse")
    assert verify_password("battery-staple", stored) is False
    assert verify_password("correct-horse ", stored) is False  # even a single trailing space is rejected


def test_salt_makes_hashes_unique():
    """The same password hashes to a different stored value each time, and both still verify."""
    a = hash_password("same-password")
    b = hash_password("same-password")
    assert a != b
    assert verify_password("same-password", a)
    assert verify_password("same-password", b)


@pytest.mark.parametrize(
    "bad",
    [
        None,                       # no password set yet (pre-bootstrap): nothing can authenticate
        "",                         # empty stored value
        "not-a-valid-hash",         # no separators
        "pbkdf2_sha256$200000$onlythree",   # too few fields
        "bcrypt$12$salt$hash",      # unsupported algorithm
        "pbkdf2_sha256$notint$c2FsdA==$aGFzaA==",  # iteration count is not an integer
    ],
)
def test_malformed_stored_never_authenticates(bad):
    """A corrupt, malformed, or unsupported stored value authenticates nothing, returning False
    rather than raising."""
    assert verify_password("anything", bad) is False


def test_iteration_count_is_strong():
    """The stored format must carry a high enough iteration count, which is what prevents a
    downgrade."""
    stored = hash_password("x")
    _, iters, _, _ = stored.split("$")
    assert int(iters) >= 100_000
