#!/usr/bin/env python3
"""Provenova growth agent — a self-contained, headless runner for the content pipeline.

Packages the scheduled routine as a portable CLI so it can run anywhere (a laptop,
a cron box, or GitHub Actions) without Claude Code. It uses the Anthropic SDK for
the editorial judgement (paper triage, circuit design, report writing) and httpx for
everything else, and calls the live Provenova Growth API to publish.

Honesty by construction: paper metadata (arXiv id, DOI, authors, URL) is fetched
DETERMINISTICALLY and merged back by index — the model only decides *which* papers
warrant a card and *what* faithful textbook circuit to run, so it can never fabricate
a citation. Every card records a real deterministic simulator run on Provenova; the
server re-validates and re-enforces all caps regardless of what this script sends.

Subcommands:
    research-cards   Scan new arXiv/OpenAlex papers, publish faithful reproduction cards.
    weekly-report    Publish the weekly "State of Quantum Hardware" report.
    status           Print the Growth API status (debugging).

Environment:
    PROVENOVA_GROWTH_KEY   (required)  ql_live_… key with the `growth` scope.
    ANTHROPIC_API_KEY      (required for research-cards/weekly-report) Anthropic key.
    PROVENOVA_BASE_URL     (default https://provenova.net)
    PROVENOVA_AGENT_MODEL  (default claude-opus-4-8)
    PROVENOVA_MAX_CARDS    (default 2)  soft cap per run (server hard-caps at 3/day).
    OPENALEX_MAILTO        (default hi@ben.gy)  polite-pool contact for OpenAlex/arXiv UA.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import math
import os
import re
import sys
import time
import xml.etree.ElementTree as ET

import httpx
from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

BASE_URL = os.environ.get("PROVENOVA_BASE_URL", "https://provenova.net").rstrip("/")
GROWTH_KEY = os.environ.get("PROVENOVA_GROWTH_KEY", "")
MODEL = os.environ.get("PROVENOVA_AGENT_MODEL", "claude-opus-4-8")
MAX_CARDS = int(os.environ.get("PROVENOVA_MAX_CARDS", "2"))
MAILTO = os.environ.get("OPENALEX_MAILTO", "hi@ben.gy")
UA = f"provenova-growth/1.0 (mailto:{MAILTO})"

# Mirror of the server's qlir gate allowlist (services/growth.py). The server is
# authoritative and re-validates every item; this is a fast local pre-check.
ALLOWED_GATES: dict[str, tuple[int, int]] = {
    "h": (0, 1), "x": (0, 1), "y": (0, 1), "z": (0, 1),
    "s": (0, 1), "sdg": (0, 1), "t": (0, 1), "tdg": (0, 1), "sx": (0, 1), "id": (0, 1),
    "rz": (1, 1), "rx": (1, 1), "ry": (1, 1),
    "cx": (0, 2), "cz": (0, 2), "swap": (0, 2), "ccx": (0, 3),
}
MAX_QUBITS, MAX_GATES = 10, 256
_ALLOWED_HOSTS = {"arxiv.org", "www.arxiv.org", "doi.org", "dx.doi.org"}


def _die(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


# --------------------------------------------------------------------------- #
# Growth API client
# --------------------------------------------------------------------------- #

def _auth() -> dict:
    if not GROWTH_KEY:
        _die("PROVENOVA_GROWTH_KEY is not set")
    return {"Authorization": f"Bearer {GROWTH_KEY}", "User-Agent": UA}


def growth_get(path: str) -> dict:
    r = httpx.get(f"{BASE_URL}{path}", headers=_auth(), timeout=30)
    r.raise_for_status()
    return r.json()


def growth_post(path: str, json_body: dict, timeout: float = 90) -> tuple[int, dict]:
    r = httpx.post(f"{BASE_URL}{path}", headers=_auth(), json=json_body, timeout=timeout)
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text}
    return r.status_code, body


# --------------------------------------------------------------------------- #
# Paper sourcing (deterministic — no model in the loop)
# --------------------------------------------------------------------------- #

def _abstract_from_inverted(inv: dict | None) -> str:
    if not inv:
        return ""
    pos = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in range(max(pos) + 1) if i in pos) if pos else ""


def fetch_arxiv(max_results: int = 40) -> list[dict]:
    """Newest quant-ph submissions from the arXiv API (Atom). Empty list on failure."""
    # HTTPS so a network MITM can't inject attacker-controlled abstract text that
    # would then flow into the triage model as a prompt-injection channel.
    url = ("https://export.arxiv.org/api/query?search_query=cat:quant-ph"
           f"&sortBy=submittedDate&sortOrder=descending&max_results={max_results}")
    for attempt in range(2):
        try:
            r = httpx.get(url, headers={"User-Agent": UA}, timeout=40)
            if r.status_code == 200 and "<entry>" in r.text:
                break
        except Exception:
            pass
        time.sleep(20)  # arXiv rate limit: back off and retry once
    else:
        return []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(r.text)
    out = []
    for e in root.findall("a:entry", ns):
        aid = (e.findtext("a:id", "", ns) or "").rsplit("/", 1)[-1]
        m = re.match(r"^(\d{4}\.\d{4,5})", aid)
        if not m:
            continue
        base_id = m.group(1)
        out.append({
            "title": " ".join((e.findtext("a:title", "", ns) or "").split()),
            "abstract": " ".join((e.findtext("a:summary", "", ns) or "").split()),
            "authors": [a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)][:12],
            "year": int((e.findtext("a:published", "", ns) or "0000")[:4] or 0) or None,
            "arxiv_id": base_id,
            "doi": None,
            "url": f"https://arxiv.org/abs/{base_id}",
        })
    return out


def fetch_openalex(max_results: int = 40) -> list[dict]:
    """Fallback source: recent quantum-computing works from OpenAlex (CC0)."""
    since = (_today() - _dt.timedelta(days=21)).isoformat()
    url = ("https://api.openalex.org/works?filter="
           f"title_and_abstract.search:quantum circuit,from_publication_date:{since}"
           f"&sort=publication_date:desc&per_page={max_results}&mailto={MAILTO}")
    try:
        d = httpx.get(url, headers={"User-Agent": UA}, timeout=40).json()
    except Exception:
        return []
    out = []
    for w in d.get("results", []):
        try:  # this is the resilience fallback — one bad record must not sink the run
            arxiv = None
            for loc in w.get("locations", []):
                u = f"{loc.get('landing_page_url') or ''} {loc.get('pdf_url') or ''}"
                mm = re.search(r"arxiv\.org/abs/(\d{4}\.\d{4,5})", u)
                if mm:
                    arxiv = mm.group(1)
                    break
            doi = (w.get("doi") or "").replace("https://doi.org/", "") or None
            if not (arxiv or doi):
                continue
            # author may be null (anonymised/withdrawn/corporate works) or lack a name.
            authors = [n for a in w.get("authorships", [])
                       if (n := (a.get("author") or {}).get("display_name"))][:12]
            out.append({
                "title": w.get("title") or "",
                "abstract": _abstract_from_inverted(w.get("abstract_inverted_index")),
                "authors": authors,
                "year": w.get("publication_year"),
                "arxiv_id": arxiv,
                "doi": None if arxiv else doi,
                "url": f"https://arxiv.org/abs/{arxiv}" if arxiv else f"https://doi.org/{doi}",
            })
        except Exception as e:  # noqa: BLE001
            print(f"openalex: skip malformed record: {e}", file=sys.stderr)
    return out


def fetch_recent_papers() -> list[dict]:
    papers = fetch_arxiv()
    if not papers:
        print("arXiv unavailable/rate-limited; falling back to OpenAlex", file=sys.stderr)
        papers = fetch_openalex()
    return papers


# --------------------------------------------------------------------------- #
# Structured LLM outputs
# --------------------------------------------------------------------------- #

class QGate(BaseModel):
    name: str
    qubits: list[int]
    params: list[float]


class CardDecision(BaseModel):
    paper_index: int = Field(description="Index into the provided paper list")
    primitive: str = Field(description="The textbook primitive, e.g. 'GHZ-4' or 'QFT-3'")
    title: str = Field(description="Card title, e.g. 'GHZ-4 state preparation — referencing arXiv:2604.02301'")
    n_qubits: int
    gates: list[QGate]
    commentary_md: str = Field(description="150-400 words; see the honesty rules")


class TriageResult(BaseModel):
    decisions: list[CardDecision]


class ReportOut(BaseModel):
    title: str
    meta_description: str
    body_md: str


def _anthropic():
    try:
        import anthropic
    except ImportError:
        _die("the 'anthropic' package is required (pip install anthropic)")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        _die("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic()


# --------------------------------------------------------------------------- #
# qlir validation (local pre-check; server is authoritative)
# --------------------------------------------------------------------------- #

def validate_qlir(circuit: dict) -> str | None:
    """Return an error string if invalid, else None."""
    n = circuit.get("n_qubits")
    if not isinstance(n, int) or not (1 <= n <= MAX_QUBITS):
        return f"n_qubits must be 1..{MAX_QUBITS}"
    gates = circuit.get("gates")
    if not isinstance(gates, list) or not (1 <= len(gates) <= MAX_GATES):
        return f"gates must be a list of 1..{MAX_GATES}"
    for i, g in enumerate(gates):
        name = g.get("name")
        if name not in ALLOWED_GATES:
            return f"gate[{i}].name {name!r} not allowed"
        n_params, n_qubits = ALLOWED_GATES[name]
        qs = g.get("qubits", [])
        if (not isinstance(qs, list) or len(qs) != n_qubits
                or not all(isinstance(q, int) and 0 <= q < n for q in qs)
                or len(set(qs)) != len(qs)):
            return f"gate[{i}].qubits must be {n_qubits} distinct ints in [0,{n})"
        ps = g.get("params", [])
        if not isinstance(ps, list) or len(ps) != n_params or not all(
                isinstance(p, (int, float)) and math.isfinite(p) for p in ps):
            return f"gate[{i}].params must have {n_params} finite numbers"
    return None


# --------------------------------------------------------------------------- #
# research-cards
# --------------------------------------------------------------------------- #

_TRIAGE_SYSTEM = """You are the Provenova research-cards editor. You are given recent \
quant-ph papers. Select ONLY papers whose abstract clearly centers on a textbook-class \
quantum circuit you can faithfully build small, and for each emit a card decision. \
Publishing zero cards is a correct, successful outcome — skip by default.

