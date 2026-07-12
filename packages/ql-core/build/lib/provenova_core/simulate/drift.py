"""Deterministic, seeded calibration drift — the engine behind the "aha" demo.

Given a baseline calibration payload, produce a new one that has drifted over a
number of days. Because the payload changes, its content hash changes, so it
becomes a new snapshot on the backend's time-series and re-running the same
circuit against it yields a measurably different distribution.
"""

from __future__ import annotations

import copy
import datetime as _dt

import numpy as np

PROFILES = {
    # (T1 daily drift mean, T2 daily drift mean, readout daily, gate-err daily, jitter)
    "typical": (-0.002, -0.003, 0.010, 0.005, 0.02),
    "bad_day": (-0.02, -0.06, 0.08, 0.06, 0.05),
    "recalibrated": (0.01, 0.015, -0.05, -0.04, 0.01),
}


def _clip(v, lo=1e-6, hi=0.9):
    return float(min(hi, max(lo, v)))


def drift_calibration(
    baseline: dict, *, days: float = 30.0, seed: int = 7, profile: str = "typical"
) -> dict:
    rng = np.random.default_rng(seed)
    t1d, t2d, rod, ged, jit = PROFILES.get(profile, PROFILES["typical"])
    out = copy.deepcopy(baseline)

    for q in out.get("qubits", []):
        if q.get("T1_us") is not None:
            q["T1_us"] = float(max(1.0, q["T1_us"] * (1 + t1d * days + rng.normal(0, jit))))
        if q.get("T2_us") is not None:
            q["T2_us"] = float(max(1.0, q["T2_us"] * (1 + t2d * days + rng.normal(0, jit))))
        if q.get("readout_error") is not None:
            q["readout_error"] = _clip(q["readout_error"] * (1 + rod * days + rng.normal(0, jit)))
        for k in ("prob_meas0_prep1", "prob_meas1_prep0"):
            if q.get(k) is not None:
                q[k] = _clip(q[k] * (1 + rod * days + rng.normal(0, jit)))

    for g in out.get("gates", []):
        if g.get("error") is not None:
            g["error"] = _clip(g["error"] * (1 + ged * days + rng.normal(0, jit)), hi=0.75)

    # advance the captured_at timestamp
    try:
        base_ts = _dt.datetime.fromisoformat(str(baseline["captured_at"]).replace("Z", "+00:00"))
    except Exception:
        base_ts = _dt.datetime.now(_dt.timezone.utc)
    new_ts = base_ts + _dt.timedelta(days=days)
    out["captured_at"] = new_ts.isoformat()
    out.setdefault("provenance", {})["source"] = "simulated_drift"
    out["provenance"]["drift_profile"] = profile
    out["provenance"]["drift_days"] = days
    return out
