"""Lightweight in-process rate limiting.

A fixed-window counter keyed by ``(bucket, client-ip)``. State lives in memory,
so limits are per-process and reset on restart / scale-to-zero. That is
deliberately modest: the goal is to raise the cost of credential brute-force,
registration spam and compute amplification — not to be a globally-consistent
quota. For cross-instance limits, front this with a shared store (not needed at
current scale).

Client IP is taken from ``request.client.host``, which is only the real caller
when uvicorn runs with ``--proxy-headers --forwarded-allow-ips`` behind the
trusted Fly/Caddy edge (see deploy configs). Without that it is the proxy IP and
all callers share a bucket — fail-safe (over-throttles) rather than fail-open.
"""

from __future__ import annotations

import threading
import time

from fastapi import HTTPException, Request

from .config import get_settings

_LOCK = threading.Lock()
# (bucket, ip) -> (window_start_monotonic, count)
_HITS: dict[tuple[str, str], tuple[float, int]] = {}
_LAST_PRUNE = [0.0]


def _enabled() -> bool:
    return get_settings().ratelimit_enabled


def _client_ip(request: Request) -> str:
    return (request.client.host if request.client else None) or "unknown"


def _prune(now: float) -> None:
    # Opportunistic cleanup (at most once a minute) so the map can't grow without
    # bound under IP churn. Anything older than an hour is certainly expired.
    if now - _LAST_PRUNE[0] < 60:
        return
    _LAST_PRUNE[0] = now
    for k in [k for k, (start, _) in _HITS.items() if now - start > 3600]:
        _HITS.pop(k, None)


def check(bucket: str, ip: str, *, limit: int, window_s: float) -> None:
    """Record one hit for (bucket, ip); raise HTTP 429 if over ``limit`` in the window."""
    if not _enabled():
        return
    now = time.monotonic()
    with _LOCK:
        _prune(now)
        start, count = _HITS.get((bucket, ip), (now, 0))
        if now - start >= window_s:
            start, count = now, 0
        count += 1
        _HITS[(bucket, ip)] = (start, count)
        if count > limit:
            retry = max(1, int(window_s - (now - start)) + 1)
            raise HTTPException(status_code=429, detail="rate limit exceeded — slow down",
                                headers={"Retry-After": str(retry)})


def rate_limit(bucket: str, *, limit: int, window_s: float):
    """FastAPI dependency factory: throttle a route by client IP within a bucket."""

    def _dep(request: Request) -> None:
        check(bucket, _client_ip(request), limit=limit, window_s=window_s)

    return _dep


def reset() -> None:  # test helper
    with _LOCK:
        _HITS.clear()
        _LAST_PRUNE[0] = 0.0
