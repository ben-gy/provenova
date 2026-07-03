"""In-app documentation: an authored markdown site + generated reference pages.

Content pages are authored Markdown under ``web/docs/*.md`` and rendered
server-side (cached by mtime). Reference pages (``frameworks``, ``api``, ``cli``)
are generated from live code/data so they can't drift. The API reference is
deliberately rendered as readable, example-driven endpoint entries — not a raw
method/path table — so it reads like a developer guide, not a Swagger dump.

``page_html()`` / ``home_context()`` are the entry points used by the web routes.
"""

from __future__ import annotations

import html as _html
import re
from pathlib import Path
from typing import Any

import markdown as _markdown

DOCS_DIR = Path(__file__).resolve().parent / "docs"

# Ordered navigation. ``source`` is ``file:<name>.md`` or ``gen:<key>``.
# ``layout: "split"`` renders the page in the two-pane (prose + code rail) shell.
DOCS_MANIFEST: list[dict] = [
    {"title": "Introduction", "pages": [
        {"slug": "overview", "title": "What is Provenova?", "source": "file:overview.md"},
        {"slug": "getting-started", "title": "Getting started", "source": "file:getting-started.md",
         "layout": "split"},
    ]},
    {"title": "Concepts & workflows", "pages": [
        {"slug": "core-concepts", "title": "Core concepts", "source": "file:core-concepts.md"},
        {"slug": "capturing-runs", "title": "Capturing runs", "source": "file:capturing-runs.md",
         "layout": "split"},
        {"slug": "reproduce-and-drift", "title": "Reproduce & drift", "source": "file:reproduce-and-drift.md"},
        {"slug": "result-cards-and-badges", "title": "Result cards & badges", "source": "file:result-cards-and-badges.md"},
        {"slug": "compliance", "title": "Compliance & attestations", "source": "file:compliance.md"},
        {"slug": "corpus-and-leaderboard", "title": "Corpus & leaderboard", "source": "file:corpus-and-leaderboard.md"},
    ]},
    {"title": "Using the app", "pages": [
        {"slug": "product-tour", "title": "Product tour (every page & button)", "source": "file:product-tour.md"},
        {"slug": "pricing-faq", "title": "Pricing FAQ", "source": "file:pricing-faq.md"},
    ]},
    {"title": "Libraries & reference", "pages": [
        {"slug": "libraries", "title": "Libraries & downloads", "source": "file:libraries.md"},
        {"slug": "cli", "title": "CLI reference", "source": "gen:cli"},
        {"slug": "api", "title": "API reference", "source": "gen:api"},
        {"slug": "frameworks", "title": "Frameworks reference", "source": "gen:frameworks"},
        {"slug": "open-schemas", "title": "Open schemas (qlprov)", "source": "file:open-schemas.md"},
        {"slug": "deployment", "title": "Deployment & self-hosting", "source": "file:deployment.md"},
    ]},
]

# Curated cards for the docs home.
HOME_CARDS: list[dict] = [
    {"slug": "getting-started", "eyebrow": "Start here", "title": "Getting started",
     "desc": "Record and reproduce your first quantum run in under five minutes — offline, no account."},
    {"slug": "core-concepts", "eyebrow": "Concepts", "title": "Core concepts",
     "desc": "Provenance, content-addressing, the Merkle run-hash and the tamper-evident ledger."},
    {"slug": "capturing-runs", "eyebrow": "Guide", "title": "Capturing runs",
     "desc": "The @ql.capture decorator, the local ledger and the vendor connector plugins."},
    {"slug": "reproduce-and-drift", "eyebrow": "Guide", "title": "Reproduce & drift",
     "desc": "Re-run against a drifted device state; read the Hellinger score, verdict and diff."},
    {"slug": "compliance", "eyebrow": "Guide", "title": "Compliance & attestations",
     "desc": "Frameworks-as-data, evidence rules, gaps, and signed, verifiable attestations."},
    {"slug": "api", "eyebrow": "Reference", "title": "API reference",
     "desc": "Every REST endpoint with example requests and responses."},
    {"slug": "cli", "eyebrow": "Reference", "title": "CLI reference",
     "desc": "The ql command-line tool: init, demo, capture-target, list, show, reproduce, push."},
    {"slug": "deployment", "eyebrow": "Operate", "title": "Deployment & self-hosting",
     "desc": "docker-compose, environment variables, SQLite vs PostgreSQL, air-gapped mode."},
]

HOME_HERO = ("Bind every quantum run to the exact calibration and hardware state that produced it — so "
             "results are reproducible, comparable, shareable and auditable across vendors. These docs "
             "cover the concepts, the workflows, every page of the app, the libraries and the full API.")

