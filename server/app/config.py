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
    enable_doi: bool = False
    data_dir: str = "./data"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    # DATABASE_URL (no prefix) is honored for convenience/compat with tooling.
    if "DATABASE_URL" in os.environ and "QL_DATABASE_URL" not in os.environ:
        os.environ["QL_DATABASE_URL"] = os.environ["DATABASE_URL"]
    return Settings()
