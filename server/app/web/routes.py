"""Server-rendered web UI (Jinja + HTMX-friendly forms)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from quantumledger_core.models import (
    Account,
    ApiKey,
    Attestation,
    ComplianceFramework,
    Control,
    EvidenceItem,
    Org,
    OrgMembership,
    ReproductionEvent,
    ResultCard,
    Run,
    Workspace,
    WorkspaceFramework,
)
from quantumledger_core.reproduce import runner
from quantumledger_core.reproduce.report import build_report

from quantumledger_core.models import PLAN_DISPLAY

from ..config import get_settings
from ..db import attestation_key, get_db
from ..deps import Principal, current_principal
from ..entitlements import is_unlimited, quota_for
from ..security import generate_api_key
from ..services import accounts as acc_svc
from ..services import cards as cards_svc
from ..services import compliance as comp
from ..services import limits as limits_svc
from ..services import settings as settings_svc
from ..services.attestation import create_attestation

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

from . import docs as docs_mod  # noqa: E402
from .glossary import register as _register_glossary  # noqa: E402

_register_glossary(templates)


def render(request: Request, name: str, p: Principal | None = None, **ctx) -> HTMLResponse:
    base = {"principal": p, "settings": get_settings()}
    base.update(ctx)
    return templates.TemplateResponse(request, name, base)


@router.get("/", response_class=HTMLResponse)
def landing(request: Request, db: Session = Depends(get_db),
            p: Principal | None = Depends(current_principal)):
    recent = []
    stats = {}
    if p:
        q = select(Run).order_by(Run.created_at.desc()).limit(8)
        if p.workspace_id:
            q = q.where(Run.workspace_id == p.workspace_id)
        recent = db.scalars(q).all()
        runs_count = db.scalar(
            select(func.count(Run.id)).where(Run.workspace_id == p.workspace_id)
        ) or 0
        repro_count = db.scalar(
            select(func.count(ReproductionEvent.id))
            .join(Run, Run.id == ReproductionEvent.original_run_id)
            .where(Run.workspace_id == p.workspace_id)
        ) or 0
        cards_count = db.scalar(
            select(func.count(ResultCard.id)).where(
                ResultCard.workspace_id == p.workspace_id, ResultCard.visibility == "public"
            )
        ) or 0
        stats = {"runs": runs_count, "reproductions": repro_count, "public_cards": cards_count}
    activation = _activation_state(db, p) if p else None
    usage = limits_svc.private_run_usage(db, p.plan, p.workspace_id) if p else None
    return render(request, "landing.html", p, recent=recent, stats=stats, activation=activation,
                  usage=usage)


def _activation_state(db: Session, p: Principal) -> dict:
    """Real setup progress for the dashboard checklist + demo-seed button."""
    has_key = db.scalar(select(ApiKey.id).where(
        ApiKey.org_id == p.org_id, ApiKey.revoked.is_(False))) is not None
    n_runs = db.scalar(select(func.count(Run.id)).where(Run.workspace_id == p.workspace_id)) or 0
    has_fw = db.scalar(select(WorkspaceFramework.id).where(
        WorkspaceFramework.workspace_id == p.workspace_id)) is not None
    has_card = db.scalar(select(ResultCard.id).where(
        ResultCard.workspace_id == p.workspace_id)) is not None
    has_att = db.scalar(select(Attestation.id).where(
        Attestation.workspace_id == p.workspace_id)) is not None
    can_comp = p.has("compliance_frameworks")
    steps = [
        {"label": "Create an API key", "done": has_key, "href": "/app/start",
         "hint": "Authenticate the SDK so it can push runs here."},
        {"label": "Capture & push your first run", "done": n_runs > 0, "href": "/app/start",
         "hint": "Wrap a job with @ql.capture, then ql push."},
        {"label": "Enable a compliance framework", "done": has_fw,
         "href": "/app/compliance" if can_comp else "/app/plans",
         "hint": "Map your runs to FAIR, IEEE P7131 and more." if can_comp else "Available on Pro and up.",
         "locked": not can_comp},
        {"label": "Publish a card or issue an attestation", "done": has_card or has_att,
         "href": "/app/records", "hint": "Share a citable result, or sign your compliance evidence."},
    ]
    done = sum(1 for s in steps if s["done"])
    return {"steps": steps, "done": done, "total": len(steps),
            "complete": done == len(steps), "workspace_empty": n_runs == 0}


@router.post("/app/demo-seed")
def web_demo_seed(request: Request, db: Session = Depends(get_db),
                  p: Principal | None = Depends(current_principal)):
    if p is None:
        return RedirectResponse("/login", status_code=303)
    ws = db.get(Workspace, p.workspace_id)
    from ..services import demo_seed

    if ws is not None and demo_seed.is_empty(db, ws):
        demo_seed.seed_workspace(db, ws, account_id=p.account_id)
        db.commit()
        return RedirectResponse("/app/records?seeded=1", status_code=303)
    return RedirectResponse("/app/records", status_code=303)


@router.get("/app/start", response_class=HTMLResponse)
def app_start(request: Request, db: Session = Depends(get_db),
              p: Principal | None = Depends(current_principal)):
    if p is None:
        return RedirectResponse("/login", status_code=303)
    keys = db.scalars(select(ApiKey).where(
        ApiKey.org_id == p.org_id, ApiKey.revoked.is_(False))).all()
    new_key = request.session.pop("started_api_key", None)
    return render(request, "start.html", p, endpoint=get_settings().base_url,
                  api_keys=keys, new_api_key=new_key)


@router.post("/app/start/api-key")
def app_start_key(request: Request, db: Session = Depends(get_db),
                  p: Principal | None = Depends(current_principal)):
    if p is None:
        return RedirectResponse("/login", status_code=303)
    full, prefix, key_hash = generate_api_key()
    db.add(ApiKey(org_id=p.org_id, account_id=p.account_id, name="quickstart",
                  prefix=prefix, key_hash=key_hash))
    db.commit()
    request.session["started_api_key"] = full  # shown once on the next render
    return RedirectResponse("/app/start", status_code=303)


@router.get("/app/plans", response_class=HTMLResponse)
def app_plans(request: Request, db: Session = Depends(get_db),
              p: Principal | None = Depends(current_principal)):
    if p is None:
        return RedirectResponse("/login", status_code=303)
    from quantumledger_core.models import PLAN_ORDER

    from ..entitlements import FEATURES, QUOTAS

    return render(request, "plans.html", p, plan_order=PLAN_ORDER, features=FEATURES,
                  quotas=QUOTAS, current=p.plan, feature_labels=_FEATURE_LABELS,
                  plan_blurbs=_PLAN_BLURBS, plan_display=PLAN_DISPLAY)


_FEATURE_LABELS = {
    "public_result_cards": "Public, citable result cards",
    "badges": "Embeddable maturity badges",
    "reproduce": "Reproduce & drift analysis",
    "private_records": "Private records",
    "workspace_sharing": "Workspace sharing / seats",
    "analytics_depth": "Deep analytics",
    "compare_vs_fleet": "Compare vs. the public fleet",
    "compliance_frameworks": "Compliance frameworks",
    "continuous_monitoring": "Continuous monitoring & alerts",
    "attestation_signing": "Signed attestations",
    "trust_center": "Public Trust Center",
    "self_host": "Self-hosting",
    "sso_saml": "SSO / SAML",
    "data_residency": "Data residency",
    "sla": "Support SLA",
}

_PLAN_BLURBS = {
    "free": "Everything you need to capture, reproduce, benchmark & publish — private by default.",
    "academic": "Free for verified academic domains — unlimited private records & signed FAIR attestations.",
    "pro": "For teams — compliance frameworks, continuous monitoring & signed attestations.",
    "lab": "For labs — SSO, a public Trust Center & a self-hostable signing service.",
    "enterprise": "Everything, plus data residency, custom controls & an SLA.",
}

# Indicative list pricing. Provisioning is admin-granted (no self-serve checkout);
# paid tiers are "request access" to the contact address below.
_CONTACT = "mailto:hi@ben.gy"
_PRICING = {
    "free": {"price": "$0", "cadence": "forever", "tagline": "For individuals getting started",
             "highlights": ["Capture, reproduce & benchmark runs", "Unlimited public result cards & badges",
                            "250 private records", "Compare vs. the public fleet",
                            "FAIR compliance checklist", "Full qlprov export"],
             "cta": ("Get started", "/register"), "highlight": False},
    "academic": {"price": "$0", "cadence": "for verified academia", "tagline": "Free for .edu / .ac.* domains",
                 "highlights": ["Everything in Free", "Unlimited private records",
                                "Signed FAIR attestations", "15 seats"],
                 "cta": ("Sign up with your academic email", "/register"), "highlight": False},
    "pro": {"price": "$199", "cadence": "per month · $1,990/yr", "tagline": "For research teams",
            "highlights": ["Everything in Academic", "All compliance frameworks", "Signed attestations",
                           "Continuous monitoring & alerts", "10 seats"],
            "cta": ("Request access", _CONTACT + "?subject=QuantumLedger%20Team"),
            "highlight": True},
    "lab": {"price": "$499", "cadence": "per month", "tagline": "For labs & departments",
            "highlights": ["Everything in Team", "SSO / SAML", "Public Trust Center",
                           "Self-hostable signing service", "50 seats"],
            "cta": ("Request access", _CONTACT + "?subject=QuantumLedger%20Lab"),
            "highlight": False},
    "enterprise": {"price": "Custom", "cadence": "", "tagline": "For organizations with scale & governance needs",
                   "highlights": ["Everything in Lab", "Data residency", "Custom controls",
                                  "Support SLA", "Unlimited seats"],
                   "cta": ("Contact us", _CONTACT + "?subject=QuantumLedger%20Enterprise"),
                   "highlight": False},
}


@router.get("/pricing", response_class=HTMLResponse)
def pricing(request: Request, p: Principal | None = Depends(current_principal)):
    """Public pricing page — no login required."""
    from quantumledger_core.models import PLAN_ORDER

    from ..entitlements import FEATURES, QUOTAS

    return render(request, "pricing.html", p, plan_order=PLAN_ORDER, features=FEATURES,
                  quotas=QUOTAS, feature_labels=_FEATURE_LABELS, plan_blurbs=_PLAN_BLURBS,
                  pricing=_PRICING, current=(p.plan if p else None), plan_display=PLAN_DISPLAY)


# -- auth -------------------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, p: Principal | None = Depends(current_principal)):
    return render(request, "login.html", p, mode="login")


@router.get("/register", response_class=HTMLResponse)
def register_form(request: Request, p: Principal | None = Depends(current_principal)):
    return render(request, "login.html", p, mode="register")


def _establish_session(request: Request, db: Session, acc: Account) -> None:
    request.session["account_id"] = acc.id
    request.session.pop("mfa_pending", None)
    mem = db.scalar(select(OrgMembership).where(OrgMembership.account_id == acc.id))
    if mem:
        ws = db.scalar(select(Workspace).where(Workspace.org_id == mem.org_id))
        if ws:
            request.session["workspace_id"] = ws.id


@router.post("/login")
def do_login(request: Request, email: str = Form(...), password: str = Form(...),
             db: Session = Depends(get_db)):
    acc = acc_svc.authenticate(db, email=email, password=password)
    if acc is None:
        return render(request, "login.html", None, mode="login", error="Invalid credentials")
    if settings_svc.mfa_enabled(db, acc):
        request.session["mfa_pending"] = acc.id
        return RedirectResponse("/login/mfa", status_code=303)
    _establish_session(request, db, acc)
    return RedirectResponse("/", status_code=303)


@router.get("/login/mfa", response_class=HTMLResponse)
def login_mfa_form(request: Request):
    if not request.session.get("mfa_pending"):
        return RedirectResponse("/login", status_code=303)
    return render(request, "login.html", None, mode="mfa")


@router.post("/login/mfa")
def do_login_mfa(request: Request, code: str = Form(...), db: Session = Depends(get_db)):
    acc_id = request.session.get("mfa_pending")
    if not acc_id:
        return RedirectResponse("/login", status_code=303)
    acc = db.get(Account, acc_id)
    cred = settings_svc.get_mfa(db, acc) if acc else None
    if not (cred and settings_svc.verify_code(cred.secret, code)):
        return render(request, "login.html", None, mode="mfa", error="Invalid authentication code")
    _establish_session(request, db, acc)
    return RedirectResponse("/", status_code=303)


@router.post("/register")
def do_register(request: Request, email: str = Form(...), password: str = Form(...),
                display_name: str = Form(None), db: Session = Depends(get_db)):
    try:
        acc = acc_svc.register(db, email=email, password=password, display_name=display_name)
        acc_svc.verify_email(db, acc)  # demo: auto-verify (grants academic if applicable)
        db.commit()
    except ValueError as e:
        return render(request, "login.html", None, mode="register", error=str(e))
    _establish_session(request, db, acc)
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def do_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# -- account settings -------------------------------------------------------

def _settings_ctx(db: Session, p: Principal, **extra) -> dict:
    account = db.get(Account, p.account_id)
    keys = db.scalars(
        select(ApiKey).where(ApiKey.account_id == p.account_id, ApiKey.revoked.is_(False))
        .order_by(ApiKey.created_at.desc())
    ).all()
    ctx = {"account": account, "mfa_on": settings_svc.mfa_enabled(db, account), "api_keys": keys}
    ctx.update(extra)
    return ctx


@router.get("/app/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db),
                  p: Principal | None = Depends(current_principal)):
    if p is None:
        return RedirectResponse("/login", status_code=303)
    return render(request, "settings.html", p, **_settings_ctx(db, p))


@router.post("/app/settings/profile")
def settings_profile(request: Request, display_name: str = Form(None), email: str = Form(None),
                     db: Session = Depends(get_db), p: Principal | None = Depends(current_principal)):
    if p is None:
        return RedirectResponse("/login", status_code=303)
    account = db.get(Account, p.account_id)
    try:
        settings_svc.update_profile(db, account, display_name=display_name, email=email)
        db.commit()
        msg = "Profile updated."
    except ValueError as e:
        db.rollback()
        return render(request, "settings.html", p, **_settings_ctx(db, p, error=str(e)))
    return render(request, "settings.html", p, **_settings_ctx(db, p, ok=msg))


@router.post("/app/settings/password")
def settings_password(request: Request, current_password: str = Form(...), new_password: str = Form(...),
                      db: Session = Depends(get_db), p: Principal | None = Depends(current_principal)):
    if p is None:
        return RedirectResponse("/login", status_code=303)
    account = db.get(Account, p.account_id)
    try:
        settings_svc.change_password(db, account, current=current_password, new=new_password)
        db.commit()
    except ValueError as e:
        db.rollback()
        return render(request, "settings.html", p, **_settings_ctx(db, p, error=str(e)))
    return render(request, "settings.html", p, **_settings_ctx(db, p, ok="Password changed."))


@router.get("/app/settings/mfa/setup", response_class=HTMLResponse)
def mfa_setup(request: Request, db: Session = Depends(get_db),
              p: Principal | None = Depends(current_principal)):
    if p is None:
        return RedirectResponse("/login", status_code=303)
    account = db.get(Account, p.account_id)
    secret = settings_svc.new_secret()
    request.session["mfa_setup_secret"] = secret
    uri = settings_svc.provisioning_uri(secret, account.email)
    return render(request, "mfa_setup.html", p, secret=secret, qr_svg=settings_svc.qr_svg(uri))


@router.post("/app/settings/mfa/enable")
def mfa_enable(request: Request, code: str = Form(...), db: Session = Depends(get_db),
               p: Principal | None = Depends(current_principal)):
    if p is None:
        return RedirectResponse("/login", status_code=303)
    account = db.get(Account, p.account_id)
    secret = request.session.get("mfa_setup_secret")
    if not secret or not settings_svc.verify_code(secret, code):
        uri = settings_svc.provisioning_uri(secret, account.email) if secret else ""
        return render(request, "mfa_setup.html", p, secret=secret or "",
                      qr_svg=settings_svc.qr_svg(uri) if secret else "",
                      error="That code didn't match — try again.")
    settings_svc.enable_mfa(db, account, secret)
    db.commit()
    request.session.pop("mfa_setup_secret", None)
    return render(request, "settings.html", p, **_settings_ctx(db, p, ok="Two-factor authentication enabled."))


@router.post("/app/settings/mfa/disable")
def mfa_disable(request: Request, password: str = Form(...), db: Session = Depends(get_db),
                p: Principal | None = Depends(current_principal)):
    if p is None:
        return RedirectResponse("/login", status_code=303)
    account = db.get(Account, p.account_id)
    from ..security import verify_password

    if not verify_password(password, account.password_hash):
        return render(request, "settings.html", p, **_settings_ctx(db, p, error="Password incorrect."))
    settings_svc.disable_mfa(db, account)
    db.commit()
    return render(request, "settings.html", p, **_settings_ctx(db, p, ok="Two-factor authentication disabled."))


@router.post("/app/settings/api-keys")
def settings_create_key(request: Request, name: str = Form("default"), db: Session = Depends(get_db),
                        p: Principal | None = Depends(current_principal)):
    if p is None:
        return RedirectResponse("/login", status_code=303)
    full, prefix, key_hash = generate_api_key()
    db.add(ApiKey(org_id=p.org_id, account_id=p.account_id, name=name or "default",
                  prefix=prefix, key_hash=key_hash))
    db.commit()
    return render(request, "settings.html", p, **_settings_ctx(db, p, new_api_key=full))


@router.post("/app/settings/api-keys/{key_id}/revoke")
def settings_revoke_key(key_id: str, request: Request, db: Session = Depends(get_db),
                        p: Principal | None = Depends(current_principal)):
    if p is None:
        return RedirectResponse("/login", status_code=303)
    key = db.get(ApiKey, key_id)
    if key and key.account_id == p.account_id:
        key.revoked = True
        db.commit()
    return render(request, "settings.html", p, **_settings_ctx(db, p, ok="API key revoked."))


# -- records ----------------------------------------------------------------

@router.get("/app/records", response_class=HTMLResponse)
def records(request: Request, db: Session = Depends(get_db),
            p: Principal | None = Depends(current_principal)):
    if p is None:
        return RedirectResponse("/login", status_code=303)
    q = select(Run).order_by(Run.created_at.desc()).limit(200)
    if p.workspace_id:
        q = q.where(Run.workspace_id == p.workspace_id)
    runs = db.scalars(q).all()
    cards = {c.run_id: c for c in db.scalars(
        select(ResultCard).where(ResultCard.workspace_id == p.workspace_id))}
    return render(request, "records.html", p, runs=runs, cards=cards)


def _owned_run(db: Session, run_id: str, p: Principal | None) -> "Run":
    """Fetch a run only if the caller owns it (or is superadmin). 404 otherwise.

    Runs are private provenance — public sharing happens via Result Cards. This
    guards both direct viewing and mutations (reproduce/publish) against IDOR.
    """
    if p is None:
        raise HTTPException(401)
    run = db.get(Run, run_id)
    if run is None or (not p.is_superadmin and run.workspace_id != p.workspace_id):
        raise HTTPException(404)
    return run


@router.get("/app/records/{run_id}", response_class=HTMLResponse)
def record_detail(run_id: str, request: Request, db: Session = Depends(get_db),
                  p: Principal | None = Depends(current_principal)):
    if p is None:
        return RedirectResponse("/login", status_code=303)
    run = _owned_run(db, run_id, p)
    from quantumledger_core.provenance import build_run_doc

    doc = build_run_doc(run)
    ev = db.scalar(select(ReproductionEvent).where(ReproductionEvent.original_run_id == run_id)
                   .order_by(ReproductionEvent.created_at.desc()))
    report = None
    if ev is not None:
        report = build_report(run, db.get(Run, ev.reproduced_run_id), ev)
    card = db.scalar(select(ResultCard).where(ResultCard.run_id == run_id))
    from ..services.benchmark import entry_for

    benchmark = entry_for(db, run_id)
    return render(request, "record_detail.html", p, run=run, doc=doc, report=report, card=card,
                  benchmark=benchmark, can_benchmark=p.has("compare_vs_fleet"))


@router.post("/app/records/{run_id}/benchmark")
def web_benchmark(run_id: str, request: Request, db: Session = Depends(get_db),
                  p: Principal | None = Depends(current_principal)):
    if p is None:
        return RedirectResponse("/login", status_code=303)
    if not p.has("compare_vs_fleet"):
        raise HTTPException(402, "fleet comparison requires an account")
    run = _owned_run(db, run_id, p)
    from ..services.benchmark import benchmark_run

    benchmark_run(db, run)
    db.commit()
    return RedirectResponse(f"/app/records/{run_id}", status_code=303)


@router.post("/app/records/{run_id}/reproduce")
def web_reproduce(run_id: str, request: Request, days: float = Form(30.0), profile: str = Form("typical"),
                  db: Session = Depends(get_db), p: Principal | None = Depends(current_principal)):
    if p is None:
        return RedirectResponse("/login", status_code=303)
    run = _owned_run(db, run_id, p)
    ws = db.get(Workspace, run.workspace_id)
    runner.reproduce_run(db, run, workspace=ws, days=days, profile=profile, account_id=p.account_id)
    db.commit()
    return RedirectResponse(f"/app/records/{run_id}", status_code=303)


@router.post("/app/records/{run_id}/publish")
def web_publish(run_id: str, request: Request, db: Session = Depends(get_db),
                p: Principal | None = Depends(current_principal)):
    if p is None:
        return RedirectResponse("/login", status_code=303)
    run = _owned_run(db, run_id, p)
    card = cards_svc.get_or_create_card(db, run)
    cards_svc.publish_card(db, card)
    acc_svc.audit(db, workspace_id=run.workspace_id, account_id=p.account_id, action="card.publish",
                  resource_type="card", resource_id=card.id)
    db.commit()
    return RedirectResponse(f"/cards/{card.slug}", status_code=303)


# -- public result card -----------------------------------------------------

@router.get("/cards/{slug}", response_class=HTMLResponse)
def public_card(slug: str, request: Request, db: Session = Depends(get_db),
                p: Principal | None = Depends(current_principal)):
    card = db.scalar(select(ResultCard).where(ResultCard.slug == slug))
    if card is None or card.visibility != "public":
        raise HTTPException(404, "card not found")
    run = db.get(Run, card.run_id)
    embed = cards_svc.embed_snippets(card, get_settings().base_url)
    return render(request, "card.html", p, card=card, run=run, embed=embed)


# -- leaderboard ------------------------------------------------------------

@router.get("/leaderboard", response_class=HTMLResponse)
def leaderboard(request: Request, metric: str = "median_2q_error", db: Session = Depends(get_db),
                p: Principal | None = Depends(current_principal)):
    entries = []
    metrics = []
    metric_label = metric
    try:
        from quantumledger_crawler.corpus import LEADERBOARD_METRICS, fleet_leaderboard

        metrics = LEADERBOARD_METRICS
        metric_label = next((m["label"] for m in metrics if m["key"] == metric), metric)
        entries = fleet_leaderboard(db, metric=metric)
    except Exception:
        entries = []
    return render(request, "leaderboard.html", p, entries=entries, metric=metric,
                  metrics=metrics, metric_label=metric_label)


# -- docs -------------------------------------------------------------------

@router.get("/docs", response_class=HTMLResponse)
def docs_index(request: Request, p: Principal | None = Depends(current_principal)):
    return render(request, "docs.html", p, home=docs_mod.home_context(),
                  manifest=docs_mod.DOCS_MANIFEST, active_slug=None)


@router.get("/docs/{slug}", response_class=HTMLResponse)
def docs_page(slug: str, request: Request, db: Session = Depends(get_db),
              p: Principal | None = Depends(current_principal)):
    doc = docs_mod.page_html(slug, request, db)
    if doc is None:
        raise HTTPException(404, "unknown docs page")
    return render(request, "docs.html", p, doc=doc,
                  manifest=docs_mod.DOCS_MANIFEST, active_slug=slug)


# -- compliance console -----------------------------------------------------

@router.get("/app/compliance", response_class=HTMLResponse)
def compliance_console(request: Request, evaluated: str | None = None,
                       db: Session = Depends(get_db),
                       p: Principal | None = Depends(current_principal)):
    if p is None:
        return RedirectResponse("/login", status_code=303)
    frameworks = db.scalars(select(ComplianceFramework)).all()
    enabled = {wf.framework_id: wf for wf in db.scalars(
        select(WorkspaceFramework).where(WorkspaceFramework.workspace_id == p.workspace_id))}
    atts = db.scalars(select(Attestation).where(Attestation.workspace_id == p.workspace_id)).all()

    # Plan limits: Free may only enable FAIR, and is capped by frameworks_allowed.
    fw_cap = quota_for(p.plan, "frameworks_allowed")
    enabled_count = len(enabled)
    under_cap = is_unlimited(fw_cap) or enabled_count < fw_cap

    # Per-framework rollup: how many of its controls are currently passing.
    cards = []
    for fw in frameworks:
        total = db.scalar(select(func.count(Control.id)).where(Control.framework_id == fw.id)) or 0
        wf = enabled.get(fw.id)
        detail = (wf.status_detail if wf else None) or {}
        passing = sum(1 for d in detail.values() if d.get("status") == "pass") if detail else None
        plan_allows = (p.plan != "free") or fw.key.startswith("fair")
        lock = None if (plan_allows and under_cap) else ("plan" if not plan_allows else "cap")
        cards.append({"fw": fw, "wf": wf, "total_controls": total, "passing": passing,
                      "enableable": lock is None, "lock": lock})

    # Post-evaluation summary banner (recomputed from the freshly-stored status).
    summary = None
    if evaluated is not None:
        details = [(wf.status_detail or {}) for wf in enabled.values()]
        n_controls = sum(len(d) for d in details)
        n_gaps = sum(1 for d in details for e in d.values() if e.get("status") != "pass")
        summary = {"frameworks": len(enabled), "controls": n_controls, "gaps": n_gaps}

    return render(request, "compliance.html", p, cards=cards, enabled=enabled,
                  attestations=atts, has_compliance=p.has("compliance_frameworks"),
                  can_attest=p.has("attestation_signing"), plan_display=PLAN_DISPLAY,
                  summary=summary)


@router.post("/app/compliance/enable")
def web_enable(request: Request, framework_id: str = Form(...), db: Session = Depends(get_db),
               p: Principal | None = Depends(current_principal)):
    if p is None or not p.has("compliance_frameworks"):
        raise HTTPException(402, "compliance requires a paid plan")
    ws = db.get(Workspace, p.workspace_id)
    fw = db.get(ComplianceFramework, framework_id)
    if fw is None:
        raise HTTPException(404, "framework not found")
    # Plan limits: Free may enable only FAIR, and every plan is capped by
    # frameworks_allowed. Enabling an already-enabled framework is idempotent.
    already = db.scalar(select(WorkspaceFramework).where(
        WorkspaceFramework.workspace_id == ws.id, WorkspaceFramework.framework_id == fw.id))
    if already is None:
        if p.plan == "free" and not fw.key.startswith("fair"):
            raise HTTPException(402, "Free includes FAIR only — upgrade to add more frameworks")
        cap = quota_for(p.plan, "frameworks_allowed")
        if not is_unlimited(cap) and limits_svc.frameworks_enabled_count(db, ws.id) >= cap:
            raise HTTPException(402, f"framework limit reached ({cap}) on your plan")
    comp.enable_framework(db, ws, fw)
    comp.evaluate_framework(db, ws, fw)
    db.commit()
    return RedirectResponse("/app/compliance", status_code=303)


@router.post("/app/compliance/evaluate")
def web_evaluate(request: Request, db: Session = Depends(get_db),
                 p: Principal | None = Depends(current_principal)):
    if p is None or not p.has("compliance_frameworks"):
        raise HTTPException(402, "compliance requires Pro or above")
    ws = db.get(Workspace, p.workspace_id)
    comp.evaluate_all(db, ws)
    db.commit()
    return RedirectResponse("/app/compliance?evaluated=1", status_code=303)


@router.get("/app/compliance/frameworks/{fw_id}", response_class=HTMLResponse)
def compliance_framework_detail(fw_id: str, request: Request, db: Session = Depends(get_db),
                                p: Principal | None = Depends(current_principal)):
    if p is None:
        return RedirectResponse("/login", status_code=303)
    fw = db.get(ComplianceFramework, fw_id)
    if fw is None:
        raise HTTPException(404)
    wf = db.scalar(select(WorkspaceFramework).where(
        WorkspaceFramework.workspace_id == p.workspace_id,
        WorkspaceFramework.framework_id == fw.id))
    detail = (wf.status_detail if wf else None) or {}
    controls = db.scalars(
        select(Control).where(Control.framework_id == fw.id).order_by(Control.key)).all()
    # Evidence collected for these controls, grouped by control id.
    ev_by_control: dict[str, list] = {}
    if wf:
        ctrl_ids = [c.id for c in controls]
        if ctrl_ids:
            for ev in db.scalars(select(EvidenceItem).where(
                    EvidenceItem.workspace_id == p.workspace_id,
                    EvidenceItem.control_id.in_(ctrl_ids))):
                ev_by_control.setdefault(ev.control_id, []).append(ev)

    # Build a fully-explained view row per control.
    control_views = []
    for c in controls:
        cd = detail.get(c.key) or {}
        status = cd.get("status") if wf else "unknown"
        failing = set(cd.get("failing_rule_ids") or [])
        rules = comp.rules_view(c)
        for r in rules:
            r["failing"] = r["id"] in failing
        control_views.append({
            "control": c,
            "status": status or "unknown",
            "rules": rules,
            "evidence": ev_by_control.get(c.id, []),
            "evidence_count": cd.get("evidence_count", 0),
        })

    n_pass = sum(1 for v in control_views if v["status"] == "pass")
    return render(request, "compliance_framework.html", p, fw=fw, wf=wf,
                  control_views=control_views, n_pass=n_pass, n_total=len(control_views),
                  has_compliance=p.has("compliance_frameworks"),
                  can_attest=p.has("attestation_signing"))


@router.post("/app/compliance/evaluate/{fw_id}")
def web_evaluate_one(fw_id: str, request: Request, db: Session = Depends(get_db),
                     p: Principal | None = Depends(current_principal)):
    if p is None or not p.has("compliance_frameworks"):
        raise HTTPException(402, "compliance requires Pro or above")
    ws = db.get(Workspace, p.workspace_id)
    fw = db.get(ComplianceFramework, fw_id)
    if fw is None:
        raise HTTPException(404)
    comp.evaluate_framework(db, ws, fw)
    db.commit()
    return RedirectResponse(f"/app/compliance/frameworks/{fw_id}", status_code=303)


@router.post("/app/compliance/attest")
def web_attest(request: Request, framework_id: str = Form(...), db: Session = Depends(get_db),
               p: Principal | None = Depends(current_principal)):
    if p is None or not p.has("attestation_signing"):
        raise HTTPException(402, "attestation requires Pro or above")
    ws = db.get(Workspace, p.workspace_id)
    fw = db.get(ComplianceFramework, framework_id)
    control_ids = [c.id for c in db.scalars(select(Control).where(Control.framework_id == fw.id))]
    items = db.scalars(select(EvidenceItem).where(EvidenceItem.workspace_id == ws.id,
                                                  EvidenceItem.control_id.in_(control_ids))).all()
    if items:
        priv, kid, _ = attestation_key()
        create_attestation(db, workspace=ws, framework=fw, subject_type="workspace",
                           subject_id=ws.id, evidence_items=items, private_key=priv, kid=kid,
                           issuer_org=ws.org_id)
        db.commit()
    return RedirectResponse("/app/compliance", status_code=303)


# -- attestation verification (public, human-readable) ----------------------

@router.get("/verify/{att_id}", response_class=HTMLResponse)
def verify_attestation_page(att_id: str, request: Request, db: Session = Depends(get_db),
                            p: Principal | None = Depends(current_principal)):
    """Human-readable attestation verification (the raw JSON lives at the API)."""
    from ..api.v1.public import live_hashes_for
    from ..services.attestation import verify_attestation

    att = db.get(Attestation, att_id)
    if att is None:
        raise HTTPException(404, "attestation not found")
    _priv, _kid, jwks_doc = attestation_key()
    entries = (att.satisfied_state or {}).get("evidence_entries", [])
    result = verify_attestation(db, att, jwks_doc, live_content_hashes=live_hashes_for(db, entries))
    fw = db.get(ComplianceFramework, att.framework_id) if att.framework_id else None
    return render(request, "verify.html", p, att=att, fw=fw, result=result,
                  evidence_count=len(entries))


# -- trust center -----------------------------------------------------------

@router.get("/trust/{org_slug}", response_class=HTMLResponse)
def trust_center(org_slug: str, request: Request, db: Session = Depends(get_db),
                 p: Principal | None = Depends(current_principal)):
    org = db.scalar(select(Org).where(Org.slug == org_slug))
    if org is None:
        raise HTTPException(404)
    ws_ids = [w.id for w in db.scalars(select(Workspace).where(Workspace.org_id == org.id))]
    frameworks = []
    attestations = []
    if ws_ids:
        for wf in db.scalars(select(WorkspaceFramework).where(WorkspaceFramework.workspace_id.in_(ws_ids))):
            fw = db.get(ComplianceFramework, wf.framework_id)
            frameworks.append({"name": fw.name if fw else "?", "status": wf.status})
        attestations = db.scalars(select(Attestation).where(
            Attestation.workspace_id.in_(ws_ids), Attestation.revoked.is_(False))).all()
    return render(request, "trust.html", p, org=org, frameworks=frameworks, attestations=attestations)


# -- admin ------------------------------------------------------------------

@router.get("/app/admin", response_class=HTMLResponse)
def admin_page(request: Request, db: Session = Depends(get_db),
               p: Principal | None = Depends(current_principal)):
    if p is None or not p.is_superadmin:
        raise HTTPException(403, "superadmin only")
    orgs = db.scalars(select(Org)).all()
    from ..entitlements import effective_plan

    rows = [{"org": o, "effective": effective_plan(db, o)} for o in orgs]
    return render(request, "admin.html", p, rows=rows)


@router.post("/app/admin/upgrade")
def web_upgrade(request: Request, org_id: str = Form(...), plan: str = Form(...),
                db: Session = Depends(get_db), p: Principal | None = Depends(current_principal)):
    if p is None or not p.is_superadmin:
        raise HTTPException(403, "superadmin only")
    org = db.get(Org, org_id)
    acc_svc.grant_plan(db, org, plan, source="admin_override", granted_by=p.account_id)
    db.commit()
    return RedirectResponse("/app/admin", status_code=303)
