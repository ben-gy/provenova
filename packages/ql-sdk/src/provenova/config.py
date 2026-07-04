"""SDK configuration: store path, sync endpoint, token."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


def default_home() -> Path:
    if "QL_HOME" in os.environ:
        return Path(os.environ["QL_HOME"])
    new = Path.home() / ".provenova"
    legacy = Path.home() / ".quantumledger"
    # Honor a pre-rename home dir so existing local ledgers keep working.
    if legacy.exists() and not new.exists():
        return legacy
    return new


@dataclass
class QLConfig:
    store_path: str = ""
    sync_endpoint: str = "http://localhost:8000"
    token: str | None = None
    default_project: str | None = None

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.store_path}"


def config_path(home: Path | None = None) -> Path:
    return (home or default_home()) / "config.json"


def load_config(home: Path | None = None) -> QLConfig:
    home = home or default_home()
    cfg = QLConfig(store_path=str(home / "ledger.db"))
    p = config_path(home)
    if p.exists():
        data = json.loads(p.read_text())
        for k, v in data.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
    if not cfg.store_path:
        cfg.store_path = str(home / "ledger.db")
    return cfg


def save_config(cfg: QLConfig, home: Path | None = None) -> None:
    home = home or default_home()
    home.mkdir(parents=True, exist_ok=True)
    config_path(home).write_text(json.dumps(asdict(cfg), indent=2))
