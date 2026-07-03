"""DOI / persistent-identifier minting for published Result Cards.

Mirrors the crawler's offline-vs-live split: the default LocalPidProvider is
credential-free and never touches the network (self-host, demos, tests); the
DataCiteProvider registers real DOIs when credentials are configured. A
DataCite failure never blocks publishing — callers fall back to the local PID.

DataCite DOIs are PERMANENT: they cannot be deleted, only hidden. The prefix
and repository credentials must be stable across redeploys.
"""

from __future__ import annotations

import abc
import datetime as _dt
import logging
from typing import NamedTuple

log = logging.getLogger("quantumledger.doi")


class DoiMintError(RuntimeError):
    """Raised when a live provider fails to mint; callers fall back to PID."""


class MintResult(NamedTuple):
    identifier: str          # "10.1234/abc123" (doi) or "ql:card:<hash>" (pid)
    scheme: str              # "doi" | "pid"
    provider: str            # "datacite" | "local" | "off"
    url: str | None = None   # registered landing URL (DataCite only)
    raw: dict | None = None  # provider response, for the audit trail


def local_pid(card) -> str:
    """Stable, offline persistent identifier (same convention as cards.py)."""
    run_hash = (card.summary or {}).get("run_hash", "")
    return f"ql:card:{run_hash[:16]}" if run_hash else f"ql:card:{card.slug}"


class DoiProvider(abc.ABC):
    scheme: str = "pid"
    provider: str = "local"

    @abc.abstractmethod
    def mint(self, card, base_url: str) -> MintResult: ...

    def hide(self, identifier: str) -> None:
        """Best-effort de-listing on unpublish (DOIs are permanent)."""


class LocalPidProvider(DoiProvider):
    """Default: a deterministic local PID. Network-free, never raises."""

    scheme = "pid"
    provider = "local"

    def mint(self, card, base_url: str) -> MintResult:
        return MintResult(identifier=local_pid(card), scheme="pid", provider="local")


class OffProvider(DoiProvider):
    """Identifiers explicitly disabled; still returns the free local PID."""

    scheme = "pid"
    provider = "off"

    def mint(self, card, base_url: str) -> MintResult:
        return MintResult(identifier=local_pid(card), scheme="pid", provider="off")


class DataCiteProvider(DoiProvider):
    """Registers real DOIs via the DataCite REST API (JSON:API)."""

    scheme = "doi"
    provider = "datacite"

    def __init__(self, settings):
        self._endpoint = settings.datacite_endpoint.rstrip("/")
        self._auth = (settings.datacite_repository_id, settings.datacite_password)
        self._prefix = settings.datacite_prefix

    def _payload(self, card, base_url: str) -> dict:
        from . import cards as cards_svc

        cf = cards_svc.citation_fields(card, base_url)
        return {
            "data": {
                "type": "dois",
                "attributes": {
                    "prefix": self._prefix,
                    "event": "publish",
                    "titles": [{"title": cf["title"]}],
                    "creators": [{"name": cf["author"], "nameType": "Organizational"}],
                    "publisher": cf["publisher"],
                    "publicationYear": cf["year"],
                    "types": {"resourceTypeGeneral": "Dataset",
                              "resourceType": "Quantum Result Card"},
                    "url": cf["url"],
                    "rightsList": [{"rights": card.license or "CC-BY-4.0"}],
                    "descriptions": [{
                        "description": f"provenance-hash: {(card.summary or {}).get('run_hash', '')}",
                        "descriptionType": "Other",
                    }],
                },
            }
        }

    def mint(self, card, base_url: str) -> MintResult:
        import httpx  # network boundary: imported only when actually minting

        try:
            resp = httpx.post(f"{self._endpoint}/dois", json=self._payload(card, base_url),
                              auth=self._auth, timeout=15.0,
                              headers={"Content-Type": "application/vnd.api+json"})
            resp.raise_for_status()
            body = resp.json()
            doi = body["data"]["attributes"]["doi"]
        except Exception as e:  # noqa: BLE001 — any failure degrades to PID
            raise DoiMintError(f"datacite mint failed: {e}") from e
        return MintResult(identifier=doi, scheme="doi", provider="datacite",
                          url=f"{base_url}/cards/{card.slug}", raw=body)

    def hide(self, identifier: str) -> None:
        import httpx

        try:
            httpx.put(f"{self._endpoint}/dois/{identifier}",
                      json={"data": {"type": "dois", "attributes": {"event": "hide"}}},
                      auth=self._auth, timeout=15.0,
                      headers={"Content-Type": "application/vnd.api+json"})
        except Exception as e:  # noqa: BLE001 — best-effort only
            log.warning("datacite hide(%s) failed: %s", identifier, e)


def provider_for(settings) -> DoiProvider:
    """Resolve the configured provider; degrade to local when creds are absent."""
    p = (settings.doi_provider or ("datacite" if settings.enable_doi else "local")).lower()
    if p == "datacite":
        if settings.datacite_repository_id and settings.datacite_password and settings.datacite_prefix:
            return DataCiteProvider(settings)
        log.warning("QL_DOI_PROVIDER=datacite but credentials are incomplete; using local PIDs")
        return LocalPidProvider()
    if p == "off":
        return OffProvider()
    return LocalPidProvider()


def month_start(now: _dt.datetime | None = None) -> _dt.datetime:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