Faithful textbook primitives only: Bell / GHZ / graph states, Quantum Fourier Transform, \
Grover, Deutsch-Jozsa, Bernstein-Vazirani, QAOA ansatz, hardware-efficient VQE ansatz, \
quantum teleportation, small phase estimation.

For each selected paper return: paper_index (into the given list), primitive, title, \
n_qubits (<=10), gates, and commentary_md.

Circuit rules — qlir gates, allowed names ONLY: h,x,y,z,s,sdg,t,tdg,sx,rz,rx,ry,cx,cz,swap,ccx,id. \
Each gate is {name, qubits (0-indexed, distinct), params}. 1-qubit gates take 1 qubit; \
cx/cz/swap take 2; ccx takes 3; rz/rx/ry take exactly 1 param (radians), all others take []. \
n_qubits<=10 and <=256 gates. Example GHZ-4: n_qubits=4, gates = h[0], cx[0,1], cx[1,2], cx[2,3].

commentary_md rules (HARD): 150-400 words. (a) Name the primitive and what the run shows. \
(b) Restate ONLY what the paper's abstract says — never invent numbers, results, or quotes. \
(c) State explicitly this is a deterministic simulator run on Provenova and NOT a reproduction \
of the paper's hardware results. (d) Never imply the authors endorse it. No raw HTML, no images, \
no marketing superlatives. Do not put the arXiv id or author names in the title as fabricated \
text — reference the paper naturally (the id is added automatically).

