"""Result Card lifecycle + citation export."""

from __future__ import annotations

import datetime as _dt
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from provenova_core import hashing
from provenova_core.models import (
    ReproductionEvent,
    Result,
    ResultCard,
    Run,
    VIS_PRIVATE,
    VIS_PUBLIC,
    new_ulid,
)


def _slugify(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48] or "result"
    return f"{base}-{new_ulid()[-8:].lower()}"


def build_summary(session: Session, run: Run) -> dict:
    res = run.results[0] if run.results else None
    repro = session.scalar(
        select(ReproductionEvent).where(
            ReproductionEvent.original_run_id == run.id, ReproductionEvent.status == "verified"
        )
    )
    hellinger = None
    if repro:
        scores = (repro.diff or {}).get("scores") or {}
        hf = scores.get("hellinger_fidelity", repro.reproducibility_score)
        hellinger = round(hf, 3) if hf is not None else None
    return {
        "backend": {"vendor": run.backend.vendor, "name": run.backend.name, "kind": run.backend.kind},
        "calibration_captured_at": run.calibration.captured_at.isoformat() if run.calibration.captured_at else None,
        "circuit": {"format": run.circuit.fmt, "n_qubits": run.circuit.n_qubits},
        "shots": run.shots,
        "distribution": res.distribution if res else {},
        "run_hash": run.run_hash,
        "reproducibility_score": repro.reproducibility_score if repro else None,
        "hellinger_fidelity": hellinger,
        "verdict": repro.verdict if repro else None,
        "reproductions": session.scalar(
            select(ReproductionEvent).where(ReproductionEvent.original_run_id == run.id)
        )
        is not None,
    }


def get_or_create_card(session: Session, run: Run, *, title: str | None = None) -> ResultCard:
    card = session.scalar(select(ResultCard).where(ResultCard.run_id == run.id))
    if card is not None:
        return card
    title = title or f"{run.backend.vendor}/{run.backend.name} — {run.circuit.n_qubits}q run"
    summary = build_summary(session, run)
    card = ResultCard(
        run_id=run.id,
        workspace_id=run.workspace_id,
        slug=_slugify(title),
        title=title,
        visibility=VIS_PRIVATE,
        summary=summary,
        card_sha256=hashing.sha256_hex(summary),
        pid=f"ql:card:{run.run_hash[:16]}",
    )
    session.add(card)
    session.flush()
    return card


def publish_card(session: Session, card: ResultCard, *, plan: str = "free",
                 provider=None, base_url: str = "") -> tuple[ResultCard, dict]:
    run = session.get(Run, card.run_id)
    card.summary = build_summary(session, run)
    card.card_sha256 = hashing.sha256_hex(card.summary)
    card.visibility = VIS_PUBLIC
    card.published_at = _dt.datetime.now(_dt.timezone.utc)
    mint_info = _maybe_mint_doi(session, card, plan=plan, provider=provider, base_url=base_url)
    session.flush()
    return card, mint_info


def _maybe_mint_doi(session: Session, card: ResultCard, *, plan: str,
                    provider, base_url: str) -> dict:
    """Mint an identifier on first publish. Never blocks publishing:
    over-quota and provider failures degrade to the free local PID."""
    from . import limits
    from .doi import DoiMintError, LocalPidProvider, local_pid

    if card.doi:
        return {"status": "exists", "doi": card.doi}
    provider = provider or LocalPidProvider()
    if not card.pid:
        card.pid = local_pid(card)
    if provider.scheme != "doi":
        return {"status": "pid_only", "pid": card.pid}
    usage = limits.doi_usage(session, plan, card.workspace_id)
    if usage["at_cap"]:
        return {"status": "quota_exceeded", "pid": card.pid,
                "used": usage["used"], "cap": usage["cap"]}
    try:
        result = provider.mint(card, base_url)
    except DoiMintError as e:
        return {"status": "mint_failed", "pid": card.pid, "error": str(e)}
    card.doi = result.identifier
    return {"status": "minted", "doi": card.doi, "provider": result.provider}


