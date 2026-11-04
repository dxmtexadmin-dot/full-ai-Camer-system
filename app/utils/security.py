"""Password hashing, session and CSRF primitives.

Passwords are hashed with PBKDF2-HMAC-SHA256 (stdlib only, no native build
requirements). Session data lives in a signed cookie handled by Starlette's
``SessionMiddleware``.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Optional

PBKDF2_ALGORITHM = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 260_000
SALT_BYTES = 16

SESSION_USER_KEY = "user_id"
SESSION_CSRF_KEY = "csrf_token"
SESSION_NOTICE_KEY = "notice"
SESSION_ROLE_KEY = "role"


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Parol bo‘sh bo‘lishi mumkin emas")
    salt = secrets.token_hex(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS)
    return f"{PBKDF2_ALGORITHM}${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not password or not stored:
        return False
    try:
        algorithm, iterations, salt, expected = stored.split("$", 3)
        if algorithm != PBKDF2_ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_valid(session_token: Optional[str], submitted: Optional[str]) -> bool:
    if not session_token or not submitted:
        return False
    return hmac.compare_digest(str(session_token), str(submitted))


def safe_next_url(candidate: Optional[str], default: str = "/") -> str:
    """Only allow same-site relative redirects (prevents open redirects)."""
    if not candidate:
        return default
    candidate = candidate.strip()
    if not candidate.startswith("/") or candidate.startswith("//"):
        return default
    if "\r" in candidate or "\n" in candidate:
        return default
    return candidate


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
