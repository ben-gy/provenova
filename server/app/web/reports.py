"""Retired weekly reports + the site Atom feed.

The weekly "State of Quantum Hardware" report was replaced by /tracker in
October 2026: old report URLs redirect there permanently. Published reports
stay in the database untouched; they are just no longer served.

The Atom feed keeps its URL (subscribers have it) and now lists public
result cards.
"""

from __future__ import annotations

import datetime as _dt
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from provenova_core.models import ResultCard

from ..config import get_settings
from ..db import get_db

router = APIRouter(tags=["reports"])


@router.get("/reports", include_in_schema=False)
def reports_index():
    return RedirectResponse("/tracker", status_code=301)


@router.get("/reports/feed.xml")
def reports_feed(db: Session = Depends(get_db)):
    """Atom feed of public result cards, newest first."""
    base = get_settings().base_url
    entries = [{
        "title": c.title, "url": f"{base}/cards/{c.slug}",
        "updated": c.published_at or c.created_at,
        "summary": "Citable, offline-verifiable quantum result card.",
    } for c in db.scalars(select(ResultCard).where(ResultCard.visibility == "public")
                          .order_by(ResultCard.created_at.desc()).limit(50))]

    def _key(e):
        dt = e["updated"] or _dt.datetime.min
        return dt.replace(tzinfo=None) if dt.tzinfo else dt

    entries.sort(key=_key, reverse=True)

    ns = "http://www.w3.org/2005/Atom"
    ET.register_namespace("", ns)
    feed = ET.Element(f"{{{ns}}}feed")
    ET.SubElement(feed, f"{{{ns}}}title").text = "Provenova — result cards"
    ET.SubElement(feed, f"{{{ns}}}id").text = f"{base}/reports/feed.xml"
    link = ET.SubElement(feed, f"{{{ns}}}link")
    link.set("href", f"{base}/reports/feed.xml")
    link.set("rel", "self")
    alt = ET.SubElement(feed, f"{{{ns}}}link")
    alt.set("href", f"{base}/tracker")
    updated = entries[0]["updated"] if entries else _dt.datetime.now(_dt.timezone.utc)
    ET.SubElement(feed, f"{{{ns}}}updated").text = _iso(updated)
    author = ET.SubElement(feed, f"{{{ns}}}author")
    ET.SubElement(author, f"{{{ns}}}name").text = "Provenova"

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


@router.get("/reports/{slug}", include_in_schema=False)
def report_detail(slug: str):
    return RedirectResponse("/tracker", status_code=301)