# Flat lookups + ordering for prev/next.
_PAGES: dict[str, dict] = {}
_ORDER: list[str] = []
for _section in DOCS_MANIFEST:
    for _pg in _section["pages"]:
        _pg = {**_pg, "section": _section["title"]}
        _PAGES[_pg["slug"]] = _pg
        _ORDER.append(_pg["slug"])


def all_slugs() -> list[str]:
    """Every docs page slug, in manifest order (used by the sitemap)."""
    return list(_ORDER)

# --------------------------------------------------------------------------- #
# Markdown rendering (mtime-cached) + callouts
# --------------------------------------------------------------------------- #

_MD_EXTS = ["fenced_code", "tables", "toc", "attr_list", "sane_lists", "md_in_html"]
_cache: dict[str, tuple[float, str, str]] = {}

_CALLOUT_KINDS = {"Note": "note", "Tip": "tip", "Warning": "warning"}
# Inline Heroicons (v2 outline, MIT) — professional line icons instead of emoji.
_ICON_ATTRS = ('viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" '
               'stroke-linecap="round" stroke-linejoin="round" width="18" height="18" '
               'style="display:inline-block;vertical-align:top"')
_CALLOUT_ICON = {
    "note": f'<svg {_ICON_ATTRS}><path d="m11.25 11.25.041-.02a.75.75 0 0 1 1.063.852l-.708 2.836'
            'a.75.75 0 0 0 1.063.853l.041-.021M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9-3.75h.008v.008H12V8.25Z"/></svg>',
    "tip": f'<svg {_ICON_ATTRS}><path d="M12 18v-5.25m0 0a6.01 6.01 0 0 0 1.5-.189m-1.5.189a6.01 6.01 0 0 1-1.5-.189'
           'm3.75 7.478a12.06 12.06 0 0 1-4.5 0m3.75 2.383a14.406 14.406 0 0 1-3 0M14.25 18v-.192c0-.983.658-1.823 '
           '1.508-2.316a7.5 7.5 0 1 0-7.517 0c.85.493 1.509 1.333 1.509 2.316V18"/></svg>',
    "warning": f'<svg {_ICON_ATTRS}><path d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 '
               '2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z"/></svg>',
}
# <blockquote>\n<p><strong>Note:</strong> ...  ->  callout box
_CALLOUT_RE = re.compile(
    r"<blockquote>\s*<p><strong>(Note|Tip|Warning):</strong>(.*?)</p>\s*</blockquote>",
    re.DOTALL,
)


def _apply_callouts(html: str) -> str:
    def repl(m: re.Match) -> str:
        kind = _CALLOUT_KINDS[m.group(1)]
        body = m.group(2).strip()
        icon = _CALLOUT_ICON[kind]
        return (f'<div class="callout callout--{kind}"><span class="callout__icon">{icon}</span>'
                f'<div class="callout__body"><p>{body}</p></div></div>')
    return _CALLOUT_RE.sub(repl, html)


def _render_markdown(text: str) -> tuple[str, str]:
    md = _markdown.Markdown(extensions=_MD_EXTS,
                            extension_configs={"toc": {"permalink": False, "toc_depth": "2-3"}})
    body = _apply_callouts(md.convert(text))
    toc = getattr(md, "toc", "") or ""
    return body, toc


def _render_file(slug: str, filename: str) -> tuple[str, str]:
    path = DOCS_DIR / filename
    if not path.exists():
        return (f"<p>Documentation page <code>{_html.escape(filename)}</code> is missing.</p>", "")
    mtime = path.stat().st_mtime
    cached = _cache.get(slug)
    if cached and cached[0] == mtime:
        return cached[1], cached[2]
    body, toc = _render_markdown(path.read_text(encoding="utf-8"))
    _cache[slug] = (mtime, body, toc)
    return body, toc


# --------------------------------------------------------------------------- #
# Generated: frameworks reference
# --------------------------------------------------------------------------- #

def _h(text: Any) -> str:
    return _html.escape(str(text))


