"""HTTP client to the hosted ingestion API.

All network calls funnel through helpers that translate httpx transport/HTTP
errors into :class:`SyncError` with actionable messages (see ``errors.py``).
"""

from __future__ import annotations

import httpx

from .errors import SyncError, explain_transport_error, raise_for_status


class SyncClient:
    def __init__(self, endpoint: str, token: str | None = None, timeout: float = 30.0):
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self._client = httpx.Client(timeout=timeout)

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _get(self, path: str, *, auth: bool = False) -> httpx.Response:
        try:
            return self._client.get(f"{self.endpoint}{path}",
                                    headers=self._headers() if auth else None)
        except httpx.HTTPError as exc:
            raise explain_transport_error(exc, self.endpoint) from exc

    def health(self) -> dict:
        """Reachability + identity check against the public health endpoint."""
        r = self._get("/api/v1/health")
        raise_for_status(r, self.endpoint)
        try:
            data = r.json()
        except Exception as exc:
            raise SyncError(
                f"{self.endpoint} responded, but not with Provenova JSON.",
                hint="The endpoint is probably not a Provenova server. Check `ql config show`.",
            ) from exc
        # Accept both handshakes so a new SDK syncs with a not-yet-redeployed
        # server (and vice versa) across the quantumledger -> provenova rename.
        if data.get("service") not in ("provenova", "quantumledger"):
            raise SyncError(
                f"{self.endpoint} does not look like a Provenova server.",
                hint="Point `sync_endpoint` at your Provenova server (`ql config set sync_endpoint <url>`).",
            )
        return data

    def whoami(self) -> dict:
        """Verify the stored token by calling the authenticated principal endpoint.

        ``/api/v1/me`` uses optional auth and answers ``{"authenticated": false}``
        (HTTP 200) for a missing/invalid token, so we check that flag explicitly
        rather than relying on the status code.
        """
        r = self._get("/api/v1/me", auth=True)
        raise_for_status(r, self.endpoint)
        try:
            data = r.json()
        except Exception as exc:
            raise SyncError(f"{self.endpoint} returned an unexpected response.",
                            hint="Run `ql doctor` to check the endpoint.") from exc
        if not data.get("authenticated"):
            raise SyncError(
                "Your API token was not accepted.",
                status=401,
                hint="The token is missing, invalid, or revoked. Create one under Settings → API keys "
                     "and run `ql login --token <key>`.",
            )
        return data

    def ingest(self, bundle: dict) -> dict:
        run_hash = bundle["provenance"]["run_hash"]
        try:
            r = self._client.post(
                f"{self.endpoint}/api/v1/ingest/runs",
                json=bundle,
                headers={**self._headers(), "Idempotency-Key": run_hash},
            )
        except httpx.HTTPError as exc:
            raise explain_transport_error(exc, self.endpoint) from exc
        raise_for_status(r, self.endpoint)
        try:
            return r.json()
        except Exception as exc:
            raise SyncError(
                f"{self.endpoint} accepted the request but returned a non-JSON response.",
                hint="The endpoint may not be a Provenova ingest API. Run `ql doctor`.",
            ) from exc

    def close(self) -> None:
        self._client.close()
