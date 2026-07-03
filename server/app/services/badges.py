"""Hand-rolled shields.io-style SVG badges (no external dependency).

The badge state is computed from the record (the ladder), and each badge links
back to the live Result Card. The ETag is tied to the provenance hash so a
revocation / republish invalidates caches.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantumledger_core.models import (
    Attestation,
    BenchmarkEntry,
    ReproductionEvent,
    Result,
    ResultCard,
    Run,
    VIS_PUBLIC,
    WorkspaceFramework,
)

_COLORS = {
    "recorded": "#007ec6",
    "reproduced": "#4c1",
    "benchmarked": "#fe7d37",
    "compliant": "#8a2be2",
    "audit-ready": "#dfb317",
    "private": "#9f9f9f",
    "unknown": "#9f9f9f",
}
_LABEL = "provenova"
# Approximate Verdana glyph widths at 11px (shields.io uses a table; this is a
# simple constant-width approximation that renders cleanly enough).
_CHAR_W = 6.6
_PAD = 10.0


def _text_width(s: str) -> float:
    return len(s) * _CHAR_W + _PAD


def badge_state(session: Session, card: ResultCard, badge_type: str) -> tuple[str, str]:
    """Return (message, color) for a badge type given the record."""
    if card.visibility != VIS_PUBLIC:
        return "private", _COLORS["private"]
    run = session.get(Run, card.run_id)
    if run is None:
        return "unknown", _COLORS["unknown"]

    if badge_type == "recorded":
        return "recorded", _COLORS["recorded"]

    if badge_type == "reproduced":
        n = session.scalar(
            select(ReproductionEvent).where(
                ReproductionEvent.original_run_id == run.id,
                ReproductionEvent.status == "verified",
            )
        )
        return ("reproduced ✓", _COLORS["reproduced"]) if n else ("not reproduced", _COLORS["unknown"])

    if badge_type == "benchmarked":
        be = session.scalar(select(BenchmarkEntry).where(BenchmarkEntry.run_id == run.id))
        return ("benchmarked", _COLORS["benchmarked"]) if be else ("not benchmarked", _COLORS["unknown"])

    if badge_type == "compliant":
        wf = session.scalar(
            select(WorkspaceFramework).where(
                WorkspaceFramework.workspace_id == run.workspace_id,
                WorkspaceFramework.status == "pass",
            )
        )
        return ("compliant", _COLORS["compliant"]) if wf else ("not compliant", _COLORS["unknown"])

    if badge_type == "audit-ready":
        att = session.scalar(
            select(Attestation).where(
                Attestation.workspace_id == run.workspace_id, Attestation.revoked.is_(False)
            )
        )
        wf = session.scalar(
            select(WorkspaceFramework).where(
                WorkspaceFramework.workspace_id == run.workspace_id,
                WorkspaceFramework.status == "pass",
            )
        )
        return ("audit-ready", _COLORS["audit-ready"]) if (att and wf) else ("not audit-ready", _COLORS["unknown"])

    return "unknown", _COLORS["unknown"]


def render_svg(message: str, color: str, *, label: str = _LABEL, style: str = "flat") -> str:
    lw = _text_width(label)
    mw = _text_width(message)
    total = lw + mw
    lx = lw / 2 * 10
    mx = (lw + mw / 2) * 10
    rx = 3 if style != "flat-square" else 0
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total:.0f}" height="20" role="img" aria-label="{label}: {message}">
<title>{label}: {message}</title>
<linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>
<clipPath id="r"><rect width="{total:.0f}" height="20" rx="{rx}" fill="#fff"/></clipPath>
<g clip-path="url(#r)">
<rect width="{lw:.0f}" height="20" fill="#555"/>
<rect x="{lw:.0f}" width="{mw:.0f}" height="20" fill="{color}"/>
<rect width="{total:.0f}" height="20" fill="url(#s)"/>
</g>
<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="110" text-rendering="geometricPrecision">
<text x="{lx:.0f}" y="150" fill="#010101" fill-opacity=".3" transform="scale(.1)" textLength="{(lw-_PAD)*10:.0f}">{label}</text>
<text x="{lx:.0f}" y="140" transform="scale(.1)" textLength="{(lw-_PAD)*10:.0f}">{label}</text>
<text x="{mx:.0f}" y="150" fill="#010101" fill-opacity=".3" transform="scale(.1)" textLength="{(mw-_PAD)*10:.0f}">{message}</text>
<text x="{mx:.0f}" y="140" transform="scale(.1)" textLength="{(mw-_PAD)*10:.0f}">{message}</text>
</g></svg>"""


def endpoint_json(message: str, color_name: str) -> dict:
    return {"schemaVersion": 1, "label": _LABEL, "message": message, "color": color_name}
