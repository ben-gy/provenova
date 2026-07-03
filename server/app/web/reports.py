"""Public DB-backed report pages + the site Atom feed.

Reports are auto-published by the growth pipeline (routine → Growth API), so
bodies are rendered with the UNTRUSTED markdown sanitizer, never the docs
renderer (which passes raw HTML through). Web routes are read-only — the
render cache (body_html) is written by the publish path.
"""

from __future__ import annotations

import datetime as _dt
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from quantumledger_core.models import Report, ResultCard

from ..config import get_settings
from ..db import get_db
from ..deps import Principal, current_principal
from ..services.sanitize import render_untrusted_markdown
from .routes import render

router = APIRouter(tags=["reports"])


@router.get("/reports", response_class=HTMLResponse)
def reports_index(request: Request, db: Session = Depends(get_db),
                  p: Principal | None = Depends(current_principal)):
    reports = db.scalars(
        select(Report).where(Report.published.is_(True))
        .order_by(Report.published_at.desc())
    ).all()
    return render(request, "reports.html", p, reports=reports, canonical_path="/reports")


@router.get("/reports/feed.xml")
def reports_feed(db: Session = Depends(get_db)):
    """Atom feed: published reports + public result cards, newest first."""
    base = get_settings().base_url
    entries: list[dict] = []
    for r in db.scalars(select(Report).where(Report.published.is_(True))
                        .order_by(Report.published_at.desc()).limit(50)):
        entries.append({
            "title": r.title, "url": f"{base}/reports/{r.slug}",
            "updated": r.published_at or r.created_at,
            "summary": r.meta_description,
        })
    for c in db.scalars(select(ResultCard).where(ResultCard.visibility == "public")
                        .order_by(ResultCard.created_at.desc()).limit(50)):
        entries.append({
            "title": c.title, "url": f"{base}/cards/{c.slug}",
            "updated": c.published_at or c.created_at,
            "summary": "Citable, offline-verifiable quantum result card.",
        })

    def _key(e):
        dt = e["updated"] or _dt.datetime.min
        return dt.replace(tzinfo=None) if dt.tzinfo else dt

    entries.sort(key=_key, reverse=True)
    entries = entries[:50]

    ns = "http://www.w3.org/2005/Atom"
    ET.register_namespace("", ns)
    feed = ET.Element(f"{{{ns}}}feed")
    ET.SubElement(feed, f"{{{ns}}}title").text = "QuantumLedger — reports & result cards"
    ET.SubElement(feed, f"{{{ns}}}id").text = f"{base}/reports/feed.xml"
    link = ET.SubElement(feed, f"{{{ns}}}link")
    link.set("href", f"{base}/reports/feed.xml")
    link.set("rel", "self")
    alt = ET.SubElement(feed, f"{{{ns}}}link")
    alt.set("href", f"{base}/reports")
    updated = entries[0]["updated"] if entries else _dt.datetime.now(_dt.timezone.utc)
    ET.SubElement(feed, f"{{{ns}}}updated").text = _iso(updated)
    author = ET.SubElement(feed, f"{{{ns}}}author")
    ET.SubElement(author, f"{{{ns}}}name").text = "QuantumLedger"

    for e in entries:
        entry = ET.SubElement(feed, f"{{{ns}}}entry")
        ET.SubElement(entry, f"{{{ns}}}title").text = e["title"]
        ET.SubElement(entry, f"{{{ns}}}id").text = e["url"]
        li = ET.SubElement(entry, f"{{{ns}}}link")
        li.set("href", e["url"])
        ET.SubElement(entry, f"{{{ns}}}updated").text = _iso(e["updated"])
        ET.SubElement(entry, f"{{{ns}}}summary").text = e["summary"]

    xml = ET.tostring(feed, encoding="unicode", xml_declaration=True)
    return Response(content=xml, media_type="application/atom+xml")


def _iso(dt: _dt.datetime | None) -> str:
    if dt is None:
        dt = _dt.datetime.now(_dt.timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.isoformat()


@router.get("/reports/{slug}", response_class=HTMLResponse)
def report_detail(slug: str, request: Request, db: Session = Depends(get_db),
                  p: Principal | None = Depends(current_principal)):
    r = db.scalar(select(Report).where(Report.slug == slug))
    if r is None or not r.published:
        raise HTTPException(404, "report not found")
    # Always render from body_md (the source of truth) so a sanitizer fix
    # remediates content published before the fix — never trust the cached
    # body_html for output.
    body_html = render_untrusted_markdown(r.body_md)
    return render(request, "report.html", p, report=r, body_html=body_html,
                  canonical_path=f"/reports/{slug}")
