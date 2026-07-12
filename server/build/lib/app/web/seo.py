"""SEO plumbing: robots.txt + dynamic sitemap.xml + security.txt.

The sitemap is assembled per-request from the DB (Fly disks are ephemeral and
the app runs on >1 machine, so nothing is ever written to disk) behind a small
in-process TTL cache. Sources: static pages, the docs manifest, public result
cards, hardware device pages, eligible comparison pairs and published reports.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from provenova_core.models import Report, ResultCard

from ..config import get_settings
from ..db import get_db
from . import docs as docs_mod

router = APIRouter(tags=["seo"])

_STATIC_PATHS = ["/", "/pricing", "/leaderboard", "/hardware", "/reports", "/docs"]
_CACHE_TTL_S = 600
_cache: dict[str, tuple[float, str]] = {}


@router.get("/robots.txt", include_in_schema=False)
def robots_txt() -> PlainTextResponse:
    base = get_settings().base_url
    body = (
        "User-agent: *\n"
        "Disallow: /app/\n"
        "Disallow: /login\n"
        "Disallow: /register\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )
    return PlainTextResponse(body)


@router.get("/.well-known/security.txt", include_in_schema=False)
@router.get("/security.txt", include_in_schema=False)  # convenience alias
def security_txt() -> PlainTextResponse:
    """RFC 9116 security.txt. Expires is computed ~1 year out per-request so the
    file never goes stale (a hardcoded date would silently expire)."""
    s = get_settings()
    base = s.base_url.rstrip("/")
    expires = (
        (datetime.now(timezone.utc) + timedelta(days=365))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    body = (
        f"Contact: mailto:{s.admin_email}\n"
        f"Expires: {expires}\n"
        "Preferred-Languages: en\n"
        f"Canonical: {base}/.well-known/security.txt\n"
    )
    return PlainTextResponse(body, media_type="text/plain; charset=utf-8")


def _iso_date(dt) -> str | None:
    if dt is None:
        return None
    try:
        return dt.date().isoformat()
    except AttributeError:
        return str(dt)[:10] or None


def _build_sitemap(db: Session) -> str:
    base = get_settings().base_url
    urls: list[tuple[str, str | None]] = [(f"{base}{p}", None) for p in _STATIC_PATHS]

    # Docs manifest pages.
    urls += [(f"{base}/docs/{slug}", None) for slug in docs_mod.all_slugs()]

    # Public result cards.
    for c in db.scalars(select(ResultCard).where(ResultCard.visibility == "public")):
        urls.append((f"{base}/cards/{c.slug}", _iso_date(c.published_at or c.created_at)))

    # Hardware device pages + eligible comparison pairs.
    try:
        from provenova_crawler.corpus import comparable_pairs, list_devices

        for d in list_devices(db):
            urls.append((
                f"{base}/hardware/{d['provider'].lower()}/{d['backend_id'].lower()}",
                (d["captured_at"] or "")[:10] or None,
            ))
        for pair in comparable_pairs(db):
            a, b = pair["a"], pair["b"]
            urls.append((
                f"{base}/hardware/{a['provider'].lower()}/{a['backend_id'].lower()}"
                f"/vs/{b['provider'].lower()}/{b['backend_id'].lower()}",
                None,
            ))
    except Exception:  # crawler package absent — sitemap still serves the rest
        pass

    # Published reports.
    for r in db.scalars(select(Report).where(Report.published.is_(True))):
        urls.append((f"{base}/reports/{r.slug}", _iso_date(r.published_at or r.created_at)))

    ns = "http://www.sitemaps.org/schemas/sitemap/0.9"
    ET.register_namespace("", ns)
    urlset = ET.Element(f"{{{ns}}}urlset")
    for loc, lastmod in urls:
        u = ET.SubElement(urlset, f"{{{ns}}}url")
        ET.SubElement(u, f"{{{ns}}}loc").text = loc
        if lastmod:
            ET.SubElement(u, f"{{{ns}}}lastmod").text = lastmod
    return ET.tostring(urlset, encoding="unicode", xml_declaration=True)


@router.get("/sitemap.xml", include_in_schema=False)
def sitemap_xml(db: Session = Depends(get_db)) -> Response:
    now = time.monotonic()
    hit = _cache.get("sitemap")
    if hit and now - hit[0] < _CACHE_TTL_S:
        xml = hit[1]
    else:
        xml = _build_sitemap(db)
        _cache["sitemap"] = (now, xml)
    return Response(content=xml, media_type="application/xml")