If NO paper has a faithful small circuit, return an empty decisions list."""


def _run_research_cards() -> int:
    status = growth_get("/api/v1/growth/status")
    rc = status.get("research_cards", {})
    known = set(rc.get("known_arxiv_ids") or [])
    today, cap = rc.get("today", 0), rc.get("daily_cap", 3)
    print(f"status: {today}/{cap} cards today; {len(known)} arxiv ids seen")
    if today >= cap:
        print("daily cap reached; nothing to do")
        return 0

    # keep the corpus fresh (additive + idempotent; 429 = ran recently, that's fine).
    # Retry while incomplete (deadline-cut), bounded — reassign so the log is current.
    code, body = growth_post("/api/v1/growth/corpus/refresh", {})
    for _ in range(3):
        if not (code == 200 and body.get("complete") is False):
            break
        code, body = growth_post("/api/v1/growth/corpus/refresh", {})
    print(f"corpus refresh: {code} {body if code != 200 else body.get('by_provider', {})}")

    papers = fetch_recent_papers()
    fresh = [p for p in papers if not (p["arxiv_id"] and p["arxiv_id"] in known)]
    print(f"fetched {len(papers)} papers; {len(fresh)} not yet seen")
    if not fresh:
        return 0

    # Ask the model to triage. Enumerate with a stable index it references back.
    listing = "\n\n".join(
        f"[{i}] {p['title']}\n{p['abstract'][:900]}" for i, p in enumerate(fresh))
    client = _anthropic()
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=_TRIAGE_SYSTEM,
        messages=[{"role": "user", "content":
                   f"Here are {len(fresh)} recent quant-ph papers. Select faithful "
                   f"textbook-circuit matches (at most {MAX_CARDS}).\n\n{listing}"}],
        output_format=TriageResult,
    )
    result = resp.parsed_output
    if result is None:
        _die(f"model returned no parseable output (stop_reason={resp.stop_reason})")

    items = []
    used_idx: set[int] = set()
    for d in result.decisions:
        if len(items) >= MAX_CARDS:
            break
        if not (0 <= d.paper_index < len(fresh)):
            print(f"skip: bad paper_index {d.paper_index}", file=sys.stderr)
            continue
        if d.paper_index in used_idx:  # one card per paper — don't burn the cap on one
            print(f"skip: duplicate paper_index {d.paper_index}", file=sys.stderr)
            continue
        used_idx.add(d.paper_index)
        paper = fresh[d.paper_index]
        circuit = {"schema": "qlir/1.0", "n_qubits": d.n_qubits,
                   "gates": [{"name": g.name, "qubits": g.qubits, "params": g.params}
                             for g in d.gates]}
        err = validate_qlir(circuit)
        if err:
            print(f"skip {paper['arxiv_id']}: invalid circuit ({err})", file=sys.stderr)
            continue
        host = (paper["url"].split("/")[2] if "//" in paper["url"] else "")
        if host not in _ALLOWED_HOSTS:
            print(f"skip {paper['arxiv_id']}: url host {host} not allowed", file=sys.stderr)
            continue
        items.append({
            "paper": {"title": paper["title"], "authors": paper["authors"] or ["Unknown"],
                      "year": paper["year"], "arxiv_id": paper["arxiv_id"],
                      "doi": paper["doi"], "url": paper["url"]},
            "circuit": circuit, "shots": 4096, "seed": 1729,
            "title": d.title, "commentary_md": d.commentary_md,
        })

    if not items:
        print("no faithful circuits identified this run (a successful, quiet run)")
        return 0

    code, body = growth_post("/api/v1/growth/research-cards", {"items": items})
    if code != 200:
        _die(f"research-cards POST failed: {code} {body}")
    print(f"published: created={body.get('created')}")
    for it in body.get("items", []):
        print(f"  {it.get('status')}: {it.get('card_url') or it.get('slug') or it}")
    return 0


# --------------------------------------------------------------------------- #
# weekly-report
# --------------------------------------------------------------------------- #

_REPORT_SYSTEM = """You are the Provenova weekly-report writer. Write the weekly \
"State of Quantum Hardware" report from the REAL platform data provided. Every number \
in the report MUST come from the data given to you — no estimates, no outside knowledge, \
no fabrication. Link the underlying pages (the leaderboard at /leaderboard, device pages \
at /hardware/<provider>/<backend>, and cards at /cards/<slug>).