def _gen_frameworks(db) -> tuple[str, str]:
    from sqlalchemy import select

    from provenova_core.models import ComplianceFramework, Control

    from ..services import compliance as comp

    frameworks = db.scalars(select(ComplianceFramework).order_by(ComplianceFramework.key)).all()
    if not frameworks:
        return ("<p>No frameworks are loaded. They are read from <code>frameworks/*.yaml</code> "
                "at server startup.</p>", "")

    parts = ["<p>The compliance standards Provenova ships with, generated live from the definitions "
             "currently loaded. Each control lists what the standard requires, the automated checks used "
             "as evidence, and how to remediate a gap.</p>"]
    toc = ['<div class="toc"><ul>']
    for fw in frameworks:
        anchor = _h(fw.key)
        toc.append(f'<li><a href="#{anchor}">{_h(fw.name)}</a></li>')
        parts.append(f'<h2 id="{anchor}">{_h(fw.name)} '
                     f'<span style="font-weight:400;color:#94a3b8">v{_h(fw.version)}</span></h2>')
        if fw.description:
            parts.append(f"<p>{_h(fw.description)}</p>")
        controls = db.scalars(select(Control).where(Control.framework_id == fw.id)
                              .order_by(Control.key)).all()
        for c in controls:
            parts.append('<div class="ref-card">')
            parts.append(f'<div class="ref-card__title">{_h(c.key)} — {_h(c.title)} '
                         f'<span style="font-size:12px;color:#94a3b8">({_h(c.severity)})</span></div>')
            if c.requirement_text:
                parts.append(f'<div class="ref-card__desc">{_h(c.requirement_text)}</div>')
            rules = comp.rules_view(c)
            if rules:
                parts.append("<ul>")
                for r in rules:
                    parts.append(f"<li>{_h(r['description'])} <code>{_h(r['id'])}</code></li>")
                parts.append("</ul>")
            if c.remediation:
                parts.append(f'<div class="ref-card__desc"><b>Remediation:</b> {_h(c.remediation)}</div>')
            parts.append("</div>")
    toc.append("</ul></div>")
    return "\n".join(parts), "\n".join(toc)


# --------------------------------------------------------------------------- #
# Generated: API reference (narrative, example-driven — not Swagger)
# --------------------------------------------------------------------------- #

# path-prefix -> (resource group title, order)
_API_GROUPS: list[tuple[str, str]] = [
    ("/api/v1/ingest", "Ingest"),
    ("/api/v1/runs", "Runs"),
    ("/api/v1/cards", "Result cards"),
    ("/badge", "Badges"),
    ("/api/v1/leaderboard", "Corpus & leaderboard"),
    ("/api/v1/backends", "Corpus & leaderboard"),
    ("/api/v1/frameworks", "Compliance"),
    ("/api/v1/workspaces", "Compliance"),
    ("/api/v1/attestations", "Attestations"),
    ("/.well-known", "Attestations"),
    ("/api/v1/trust", "Trust center"),
    ("/api/v1/auth", "Auth & accounts"),
    ("/api/v1/me", "Auth & accounts"),
    ("/api/v1/orgs", "Auth & accounts"),
    ("/api/v1/health", "Health"),
]
_GROUP_ORDER = ["Ingest", "Runs", "Result cards", "Badges", "Corpus & leaderboard", "Compliance",
                "Attestations", "Trust center", "Auth & accounts", "Health"]