def unpublish_card(session: Session, card: ResultCard, *, provider=None) -> ResultCard:
    card.visibility = VIS_PRIVATE
    card.published_at = None
    # DOIs are permanent — keep card.doi; best-effort de-list the landing URL.
    if card.doi and provider is not None:
        provider.hide(card.doi)
    session.flush()
    return card


# -- citation export --------------------------------------------------------

def citation_fields(card: ResultCard, base_url: str) -> dict:
    """Shared citation metadata (BibTeX/CSL/RIS exports + DataCite payload)."""
    return {
        "identifier": card.doi or card.pid or f"ql:card:{card.slug}",
        "url": f"{base_url}/cards/{card.slug}",
        "year": (card.published_at or _dt.datetime.now(_dt.timezone.utc)).year,
        "title": card.title,
        "author": "Provenova contributor",
        "publisher": "Provenova",
    }


def citation(card: ResultCard, base_url: str, fmt: str = "bibtex") -> tuple[str, str]:
    cf = citation_fields(card, base_url)
    ident, url, year = cf["identifier"], cf["url"], cf["year"]
    key = f"ql_{card.slug.replace('-', '_')}"
    if fmt == "bibtex":
        body = (
            f"@misc{{{key},\n"
            f"  title = {{{card.title}}},\n"
            f"  author = {{Provenova contributor}},\n"
            f"  year = {{{year}}},\n"
            f"  howpublished = {{Provenova Result Card}},\n"
            f"  note = {{provenance-hash: {(card.summary or {}).get('run_hash','')}}},\n"
            f"  doi = {{{card.doi or ''}}},\n"
            f"  url = {{{url}}}\n}}\n"
        )
        return body, "application/x-bibtex"
    if fmt == "csl":
        import json

        obj = {
            "type": "dataset",
            "id": ident,
            "title": card.title,
            "DOI": card.doi,
            "URL": url,
            "publisher": "Provenova",
            "issued": {"date-parts": [[year]]},
            "author": [{"literal": "Provenova contributor"}],
        }
        return json.dumps(obj, indent=2), "application/vnd.citationstyles.csl+json"
    if fmt == "ris":
        body = (
            "TY  - DATA\n"
            f"TI  - {card.title}\n"
            f"PY  - {year}\n"
            f"PB  - Provenova\n"
            f"UR  - {url}\n"
            + (f"DO  - {card.doi}\n" if card.doi else "")
            + "ER  - \n"
        )
        return body, "application/x-research-info-systems"
    return "", "text/plain"


def embed_snippets(card: ResultCard, base_url: str, badge_type: str = "recorded") -> dict:
    import html as _html

    card_url = f"{base_url}/cards/{card.slug}"
    badge_url = f"{base_url}/badge/{card.slug}/{badge_type}.svg"
    embed_url = f"{base_url}/cards/{card.slug}/embed.html"
    title = _html.escape(card.title, quote=True)
    iframe = (
        f'<iframe src="{embed_url}" width="400" height="420" '
        f'style="border:0;overflow:hidden" loading="lazy" '
        f'title="{title} — Provenova"></iframe>'
    )
    return {
        "markdown": f"[![Provenova: {badge_type}]({badge_url})]({card_url})",
        "html": f'<a href="{card_url}"><img src="{badge_url}" alt="Provenova: {badge_type}"></a>',
        "rst": f".. image:: {badge_url}\n   :target: {card_url}",
        "iframe": iframe,
        "embed_url": embed_url,
    }


def embed_card_context(card: ResultCard, base_url: str) -> dict:
    """Render context for the standalone iframe-embeddable card."""
    return {
        "card": card,
        "base_url": base_url,
        "card_url": f"{base_url}/cards/{card.slug}",
        "badge_types": ["recorded", "reproduced", "compliant"],
    }