Return title, meta_description (30-200 chars, factual), and body_md (800-1500 words). \
Structure body_md: 'This week's fleet' (corpus size, providers, notable rankings per metric \
with source/licence caveats — vendor-reported means a manufacturer claim, not an independent \
measurement); 'New on the platform' (research cards published, linked); and a short 'Method note' \
(sources: IBM Apache-2.0 calibration, Metriq CC-BY-4.0, vendor-reported specs; all platform runs \
are deterministic simulator executions). No raw HTML, no images, no superlatives."""


def _iso_week_slug() -> str:
    y, w, _ = _today().isocalendar()
    return f"state-of-quantum-{y}-w{w:02d}"


def _run_weekly_report() -> int:
    slug = _iso_week_slug()
    status = growth_get("/api/v1/growth/status")
    if (status.get("reports") or {}).get("latest_slug") == slug:
        print(f"{slug} already published; nothing to do")
        return 0

    metrics = {}
    for m in ("two_q_fidelity", "eplg", "clops", "qaoa_ratio"):
        try:
            metrics[m] = growth_get(f"/api/v1/leaderboard?metric={m}").get("entries", [])
        except Exception:
            metrics[m] = []
    data = {"corpus": status.get("corpus"), "research_cards": status.get("research_cards"),
            "leaderboards": metrics, "week_slug": slug}

    client = _anthropic()
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=_REPORT_SYSTEM,
        messages=[{"role": "user", "content":
                   f"Write this week's report ({slug}) from this data. Use only these "
                   f"numbers.\n\n{_json(data)}"}],
        output_format=ReportOut,
    )
    out = resp.parsed_output
    if out is None:
        _die(f"model returned no parseable output (stop_reason={resp.stop_reason})")

    code, body = growth_post("/api/v1/growth/reports", {
        "slug": slug, "title": out.title,
        "meta_description": out.meta_description[:200], "body_md": out.body_md})
    if code == 409:
        print(f"{slug} already published (409); done")
        return 0
    if code != 200:
        _die(f"reports POST failed: {code} {body}")
    print(f"published report: {body.get('url')}")
    return 0


# --------------------------------------------------------------------------- #
# helpers + entrypoint
# --------------------------------------------------------------------------- #

def _today() -> _dt.date:
    return _dt.datetime.now(_dt.timezone.utc).date()


def _json(obj) -> str:
    import json
    return json.dumps(obj, indent=1, default=str)[:12000]


def _run_status() -> int:
    print(_json(growth_get("/api/v1/growth/status")))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Provenova growth agent")
    ap.add_argument("command", choices=["research-cards", "weekly-report", "status"])
    args = ap.parse_args(argv)
    if args.command == "research-cards":
        return _run_research_cards()
    if args.command == "weekly-report":
        return _run_weekly_report()
    return _run_status()


if __name__ == "__main__":
    sys.exit(main())
