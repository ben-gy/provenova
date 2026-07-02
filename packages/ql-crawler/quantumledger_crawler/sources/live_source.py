"""Live vendor-API source skeleton.

This is intentionally *not wired to the network*. Instantiating a LiveSource and
importing this module makes zero HTTP calls; the real request is isolated behind
:meth:`_http_get` and only runs when ``config['enabled']`` is truthy AND the
optional ``requests`` dependency is installed. Tests and the default fixture
path never trip it.

Each provider maps to a documented endpoint so the wiring is obvious to whoever
enables it later:

* IBM     - ``GET /backends/{id}/properties`` (IBM Quantum Runtime), returns a
  ``BackendProperties`` document — same shape as ``fixtures/ibm/*.json``.
* IonQ    - ``GET /characterizations/backends/{id}/current`` on ``api.ionq.co``.
* Braket  - ``braket:GetDevice`` (boto3), ``deviceCapabilities`` JSON.
"""

from __future__ import annotations

import datetime as _dt

from .base import CalibrationSource

_ENDPOINTS = {
    "ibm": "https://quantum.cloud.ibm.com/api/v1/backends/{backend_id}/properties",
    "ionq": "https://api.ionq.co/v0.3/characterizations/backends/{backend_id}/current",
    "braket": "braket:GetDevice?deviceArn={backend_id}",
}

_LICENSE_REFS = {
    "ibm": "ibm-quantum-tos-2024",
    "ionq": "ionq-tos-2024",
    "braket": "aws-braket-tos-2024",
}


class LiveSourceDisabled(RuntimeError):
    """Raised when a live fetch is attempted without explicit opt-in."""


class LiveSource(CalibrationSource):
    """Skeleton source that *would* poll a real vendor API.

    Parameters
    ----------
    provider:
        ``"ibm"``, ``"ionq"`` or ``"braket"``.
    config:
        Connection config. Live fetching only fires when ``config["enabled"]``
        is truthy. Expected keys (per provider): ``token``/``api_key`` and,
        optionally, ``base_url`` and ``backends`` (an explicit backend list).
    """

    mode = "live"

    def __init__(self, provider: str, config: dict | None = None):
        if provider not in _ENDPOINTS:
            raise ValueError(f"unknown provider {provider!r}")
        self.provider = provider
        self.config = dict(config or {})

    # -- guard -----------------------------------------------------------------

    def _require_enabled(self) -> None:
        if not self.config.get("enabled"):
            raise LiveSourceDisabled(
                f"LiveSource[{self.provider}] is disabled. Set config['enabled']=True "
                "and provide credentials to poll the real API. The offline "
                "FixtureSource is the default, credential-free path."
            )

    def list_backends(self) -> list[str]:
        # Callers may pre-declare backends in config to avoid a discovery call.
        backends = self.config.get("backends")
        if backends:
            return list(backends)
        self._require_enabled()
        # A real implementation would list backends from the provider here.
        raise LiveSourceDisabled(
            "backend discovery requires an enabled live connection; "
            "pass config['backends'] to enumerate offline."
        )

    def fetch_raw(self, backend_id: str) -> tuple[dict, dict]:
        """Fetch one live calibration reading (guarded; no call unless enabled)."""
        self._require_enabled()
        url = _ENDPOINTS[self.provider].format(backend_id=backend_id)
        raw = self._http_get(url)  # <-- the only network boundary
        meta = {
            "updated_at": self._extract_updated_at(raw),
            "source_url": url,
            "license_ref": _LICENSE_REFS[self.provider],
            "mode": self.mode,
            "fetched_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }
        return raw, meta

    # -- the stubbed network boundary -----------------------------------------

    def _http_get(self, url: str) -> dict:  # pragma: no cover - network stub
        """Perform the real HTTP GET. Deliberately stubbed.

        The import of ``requests`` is deferred to here so the module imports
        (and the whole test suite) never require the optional ``live`` extra.
        """
        try:
            import requests  # noqa: F401  (optional 'live' extra)
        except ImportError as exc:  # pragma: no cover
            raise LiveSourceDisabled(
                "install the 'live' extra (pip install quantumledger-crawler[live]) "
                "to enable live polling"
            ) from exc
        raise NotImplementedError(
            "LiveSource._http_get is a stub. Wire the authenticated vendor call "
            f"for provider={self.provider!r} here (endpoint: {url})."
        )

    def _extract_updated_at(self, raw: dict) -> str | None:  # pragma: no cover
        return (
            raw.get("last_update_date")
            or raw.get("date")
            or raw.get("executionWindowUpdatedAt")
        )
