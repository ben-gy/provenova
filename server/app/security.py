"""Password + API-key hashing and JWT helpers."""

from __future__ import annotations

import datetime as _dt
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from authlib.jose import jwt

from .config import get_settings

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return _ph.verify(hashed, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


# -- API keys ---------------------------------------------------------------

def generate_api_key() -> tuple[str, str, str]:
    """Return (full_key, prefix, hash). The full key is shown once."""
    body = secrets.token_urlsafe(24)
    full = f"ql_live_{body}"
    prefix = full[:16]
    return full, prefix, _ph.hash(full)


def verify_api_key(full_key: str, key_hash: str) -> bool:
    try:
        return _ph.verify(key_hash, full_key)
    except Exception:
        return False


# -- JWT (used for optional bearer access tokens) ---------------------------

def create_access_token(sub: str, org_id: str, plan: str, ttl_minutes: int = 60) -> str:
    settings = get_settings()
    header = {"alg": "HS256"}
    now = _dt.datetime.now(_dt.timezone.utc)
    payload = {
        "sub": sub,
        "org_id": org_id,
        "plan": plan,
        "iat": int(now.timestamp()),
        "exp": int((now + _dt.timedelta(minutes=ttl_minutes)).timestamp()),
    }
    return jwt.encode(header, payload, settings.secret_key).decode("utf-8")


def decode_access_token(token: str) -> dict | None:
    settings = get_settings()
    try:
        claims = jwt.decode(token, settings.secret_key)
        claims.validate()
        return dict(claims)
    except Exception:
        return None
