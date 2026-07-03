"""Reproducibility report builder (structured dict + self-contained HTML)."""

from __future__ import annotations

from ..models import ReproductionEvent, Run
from ..provenance import build_run_doc


def build_report(original: Run, reproduced: Run, event: ReproductionEvent) -> dict:
    """A self-contained, exportable reproducibility report."""
    return {
        "kind": "reproducibility_report",
        "generated_from": {
            "original_run_id": original.id,
            "reproduced_run_id": reproduced.id,
            "original_run_hash": original.run_hash,
            "reproduced_run_hash": reproduced.run_hash,
        },
        "verdict": event.verdict,
        "scores": event.diff.get("scores") if event.diff else None,
        "calibration_drift": event.calibration_drift,
        "transpilation_delta": event.transpilation_delta,
        "backend_substitution": event.diff.get("backend_substitution") if event.diff else None,
        "distributions": event.diff.get("distributions") if event.diff else None,
        "provenance": {
            "original": build_run_doc(original),
            "reproduced": build_run_doc(reproduced),
        },
    }


_VERDICT_COLOR = {
    "reproducible": "#4c1",
    "drifted": "#dfb317",
    "divergent": "#fe7d37",
    "irreproducible": "#e05d44",
}


def report_to_html(report: dict) -> str:
    scores = report.get("scores") or {}
    verdict = report.get("verdict", "unknown")
    color = _VERDICT_COLOR.get(verdict, "#9f9f9f")
    drift_rows = "".join(
        f"<tr><td>{d.get('qubit', d.get('gate',''))}</td><td>{d['param']}</td>"
        f"<td>{d['from']}</td><td>{d['to']}</td><td>{d.get('pct')}%</td></tr>"
        for d in (report.get("calibration_drift") or [])
    )
    shifts = (report.get("distributions") or {}).get("top_shifts") or []
    shift_rows = "".join(
        f"<tr><td><code>{s['bitstring']}</code></td><td>{s['delta']:+.4f}</td></tr>" for s in shifts
    )
    gf = report["generated_from"]
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Reproducibility report</title>
<style>body{{font-family:system-ui,sans-serif;max-width:820px;margin:2rem auto;color:#1f2933}}
table{{border-collapse:collapse;width:100%;margin:1rem 0}}td,th{{border:1px solid #e0e0e0;padding:.4rem .6rem;text-align:left}}
.verdict{{display:inline-block;padding:.3rem .8rem;border-radius:.4rem;color:#fff;background:{color};font-weight:600}}
code{{background:#f3f4f6;padding:.1rem .3rem;border-radius:.2rem}}</style></head><body>
<h1>Reproducibility report</h1>
<p class="verdict">{verdict.upper()}</p>
<h2>Scores</h2>
<ul>
<li>Hellinger fidelity: <b>{scores.get('hellinger_fidelity'):.4f}</b></li>
<li>Total variation distance: {scores.get('tvd'):.4f}</li>
<li>Within shot-noise: {scores.get('within_shot_noise')}</li>
</ul>
<h2>Calibration drift</h2>
<table><tr><th>qubit/gate</th><th>param</th><th>from</th><th>to</th><th>Δ%</th></tr>{drift_rows}</table>
<h2>Top distribution shifts</h2>
<table><tr><th>bitstring</th><th>Δ probability</th></tr>{shift_rows}</table>
<h2>Provenance</h2>
<p>Original run hash: <code>{gf['original_run_hash']}</code></p>
<p>Reproduced run hash: <code>{gf['reproduced_run_hash']}</code></p>
<p>Both records are Merkle-bound and independently verifiable offline.</p>
</body></html>"""