# Curated descriptions + examples keyed by (METHOD, path). Missing entries fall
# back to the route summary; examples are optional.
_API_META: dict[tuple[str, str], dict] = {
    ("POST", "/api/v1/ingest/runs"): {
        "desc": "Ingest a run bundle from the SDK. Idempotent by run_hash — re-sending the same run returns \"status\": \"exists\" with the original run_id.",
        "curl": "curl -X POST {base}/api/v1/ingest/runs \\\n  -H \"Authorization: Bearer $QL_TOKEN\" \\\n  -H \"Content-Type: application/json\" \\\n  --data @run.qlprov.json",
        "resp": '{\n  "status": "created",\n  "run_id": "01J...",\n  "run_hash": "b3f1...e9",\n  "hash_matched_client": true\n}',
    },
    ("GET", "/api/v1/runs"): {
        "desc": "List runs in your workspace, most recent first.",
        "curl": "curl {base}/api/v1/runs \\\n  -H \"Authorization: Bearer $QL_TOKEN\"",
        "resp": '[\n  { "id": "01J...", "project": "bell-demo", "vendor": "ibm", "backend": "ibm_kyiv", "shots": 4096, "status": "completed", "run_hash": "b3f1...e9", "created_at": "2026-07-03T12:00:00+00:00" }\n]',
    },
    ("GET", "/api/v1/runs/{run_id}"): {
        "desc": "Fetch the full provenance document (qlprov/run/1.0) for a run. Verifies its own hash offline.",
        "curl": "curl {base}/api/v1/runs/01J... \\\n  -H \"Authorization: Bearer $QL_TOKEN\"",
    },
    ("POST", "/api/v1/runs/{run_id}/reproduce"): {
        "desc": "Re-run the stored circuit against a drifted device state and score the result.",
        "curl": "curl -X POST \"{base}/api/v1/runs/01J.../reproduce?days=90&profile=bad_day\" \\\n  -H \"Authorization: Bearer $QL_TOKEN\"",
        "resp": '{\n  "reproduced_run_id": "01K...",\n  "verdict": "drifted",\n  "reproducibility_score": 0.947,\n  "report": { ... }\n}',
    },
    ("POST", "/api/v1/runs/{run_id}/card/publish"): {
        "desc": "Publish a run as a public, citable Result Card with provenance and badges.",
    },
    ("GET", "/api/v1/cards/{slug}"): {
        "desc": "Public Result Card metadata as JSON (no auth required).",
        "curl": "curl {base}/api/v1/cards/ghz-3-ibm-kyiv",
    },
    ("GET", "/api/v1/cards/{slug}/citation"): {
        "desc": "Ready-made citation for a card in BibTeX, CSL-JSON or RIS (?format=bibtex|csl|ris).",
    },
    ("GET", "/api/v1/leaderboard"): {
        "desc": "Cross-vendor hardware ranking by a calibration metric (?metric=median_2q_error|...).",
        "curl": "curl {base}/api/v1/leaderboard?metric=median_2q_error",
    },
    ("POST", "/api/v1/workspaces/{ws_id}/compliance/evaluate"): {
        "desc": "Evaluate every enabled framework against the workspace's runs. Safe to call repeatedly.",
    },
    ("POST", "/api/v1/workspaces/{ws_id}/attestations"): {
        "desc": "Issue an Ed25519-signed attestation over the collected evidence for a framework.",
    },
    ("GET", "/api/v1/attestations/{att_id}/verify"): {
        "desc": "Verify an attestation's signature and evidence root (public — no auth).",
        "curl": "curl {base}/api/v1/attestations/01J.../verify",
        "resp": '{\n  "attestation_id": "01J...",\n  "kid": "ql-att-...",\n  "evidence_root": "9c2a...f0",\n  "valid": true,\n  "checks": { "signature": true, "expired": false, "revoked": false, "tampered": false },\n  "reason": null\n}',
    },
    ("GET", "/.well-known/provenova-jwks.json"): {
        "desc": "Public JWKS for verifying attestation signatures yourself.",
    },
    ("GET", "/api/v1/me"): {
        "desc": "The authenticated principal: account, org, workspace, effective plan and features.",
        "curl": "curl {base}/api/v1/me -H \"Authorization: Bearer $QL_TOKEN\"",
    },
}


def _collect_api_routes(routes) -> list:
    """Flatten APIRoutes, descending through included/mounted sub-routers."""
    from fastapi.routing import APIRoute

    out: list = []
    for r in routes:
        if isinstance(r, APIRoute):
            out.append(r)
            continue
        orig = getattr(r, "original_router", None)
        sub = getattr(orig, "routes", None) if orig is not None else getattr(r, "routes", None)
        if sub:
            out.extend(_collect_api_routes(sub))
    return out


def _api_group(path: str) -> str | None:
    for prefix, group in _API_GROUPS:
        if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix):
            return group
    return None


def _gen_api(request) -> tuple[str, str]:
    from ..config import get_settings

    base = get_settings().base_url or "https://your-server"

    groups: dict[str, list[tuple[str, str]]] = {}
    seen: set = set()
    for route in _collect_api_routes(request.app.routes):
        path = route.path
        group = _api_group(path)
        if group is None:  # skip the HTML web app; document the developer API only
            continue
        for m in sorted(x for x in route.methods if x not in {"HEAD", "OPTIONS"}):
            key = (m, path)
            if key in seen:
                continue
            seen.add(key)
            fallback = route.summary or (route.name.replace("_", " ").capitalize() if route.name else "")
            groups.setdefault(group, []).append((m, path, fallback))

    intro = (
        "<p>The Provenova REST API. All responses are JSON. Authenticate with an API key as a bearer "
        "token — create one under <a href=\"/app/settings\">Settings → API keys</a>:</p>"
        f'<div class="code-block"><div class="code-block__bar"><span class="code-block__lang">Shell</span></div>'
        f'<pre><code class="language-bash">export QL_TOKEN=ql_live_...\n'
        f'curl {_h(base)}/api/v1/me -H "Authorization: Bearer $QL_TOKEN"</code></pre></div>'
    )
    parts = [intro]
    toc = ['<div class="toc"><ul>']

    ordered = [g for g in _GROUP_ORDER if g in groups] + [g for g in groups if g not in _GROUP_ORDER]
    for group in ordered:
        anchor = group.lower().replace(" & ", "-").replace(" ", "-")
        toc.append(f'<li><a href="#{anchor}">{_h(group)}</a></li>')
        parts.append(f'<h2 id="{anchor}">{_h(group)}</h2>')
        for method, path, fallback in sorted(set(groups[group]), key=lambda t: (t[1], t[0])):
            meta = _API_META.get((method, path), {})
            desc = meta.get("desc") or fallback
            parts.append('<div class="endpoint">')
            parts.append('<div class="endpoint__head">'
                         f'<span class="endpoint__method {method.lower()}">{method}</span>'
                         f'<span class="endpoint__path">{_h(path)}</span></div>')
            if desc:
                parts.append(f'<p class="endpoint__desc">{_h(desc)}</p>')
            if meta.get("curl"):
                curl = meta["curl"].replace("{base}", base)
                parts.append('<div class="code-block"><div class="code-block__bar">'
                             '<span class="code-block__lang">cURL</span></div>'
                             f'<pre><code class="language-bash">{_h(curl)}</code></pre></div>')
            if meta.get("resp"):
                parts.append('<div class="code-block"><div class="code-block__bar">'
                             '<span class="code-block__lang">Response</span></div>'
                             f'<pre><code class="language-json">{_h(meta["resp"])}</code></pre></div>')
            parts.append("</div>")
    toc.append("</ul></div>")
    return "\n".join(parts), "\n".join(toc)


