"""Server configuration from environment (pydantic-settings)."""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QL_", extra="ignore")

    deployment: str = "hosted"  # hosted | selfhost
    database_url: str = "sqlite:///./quantumledger.db"
    base_url: str = "http://localhost:8000"
    secret_key: str = "dev-insecure-change-me"
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

    # Growth engine (autonomous content pipeline)
    indexnow_key: str = ""  # serves /<key>.txt + enables IndexNow pings when set
    growth_max_cards_per_day: int = 3
    growth_max_reports_per_week: int = 2
    growth_refresh_min_hours: int = 6
    github_token: str = ""  # optional: raises GitHub API rate limit for corpus refresh

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    # DATABASE_URL (no prefix) is honored for convenience/compat with tooling.
    if "DATABASE_URL" in os.environ and "QL_DATABASE_URL" not in os.environ:
        os.environ["QL_DATABASE_URL"] = os.environ["DATABASE_URL"]
    return Settings()
