"""Server configuration from environment (pydantic-settings)."""

from __future__ import annotations

import logging
import os
import secrets as _secrets
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_log = logging.getLogger(__name__)

# Signing keys we refuse to run on in a real deployment: the empty default and
# the placeholders shipped in the example env files. Any of these means the
# operator never set QL_SECRET_KEY.
_WEAK_SECRET_KEYS = {"", "dev-insecure-change-me", "change-me-in-production"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QL_", extra="ignore")

    deployment: str = "hosted"  # hosted | selfhost
    database_url: str = "sqlite:///./quantumledger.db"
    base_url: str = "http://localhost:8000"
    # Signs session cookies AND JWT access tokens. MUST be set in production.
    # Empty by default so a missing QL_SECRET_KEY fails closed (see validator).
    secret_key: str = ""
    attestation_key_path: str = "./data/attestation_ed25519.key"
    attestation_key_b64: str = ""
    public_cards: bool = True
    admin_email: str = "admin@quantumledger.local"
    data_dir: str = "./data"

    # DOI minting. "local" (default) mints stable offline PIDs; "datacite"
    # registers real DOIs (permanent — prefix/repository must stay stable
    # across redeploys); "off" disables. enable_doi is the legacy toggle:
    # honored as "datacite" when doi_provider is unset.
    doi_provider: str = ""  # datacite | local | off ("" -> legacy enable_doi)
    enable_doi: bool = False
    datacite_endpoint: str = "https://api.datacite.org"  # test: https://api.test.datacite.org
    datacite_repository_id: str = ""
    datacite_password: str = ""
    datacite_prefix: str = ""  # e.g. "10.82521"

    # Zenodo DOI minting (FREE, opt-in). Set zenodo_token to enable the explicit
    # "Mint a DOI" action, which archives a run's provenance JSON on Zenodo and
    # mints a real, resolvable DOI at no cost. Never used by the auto-publish
    # path. Start against the sandbox (throwaway 10.5072 DOIs) before prod.
    zenodo_endpoint: str = "https://zenodo.org"  # sandbox: https://sandbox.zenodo.org
    zenodo_token: str = ""  # personal token, scopes: deposit:write deposit:actions

    # Growth engine (autonomous content pipeline)
    indexnow_key: str = ""  # serves /<key>.txt + enables IndexNow pings when set
    growth_max_cards_per_day: int = 3
    growth_max_reports_per_week: int = 2
    growth_refresh_min_hours: int = 6
    github_token: str = ""  # optional: raises GitHub API rate limit for corpus refresh

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @model_validator(mode="after")
    def _enforce_secret_key(self) -> "Settings":
        """Fail closed on a missing/placeholder signing key.

        In a real deployment an unset QL_SECRET_KEY would silently sign sessions
        and JWTs with a publicly-known constant (full auth forgery). For local
        selfhost/dev we instead mint an ephemeral per-process key so nobody has
        to configure anything, at the cost of sessions not surviving a restart.
        """
        if self.secret_key in _WEAK_SECRET_KEYS:
            if self.deployment == "selfhost":
                self.secret_key = _secrets.token_hex(32)
                _log.warning(
                    "QL_SECRET_KEY is unset; generated an ephemeral key for this "
                    "selfhost process. Sessions/JWTs will not survive a restart. "
                    "Set QL_SECRET_KEY to a persistent value (openssl rand -hex 32)."
                )
            else:
                raise RuntimeError(
                    f"QL_SECRET_KEY is unset or a known-insecure placeholder and "
                    f"deployment={self.deployment!r}. Refusing to start: it signs "
                    f"session cookies and JWTs. Set QL_SECRET_KEY to a strong "
                    f"random value, e.g. `openssl rand -hex 32`."
                )
        return self


@lru_cache
def get_settings() -> Settings:
    # DATABASE_URL (no prefix) is honored for convenience/compat with tooling.
    if "DATABASE_URL" in os.environ and "QL_DATABASE_URL" not in os.environ:
        os.environ["QL_DATABASE_URL"] = os.environ["DATABASE_URL"]
    return Settings()
