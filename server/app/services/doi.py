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
from urllib.parse import urlparse


def _https_endpoint(url: str) -> str:
    """Require an https endpoint (localhost allowed for dev). Defense-in-depth so
    a config typo/injection can't redirect DOI traffic to a plaintext or internal
    host."""
    p = urlparse(url)
    if p.scheme != "https" and (p.hostname or "").lower() not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError(f"DOI endpoint must be https: {url!r}")
    return url.rstrip("/")

log = logging.getLogger("provenova.doi")


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
        self._endpoint = _https_endpoint(settings.datacite_endpoint)
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


class ZenodoProvider(DoiProvider):
    """Mints free, real DOIs via Zenodo (a CERN/OpenAIRE DataCite member).

    Unlike DataCite's single POST, a Zenodo deposit is a 4-step flow and
    REQUIRES an archived file — we upload the run's offline-verifiable
    ``qlprov/run/1.0`` provenance document. The DOI resolves to the Zenodo
    record (not Provenova); a ``related_identifiers`` back-link ties the two.
    This provider is reached ONLY by the explicit "Mint a DOI" action, never
    the auto-publish path (Zenodo is a research repository, not a bulk sink).
    """

    scheme = "doi"
    provider = "zenodo"

    def __init__(self, settings):
        self._endpoint = _https_endpoint(settings.zenodo_endpoint)
        self._token = settings.zenodo_token
        self._timeout = 30.0

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    def _file_bytes(self, card) -> tuple[str, bytes]:
        import json

        # The service layer stashes the pre-built provenance doc here; fall back
        # to the summary blob so unit tests can pass a bare card.
        data = card.__dict__.get("_provenance_json")
        if data is None:
            data = json.dumps(card.summary or {}, indent=2, sort_keys=True).encode()
        run_hash = (card.summary or {}).get("run_hash", "") or card.slug
        return f"provenova-run-{run_hash[:16]}.json", data

    def _metadata(self, card, base_url: str) -> dict:
        from . import cards as cards_svc

        cf = cards_svc.citation_fields(card, base_url)
        summary = card.summary or {}
        backend = summary.get("backend") or {}
        backend_label = f"{backend.get('vendor', '')}/{backend.get('name', '')}".strip("/")
        return {
            "metadata": {
                "upload_type": "dataset",
                "title": cf["title"],
                "creators": [{"name": cf["author"]}],
                "description": (
                    "Quantum Result Card provenance record published via Provenova.<br>"
                    f"Provenance hash (run_hash): {summary.get('run_hash', '')}<br>"
                    f"Backend: {backend_label or 'n/a'}<br>"
                    f"Verdict: {summary.get('verdict') or 'n/a'}<br>"
                    f"Card: {cf['url']}"
                ),
                "publication_date": (
                    card.published_at or _dt.datetime.now(_dt.timezone.utc)
                ).date().isoformat(),
                "access_right": "open",
                "license": (card.license or "cc-by-4.0").lower(),
                "keywords": ["quantum computing", "provenance", "reproducibility",
                             "Provenova"] + ([backend.get("vendor")] if backend.get("vendor") else []),
                "related_identifiers": [
                    {"relation": "isIdenticalTo", "identifier": cf["url"], "scheme": "url"},
                ],
                "prereserve_doi": True,
            }
        }

    def _delete_draft(self, client, dep_id) -> None:
        if dep_id is None:
            return
        try:
            client.delete(f"{self._endpoint}/api/deposit/depositions/{dep_id}")
        except Exception as e:  # noqa: BLE001 — best-effort cleanup only
            log.warning("zenodo: failed to delete draft %s: %s", dep_id, e)

    def mint(self, card, base_url: str) -> MintResult:
        import httpx

        dep_id = None
        try:
            with httpx.Client(timeout=self._timeout, headers=self._headers()) as c:
                # 1. create a draft deposition
                r1 = c.post(f"{self._endpoint}/api/deposit/depositions", json={})
                r1.raise_for_status()
                dep = r1.json()
                dep_id = dep["id"]
                bucket = dep["links"]["bucket"]
                # 2. upload the provenance JSON (a file is required to publish)
                fname, data = self._file_bytes(card)
                c.put(f"{bucket}/{fname}", content=data).raise_for_status()
                # 3. attach metadata
                c.put(f"{self._endpoint}/api/deposit/depositions/{dep_id}",
                      json=self._metadata(card, base_url)).raise_for_status()
                # 4. publish -> mints the DOI (HTTP 202)
                r4 = c.post(f"{self._endpoint}/api/deposit/depositions/{dep_id}/actions/publish")
                r4.raise_for_status()
                pub = r4.json()
                # roll back the draft only on failure — reset so the except below
                # doesn't delete a successfully published record
                dep_id = None
        except Exception as e:  # noqa: BLE001 — any step failing degrades cleanly
            if dep_id is not None:
                with httpx.Client(timeout=self._timeout, headers=self._headers()) as c:
                    self._delete_draft(c, dep_id)
            raise DoiMintError(f"zenodo mint failed: {e}") from e
        meta = pub.get("metadata") or {}
        doi = pub.get("doi") or (meta.get("prereserve_doi") or {}).get("doi")
        links = pub.get("links") or {}
        record_url = links.get("record_html") or links.get("html") or pub.get("doi_url")
        if not doi:
            raise DoiMintError("zenodo published but returned no DOI")
        return MintResult(identifier=doi, scheme="doi", provider="zenodo",
                          url=record_url, raw=pub)

    def hide(self, identifier: str) -> None:
        # Published Zenodo records/DOIs are permanent and can't be trivially
        # withdrawn via the API — same permanence contract as DataCite.
        log.info("zenodo: hide(%s) is a no-op (published DOIs are permanent)", identifier)


def provider_for(settings) -> DoiProvider:
    """Resolve the AUTO-publish provider; degrade to local when creds are absent.

    Zenodo is deliberately NOT reachable here — it is opt-in only, via
    ``zenodo_provider()`` and the explicit mint action.
    """
    p = (settings.doi_provider or ("datacite" if settings.enable_doi else "local")).lower()
    if p == "datacite":
        if settings.datacite_repository_id and settings.datacite_password and settings.datacite_prefix:
            return DataCiteProvider(settings)
        log.warning("QL_DOI_PROVIDER=datacite but credentials are incomplete; using local PIDs")
        return LocalPidProvider()
    if p == "off":
        return OffProvider()
    return LocalPidProvider()


def zenodo_provider(settings) -> ZenodoProvider | None:
    """The opt-in Zenodo provider — only when a token is configured."""
    if getattr(settings, "zenodo_token", ""):
        return ZenodoProvider(settings)
    return None


def month_start(now: _dt.datetime | None = None) -> _dt.datetime:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
