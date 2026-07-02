"""End-to-end assertions across every moving part of QuantumLedger.

Runs after the server is up (see run.sh). Exercises, with real data:
  A. offline SDK/CLI        (ql init/demo/list/show/reproduce/connectors/config)
  B. server health
  C. auth + API keys        (login, /me, mint key)
  D. CLI connectivity       (ql login --verify, ql doctor)
  E. push local runs        (ql push -> server), and read them back over the API
  F. reproduce + cards      (reproduce, publish, public card, citation, badge)
  G. compliance             (frameworks, enable, evaluate, status, gaps, attest, verify, revoke)
  H. corpus / leaderboard
  I. web UI smoke           (dashboard, docs, pricing, app pages, run detail)
  J. multi-tenant security  (anon/cross-tenant denied, ?workspace_id= rejected, free gating)
  K. interop error clarity  (ql doctor at a dead endpoint; ql login with a bad token)

Exit code is non-zero if any check fails.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import httpx

ENDPOINT = os.environ["QL_E2E_ENDPOINT"].rstrip("/")
ADMIN_EMAIL = os.environ.get("QL_E2E_ADMIN_EMAIL", "e2e-admin@quantumledger.local")
ADMIN_PW = os.environ.get("QL_E2E_ADMIN_PASSWORD", "e2e-pass-123456")
QL_HOME = os.environ["QL_HOME"]


class Report:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.fails: list[str] = []

    def section(self, title: str) -> None:
        print(f"\n\033[1m== {title} ==\033[0m")

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        if ok:
            self.passed += 1
            print(f"  \033[32mPASS\033[0m  {name}")
        else:
            self.failed += 1
            self.fails.append(name + (f" — {detail}" if detail else ""))
            print(f"  \033[31mFAIL\033[0m  {name}" + (f"  ({detail})" if detail else ""))
        return ok

    def summary(self) -> int:
        total = self.passed + self.failed
        print(f"\n\033[1m{self.passed}/{total} checks passed.\033[0m")
        if self.fails:
            print("\033[31mFailures:\033[0m")
            for f in self.fails:
                print(f"  - {f}")
            return 1
        return 0


R = Report()


def ql(*args: str, home: str | None = None, timeout: int = 180) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["QL_HOME"] = home or QL_HOME
    return subprocess.run(
        [sys.executable, "-m", "quantumledger.cli.__main__", *args],
        env=env, capture_output=True, text=True, timeout=timeout,
    )


# --------------------------------------------------------------------------- #
# A. offline SDK / CLI
# --------------------------------------------------------------------------- #
def phase_offline_cli() -> None:
    R.section("A. Offline SDK / CLI")
    r = ql("init")
    R.check("ql init", r.returncode == 0 and "Initialized" in r.stdout, r.stderr.strip()[:120])

    r = ql("demo", "--shots", "1024", "--qubits", "3")
    R.check("ql demo (capture)", r.returncode == 0 and "Recorded" in r.stdout, r.stderr.strip()[:200])

    r = ql("list", "--json")
    rows = []
    try:
        rows = json.loads(r.stdout)
    except Exception:
        pass
    R.check("ql list --json returns a run", r.returncode == 0 and len(rows) >= 1)
    run_id = rows[0]["id"] if rows else None

    if run_id:
        r = ql("show", run_id, "--raw")
        ok = r.returncode == 0 and '"run_hash"' in r.stdout
        R.check("ql show --raw has provenance", ok, r.stderr.strip()[:120])

        r = ql("reproduce", run_id, "--days", "90", "--profile", "bad_day")
        ok = r.returncode == 0 and "verdict" in r.stdout and "Hellinger" in r.stdout
        R.check("ql reproduce (drift+score offline)", ok, r.stderr.strip()[:200])

    r = ql("connectors")
    R.check("ql connectors lists simulator", r.returncode == 0 and "simulator" in r.stdout)

    r = ql("config", "show")
    R.check("ql config show", r.returncode == 0 and "sync_endpoint" in r.stdout)


# --------------------------------------------------------------------------- #
# B/C. health, auth, API key
# --------------------------------------------------------------------------- #
def phase_auth(admin: httpx.Client) -> dict:
    R.section("B/C. Health, auth, API key")
    h = httpx.get(f"{ENDPOINT}/api/v1/health", timeout=15)
    R.check("GET /api/v1/health", h.status_code == 200 and h.json().get("service") == "quantumledger",
            f"status={h.status_code}")

    lr = admin.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PW})
    R.check("admin login", lr.status_code == 200, f"status={lr.status_code} {lr.text[:120]}")

    me = admin.get("/api/v1/me").json()
    R.check("/me authenticated + enterprise plan", me.get("authenticated") and me.get("plan") == "enterprise",
            f"plan={me.get('plan')}")
    R.check("/me has compliance features",
            "compliance_frameworks" in me.get("features", []) and "attestation_signing" in me.get("features", []))
    org_id = me.get("org_id")
    ws_id = me.get("workspace_id")

    kr = admin.post(f"/api/v1/orgs/{org_id}/api-keys", params={"name": "e2e"})
    token = kr.json().get("api_key") if kr.status_code == 200 else None
    R.check("mint API key", bool(token) and token.startswith("ql_live_"), f"status={kr.status_code}")

    # prove the key authenticates
    if token:
        who = httpx.get(f"{ENDPOINT}/api/v1/me", headers={"Authorization": f"Bearer {token}"}, timeout=15).json()
        R.check("API key authenticates (Bearer /me)", who.get("authenticated") is True)

    return {"org_id": org_id, "ws_id": ws_id, "token": token}


# --------------------------------------------------------------------------- #
# D/E. CLI connectivity + push
# --------------------------------------------------------------------------- #
def phase_push(ctx: dict) -> str | None:
    R.section("D/E. CLI connectivity + push")
    token = ctx["token"]
    if not token:
        R.check("push phase reachable (needs API key)", False, "no API key from auth phase")
        return None
    r = ql("login", "--token", token, "--endpoint", ENDPOINT)
    R.check("ql login verifies token", r.returncode == 0 and "verified" in r.stdout, r.stdout.strip()[-200:])

    r = ql("doctor")
    R.check("ql doctor (reachable + token valid)",
            r.returncode == 0 and "server reachable" in r.stdout and "token valid" in r.stdout,
            r.stdout.strip()[-200:])

    r = ql("push")
    R.check("ql push (no abort, exit 0)", r.returncode == 0 and "Push failed" not in r.stdout,
            r.stdout.strip()[-200:])

    # read pushed runs back over the API (Bearer)
    runs = httpx.get(f"{ENDPOINT}/api/v1/runs", headers={"Authorization": f"Bearer {token}"}, timeout=15).json()
    R.check("GET /api/v1/runs returns pushed data", isinstance(runs, list) and len(runs) >= 1,
            f"n={len(runs) if isinstance(runs, list) else '?'}")
    # pick an unpublished-friendly run id for later
    return runs[0]["id"] if runs else None


# --------------------------------------------------------------------------- #
# offline hash verification of server-exported provenance
# --------------------------------------------------------------------------- #
def phase_provenance(admin: httpx.Client, run_id: str) -> None:
    R.section("Provenance: offline hash verification")
    doc = admin.get(f"/api/v1/runs/{run_id}").json()
    R.check("run doc is qlprov/run", isinstance(doc, dict) and "run_hash" in doc)
    try:
        import quantumledger_core as qc

        verifier = getattr(qc, "verify_run_hash", None)
        if verifier is None:
            R.check("verify_run_hash available", False, "not exported")
        else:
            R.check("run_hash recomputes offline (tamper-evident)", bool(verifier(doc)))
    except Exception as e:  # noqa: BLE001
        R.check("run_hash recomputes offline (tamper-evident)", False, str(e)[:120])


# --------------------------------------------------------------------------- #
# F. reproduce + cards
# --------------------------------------------------------------------------- #
def phase_cards(admin: httpx.Client, run_id: str) -> None:
    R.section("F. Reproduce + Result Cards")
    rr = admin.post(f"/api/v1/runs/{run_id}/reproduce", params={"days": 90, "profile": "bad_day"})
    body = rr.json() if rr.status_code == 200 else {}
    R.check("reproduce via API", rr.status_code == 200 and "verdict" in body and "reproducibility_score" in body,
            f"status={rr.status_code}")

    pub = admin.post(f"/api/v1/runs/{run_id}/card/publish")
    slug = pub.json().get("slug") if pub.status_code == 200 else None
    R.check("publish result card", bool(slug), f"status={pub.status_code} {pub.text[:120]}")

    if slug:
        c = httpx.get(f"{ENDPOINT}/api/v1/cards/{slug}", timeout=15)
        R.check("public card JSON (no auth)", c.status_code == 200)
        cite = httpx.get(f"{ENDPOINT}/api/v1/cards/{slug}/citation", params={"format": "bibtex"}, timeout=15)
        R.check("card citation (BibTeX)", cite.status_code == 200 and "@" in cite.text)
        badge = httpx.get(f"{ENDPOINT}/badge/{slug}/recorded.svg", timeout=15)
        R.check("badge SVG", badge.status_code == 200 and "<svg" in badge.text.lower())


# --------------------------------------------------------------------------- #
# G. compliance + attestations
# --------------------------------------------------------------------------- #
def phase_compliance(admin: httpx.Client, ws_id: str) -> None:
    R.section("G. Compliance + attestations")
    fws = httpx.get(f"{ENDPOINT}/api/v1/frameworks", timeout=15).json()
    R.check("list frameworks", isinstance(fws, list) and len(fws) >= 1)
    fair = next((f for f in fws if str(f.get("key", "")).startswith("fair")), None)
    if not fair:
        R.check("FAIR framework present", False)
        return
    fw_id = fair["id"]

    en = admin.post(f"/api/v1/workspaces/{ws_id}/frameworks/{fw_id}/enable")
    R.check("enable framework", en.status_code == 200, f"status={en.status_code}")

    ev = admin.post(f"/api/v1/workspaces/{ws_id}/compliance/evaluate")
    R.check("evaluate frameworks", ev.status_code == 200 and "results" in ev.json(), f"status={ev.status_code}")

    st = admin.get(f"/api/v1/workspaces/{ws_id}/compliance")
    frameworks = st.json().get("frameworks", []) if st.status_code == 200 else []
    fair_status = next((f["status"] for f in frameworks if f["framework_id"] == fw_id), None)
    R.check("compliance status readable", st.status_code == 200 and fair_status is not None,
            f"fair_status={fair_status}")
    R.check("FAIR passes on seeded data", fair_status == "pass", f"status={fair_status}")

    gaps = admin.get(f"/api/v1/workspaces/{ws_id}/gaps")
    R.check("gaps endpoint", gaps.status_code == 200 and isinstance(gaps.json(), list))

    att = admin.post(f"/api/v1/workspaces/{ws_id}/attestations", params={"framework_id": fw_id})
    att_body = att.json() if att.status_code == 200 else {}
    att_id = att_body.get("attestation_id")
    R.check("issue signed attestation", bool(att_id), f"status={att.status_code} {att.text[:140]}")

    if att_id:
        v = httpx.get(f"{ENDPOINT}/api/v1/attestations/{att_id}/verify", timeout=15).json()
        R.check("attestation verifies (public)", v.get("valid") is True, json.dumps(v)[:140])
        rv = admin.post(f"/api/v1/attestations/{att_id}/revoke")
        R.check("revoke attestation", rv.status_code == 200)
        v2 = httpx.get(f"{ENDPOINT}/api/v1/attestations/{att_id}/verify", timeout=15).json()
        R.check("revoked attestation no longer valid", v2.get("valid") is not True or v2.get("revoked") is True)


# --------------------------------------------------------------------------- #
# H. corpus / leaderboard
# --------------------------------------------------------------------------- #
def phase_corpus() -> None:
    R.section("H. Corpus / leaderboard")
    lb = httpx.get(f"{ENDPOINT}/api/v1/leaderboard", params={"metric": "median_2q_error"}, timeout=15)
    body = lb.json() if lb.status_code == 200 else {}
    entries = body.get("entries") if isinstance(body, dict) else None
    note = body.get("note") if isinstance(body, dict) else None
    R.check("leaderboard endpoint", lb.status_code == 200 and isinstance(entries, list),
            f"note={note}")
    R.check("leaderboard populated from corpus", isinstance(entries, list) and len(entries) >= 1,
            f"n={len(entries) if isinstance(entries, list) else '?'} note={note}")


# --------------------------------------------------------------------------- #
# I. web UI smoke
# --------------------------------------------------------------------------- #
def phase_web(admin: httpx.Client, run_id: str) -> None:
    R.section("I. Web UI smoke (authenticated)")
    pages = {
        "GET / (dashboard)": ("/", "Dashboard"),
        "GET /docs (home)": ("/docs", "documentation"),
        "GET /docs/api (narrative)": ("/docs/api", "endpoint"),
        "GET /pricing (public)": ("/pricing", "pricing"),
        "GET /app/records": ("/app/records", "Records"),
        "GET /app/compliance": ("/app/compliance", "Compliance"),
        "GET /app/plans": ("/app/plans", "Plans"),
        "GET /app/start": ("/app/start", "Connect the SDK"),
    }
    for name, (path, needle) in pages.items():
        resp = admin.get(path, follow_redirects=True)
        R.check(name, resp.status_code == 200 and needle.lower() in resp.text.lower(),
                f"status={resp.status_code}")
    rd = admin.get(f"/app/records/{run_id}", follow_redirects=True)
    R.check("GET /app/records/{id} (owned)", rd.status_code == 200 and "Provenance".lower() in rd.text.lower(),
            f"status={rd.status_code}")


# --------------------------------------------------------------------------- #
# J. multi-tenant security
# --------------------------------------------------------------------------- #
def phase_security(ctx: dict, admin_run_id: str) -> None:
    R.section("J. Multi-tenant security")
    admin_ws = ctx["ws_id"]

    # anonymous cannot read a private run
    a = httpx.get(f"{ENDPOINT}/api/v1/runs/{admin_run_id}", timeout=15)
    R.check("anon GET /runs/{id} -> 401", a.status_code == 401, f"status={a.status_code}")
    a2 = httpx.get(f"{ENDPOINT}/api/v1/runs/{admin_run_id}/report", timeout=15)
    R.check("anon GET /runs/{id}/report -> 401", a2.status_code == 401, f"status={a2.status_code}")
    a3 = httpx.get(f"{ENDPOINT}/api/v1/workspaces/{admin_ws}/gaps", timeout=15)
    R.check("anon GET /workspaces/{ws}/gaps -> 401", a3.status_code == 401, f"status={a3.status_code}")

    # a second, unrelated free-tier user
    u2 = httpx.Client(base_url=ENDPOINT, timeout=20)
    reg = u2.post("/api/v1/auth/register", json={"email": "e2e-user2@example.com", "password": "user2-pass-123"})
    R.check("register second user", reg.status_code == 200, f"status={reg.status_code}")
    me2 = u2.get("/api/v1/me").json()
    R.check("second user is free tier", me2.get("plan") == "free", f"plan={me2.get('plan')}")

    r = u2.get(f"/api/v1/runs/{admin_run_id}")
    R.check("cross-tenant GET run -> 404", r.status_code == 404, f"status={r.status_code}")
    r = u2.get(f"/api/v1/workspaces/{admin_ws}/compliance")
    R.check("cross-tenant compliance -> 404", r.status_code == 404, f"status={r.status_code}")
    r = u2.post(f"/api/v1/runs/{admin_run_id}/card/publish")
    R.check("cross-tenant publish denied (not 200)", r.status_code != 200, f"status={r.status_code}")

    # the ?workspace_id= master-key must not leak another tenant's runs
    r = u2.get("/api/v1/runs", params={"workspace_id": admin_ws})
    rows = r.json() if r.status_code == 200 else None
    R.check("?workspace_id=<victim> leaks nothing", isinstance(rows, list) and len(rows) == 0,
            f"n={len(rows) if isinstance(rows, list) else '?'}")

    # free tier is gated out of compliance on its OWN workspace
    r = u2.post(f"/api/v1/workspaces/{me2.get('workspace_id')}/compliance/evaluate")
    R.check("free tier compliance gated (402)", r.status_code == 402, f"status={r.status_code}")
    u2.close()


# --------------------------------------------------------------------------- #
# K. interop error clarity
# --------------------------------------------------------------------------- #
def phase_interop_errors() -> None:
    R.section("K. Interop error clarity")
    neg_home = tempfile.mkdtemp(prefix="ql-e2e-neg-")
    ql("init", home=neg_home)

    # dead endpoint -> clear, actionable failure + non-zero exit
    ql("config", "set", "sync_endpoint", "http://127.0.0.1:1", home=neg_home)
    r = ql("doctor", home=neg_home)
    text = (r.stdout + r.stderr).lower()
    R.check("ql doctor at dead endpoint fails clearly",
            r.returncode != 0 and ("reach" in text or "refused" in text or "connection" in text),
            r.stdout.strip()[-160:])

    # bad token against the live server -> verification is reported as failed
    r = ql("login", "--token", "ql_live_bogus", "--endpoint", ENDPOINT, home=neg_home)
    text = (r.stdout + r.stderr).lower()
    R.check("ql login reports bad token",
            "not accepted" in text or "verification failed" in text or "invalid" in text,
            r.stdout.strip()[-160:])


def main() -> int:
    admin = httpx.Client(base_url=ENDPOINT, timeout=30)
    try:
        phase_offline_cli()
        ctx = phase_auth(admin)
        pushed_run = phase_push(ctx)
        run_id = pushed_run
        if run_id:
            phase_provenance(admin, run_id)
            phase_cards(admin, run_id)
        phase_compliance(admin, ctx["ws_id"])
        phase_corpus()
        if run_id:
            phase_web(admin, run_id)
            phase_security(ctx, run_id)
        phase_interop_errors()
    finally:
        admin.close()
    return R.summary()


if __name__ == "__main__":
    sys.exit(main())