def _gen_cli() -> tuple[str, str]:
    try:
        import typer  # noqa: F401
        from typer.main import get_command

        from provenova.cli.__main__ import app as cli_app  # type: ignore

        group = get_command(cli_app)
        commands = getattr(group, "commands", {})
    except Exception:
        return ("<p>The <code>ql</code> CLI ships with the <code>provenova</code> SDK "
                "(<code>pip install provenova</code>). Install it to see the generated command "
                "reference here, or run <code>ql --help</code>.</p>", "")

    parts = ["<p>The <code>ql</code> command-line tool ships with the open-source "
             "<code>provenova</code> SDK. Generated from the installed CLI.</p>"]
    toc = ['<div class="toc"><ul>']
    for name in sorted(commands):
        cmd = commands[name]
        anchor = _h(name)
        toc.append(f'<li><a href="#ql-{anchor}">ql {anchor}</a></li>')
        parts.append('<div class="ref-card">')
        parts.append(f'<div class="ref-card__title" id="ql-{anchor}">ql {_h(name)}</div>')
        if cmd.help:
            parts.append(f'<div class="ref-card__desc">{_h(cmd.help)}</div>')
        opts = []
        for param in getattr(cmd, "params", []):
            flags = ", ".join(param.opts) if getattr(param, "opts", None) else param.name
            opts.append((flags, getattr(param, "help", "") or ""))
        if opts:
            parts.append("<ul>")
            for flags, help_txt in opts:
                parts.append(f"<li><code>{_h(flags)}</code>{(' — ' + _h(help_txt)) if help_txt else ''}</li>")
            parts.append("</ul>")
        parts.append("</div>")
    toc.append("</ul></div>")
    return "\n".join(parts), "\n".join(toc)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def default_slug() -> str:
    return _ORDER[0]


def get_page(slug: str) -> dict | None:
    return _PAGES.get(slug)


def neighbours(slug: str) -> tuple[dict | None, dict | None]:
    if slug not in _PAGES:
        return None, None
    i = _ORDER.index(slug)
    prev = _PAGES[_ORDER[i - 1]] if i > 0 else None
    nxt = _PAGES[_ORDER[i + 1]] if i < len(_ORDER) - 1 else None
    return prev, nxt


def home_context() -> dict:
    return {"hero": HOME_HERO, "cards": HOME_CARDS,
            "quickstart_slug": "getting-started"}


def page_html(slug: str, request, db) -> dict | None:
    """Return ``{title, section, layout, body, toc, prev, next}`` or None."""
    page = _PAGES.get(slug)
    if page is None:
        return None
    source = page["source"]
    if source.startswith("file:"):
        body, toc = _render_file(slug, source[len("file:"):])
    elif source == "gen:frameworks":
        body, toc = _gen_frameworks(db)
    elif source == "gen:api":
        body, toc = _gen_api(request)
    elif source == "gen:cli":
        body, toc = _gen_cli()
    else:
        body, toc = ("<p>Unknown page source.</p>", "")
    prev, nxt = neighbours(slug)
    return {"title": page["title"], "section": page["section"], "layout": page.get("layout", "default"),
            "body": body, "toc": toc, "prev": prev, "next": nxt}
