"""Best-effort IndexNow pings for freshly-published public URLs.

IndexNow (indexnow.org) gives instant indexing on Bing/Yandex/Naver/Seznam —
Google ignores it (sitemap + Search Console cover Google). Pings are strictly
best-effort: called synchronously post-commit (BackgroundTasks are unsafe with
Fly auto-stop machines), errors swallowed, attempts audit-logged by callers.
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

from ..config import get_settings

ENDPOINT = "https://api.indexnow.org/indexnow"


def ping(urls: list[str]) -> bool:
    """Submit URLs to IndexNow. Returns True on a 2xx, False otherwise/no-op."""
    settings = get_settings()
    if not settings.indexnow_key or not urls:
        return False
    host = urlparse(settings.base_url).hostname or ""
    payload = {
        "host": host,
        "key": settings.indexnow_key,
        "keyLocation": f"{settings.base_url}/{settings.indexnow_key}.txt",
        "urlList": urls[:100],
    }
    try:
        r = httpx.post(ENDPOINT, json=payload, timeout=5.0)
        return 200 <= r.status_code < 300
    except Exception:
        return False
