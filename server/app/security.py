"""Password + API-key hashing and JWT helpers."""

from __future__ import annotations

import datetime as _dt
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from authlib.jose import JsonWebToken

from .config import get_settings

_ph = PasswordHasher()

# Pin the accepted JWT algorithm to HS256. Constructing JsonWebToken with an
# explicit allowlist means a token whose header claims any other alg (e.g. a
# "none"/RS256 alg-confusion attempt) is rejected at decode time.
_jwt = JsonWebToken(["HS256"])


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
        "purpose": "access",  # bind context: not usable where another purpose is required
        "iat": int(now.timestamp()),
        "exp": int((now + _dt.timedelta(minutes=ttl_minutes)).timestamp()),
    }
    return _jwt.encode(header, payload, settings.secret_key).decode("utf-8")


def decode_access_token(token: str) -> dict | None:
    settings = get_settings()
    try:
        claims = _jwt.decode(token, settings.secret_key)
        claims.validate()
        # Context binding: reject tokens minted for another purpose (e.g. an
        # email-verification token) so they can't double as access credentials.
        # Legacy access tokens carry no purpose claim, hence the None allowance.
        if claims.get("purpose") not in (None, "access"):
            return None
        return dict(claims)
    except Exception:
        return None


# -- Email verification tokens ----------------------------------------------
# Signed (HS256) tokens proving control of an email inbox. Reuses the app
# secret; a distinct ``purpose`` claim keeps them from being usable as access
# tokens and vice-versa.

_EMAIL_VERIFY_PURPOSE = "email_verify"


def create_email_verification_token(account_id: str, email: str, ttl_hours: int = 24) -> str:
    settings = get_settings()
    now = _dt.datetime.now(_dt.timezone.utc)
    payload = {
        "sub": account_id,
        "email": email,
        "purpose": _EMAIL_VERIFY_PURPOSE,
        "iat": int(now.timestamp()),
        "exp": int((now + _dt.timedelta(hours=ttl_hours)).timestamp()),
    }
    return _jwt.encode({"alg": "HS256"}, payload, settings.secret_key).decode("utf-8")


def verify_email_verification_token(token: str) -> dict | None:
    """Return the claims for a valid, unexpired email-verification token, else None."""
    settings = get_settings()
    try:
        claims = _jwt.decode(token, settings.secret_key)
        claims.validate()  # exp/iat
        if claims.get("purpose") != _EMAIL_VERIFY_PURPOSE:
            return None
        return dict(claims)
    except Exception:
        return None
