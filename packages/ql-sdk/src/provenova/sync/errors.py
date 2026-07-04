"""Turn low-level HTTP/transport failures into clear, actionable messages.

The goal is that when interop breaks (network down, wrong endpoint, bad token,
server error, schema drift) the user is told *what* went wrong and *where to
look* — not handed a raw traceback or an opaque ``str(exception)``.
"""

from __future__ import annotations

import json

import httpx


class SyncError(Exception):
    """A user-facing sync/connectivity error.

    ``message`` is a plain-English statement of what went wrong; ``hint`` is the
    concrete next step; ``status`` is the HTTP status (None for transport errors).
    """

    def __init__(self, message: str, *, hint: str | None = None, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.status = status

    @property
    def transport(self) -> bool:
        """True if this was a connection-level failure (no HTTP response)."""
        return self.status is None

    def render(self) -> str:
        return self.message + (f"\n  → {self.hint}" if self.hint else "")


def explain_transport_error(exc: Exception, endpoint: str) -> SyncError:
    """Map an httpx transport exception to a friendly SyncError."""
    text = str(exc).lower()
    if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.TimeoutException)):
        return SyncError(
            f"Timed out talking to {endpoint}.",
            hint="The server is slow or unreachable. Check your network, VPN/proxy, and that the endpoint is correct (`ql config show`).",
        )
    if isinstance(exc, httpx.ConnectError):
        if "ssl" in text or "certificate" in text or "tls" in text:
            return SyncError(
                f"TLS/certificate error connecting to {endpoint}.",
                hint="The server's certificate couldn't be verified. Confirm the URL host and scheme (https), or that a corporate proxy isn't intercepting TLS.",
            )
        if "getaddrinfo" in text or "name or service not known" in text or "nodename nor servname" in text or "no address" in text:
            return SyncError(
                f"Could not resolve the host in {endpoint} (DNS failure).",
                hint="The hostname looks wrong or unreachable. Check the endpoint with `ql config show` / `ql config set sync_endpoint <url>`.",
            )
        return SyncError(
            f"Could not reach the Provenova server at {endpoint} (connection refused).",
            hint="Is the server running and the endpoint correct? Check `ql config show`, then `ql doctor`.",
        )
    # generic fallback for other httpx transport issues
    return SyncError(f"Network error talking to {endpoint}: {exc}",
                     hint="Run `ql doctor` to diagnose connectivity.")


def _detail(resp: httpx.Response) -> str | None:
    try:
        body = resp.json()
    except Exception:
        text = (resp.text or "").strip()
        return text[:200] or None
    detail = body.get("detail") if isinstance(body, dict) else body
    if isinstance(detail, dict):
        return detail.get("error") or json.dumps(detail)
    return str(detail) if detail is not None else None


def raise_for_status(resp: httpx.Response, endpoint: str) -> None:
    """Raise a friendly SyncError for a non-2xx response."""
    if resp.is_success:
        return
    code = resp.status_code
    detail = _detail(resp)
    said = f" Server said: {detail}" if detail else ""
    if code in (401, 403):
        raise SyncError(
            f"Not authorized ({code}).",
            status=code,
            hint="Your API token is missing, invalid, or revoked. Create one under Settings → API keys and run "
                 "`ql login --token <key>`." + said,
        )
    if code == 404:
        # Don't echo the (often HTML) body — it's just noise for a wrong URL.
        raise SyncError(
            f"{endpoint} returned 404 for the Provenova API.",
            status=code,
            hint="This may not be a Provenova server, or the endpoint is wrong. Check `ql config show`.",
        )
    if code == 422:
        raise SyncError(
            "The server rejected the run bundle (422).",
            status=code,
            hint=(detail or "The bundle may be malformed or from an incompatible SDK version. Upgrade "
                  "`provenova` and retry.") ,
        )
    if 500 <= code < 600:
        raise SyncError(
            f"The server at {endpoint} returned an error ({code}).",
            status=code,
            hint=("Try again shortly; if it persists, share this with the server operator." + said),
        )
    raise SyncError(f"Request to {endpoint} failed ({code}).", status=code, hint=(detail or None))
