"""Diff two runs: calibration drift, backend substitution, transpilation delta."""

from __future__ import annotations

from . import scoring


def calibration_drift(cal_a: dict, cal_b: dict) -> list[dict]:
    """Per-parameter deltas between two calibration payloads."""
    out: list[dict] = []
    qa = {q["index"]: q for q in cal_a.get("qubits", [])}
    qb = {q["index"]: q for q in cal_b.get("qubits", [])}
    for idx in sorted(set(qa) | set(qb)):
        a = qa.get(idx, {})
        b = qb.get(idx, {})
        for param in ("T1_us", "T2_us", "readout_error"):
            va, vb = a.get(param), b.get(param)
            if va is None or vb is None or va == vb:
                continue
            pct = (vb - va) / va * 100 if va else None
            out.append({"qubit": idx, "param": param, "from": va, "to": vb,
                        "pct": round(pct, 2) if pct is not None else None})
    ga = {(g["gate"], tuple(g["qubits"])): g.get("error") for g in cal_a.get("gates", [])}
    gb = {(g["gate"], tuple(g["qubits"])): g.get("error") for g in cal_b.get("gates", [])}
    for key in sorted(set(ga) | set(gb)):
        va, vb = ga.get(key), gb.get(key)
        if va is None or vb is None or va == vb:
            continue
        pct = (vb - va) / va * 100 if va else None
        out.append({"gate": key[0], "qubits": list(key[1]), "param": "error",
                    "from": va, "to": vb, "pct": round(pct, 2) if pct is not None else None})
    return out


def transpilation_delta(metrics_a: dict, metrics_b: dict) -> dict:
    out: dict = {}
    for key in ("depth", "size", "n_cx"):
        a, b = metrics_a.get(key), metrics_b.get(key)
        if a != b:
            out[key] = {"from": a, "to": b}
    return out


def build_diff(
    *,
    dist_a: dict,
    dist_b: dict,
    counts_a: dict | None,
    counts_b: dict | None,
    cal_a: dict,
    cal_b: dict,
    metrics_a: dict,
    metrics_b: dict,
    backend_a: str,
    backend_b: str,
) -> dict:
    s = scoring.score(dist_a, dist_b, counts_a=counts_a, counts_b=counts_b)
    keys = sorted(set(dist_a) | set(dist_b))
    shifts = sorted(
        ({"bitstring": k, "delta": round(dist_b.get(k, 0.0) - dist_a.get(k, 0.0), 4)} for k in keys),
        key=lambda x: abs(x["delta"]),
        reverse=True,
    )[:8]
    return {
        "distributions": {"original": dist_a, "reproduced": dist_b, "top_shifts": shifts},
        "scores": s,
        "calibration_drift": calibration_drift(cal_a, cal_b),
        "backend_substitution": None if backend_a == backend_b else {"from": backend_a, "to": backend_b},
        "transpilation_delta": transpilation_delta(metrics_a, metrics_b),
        "verdict": s["verdict"],
    }
