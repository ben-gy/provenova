"""Reproducibility scoring: Hellinger fidelity (primary), TVD, shot-noise CI."""

from __future__ import annotations

import math

import numpy as np

from ..models import (
    VERDICT_DIVERGENT,
    VERDICT_DRIFTED,
    VERDICT_IRREPRODUCIBLE,
    VERDICT_REPRODUCIBLE,
)


def _support(p: dict, q: dict) -> list[str]:
    return sorted(set(p) | set(q))


def total_variation_distance(p: dict, q: dict) -> float:
    keys = _support(p, q)
    return 0.5 * sum(abs(p.get(k, 0.0) - q.get(k, 0.0)) for k in keys)


def hellinger_fidelity(p: dict, q: dict) -> float:
    """(Bhattacharyya coefficient)^2 — the standard count-distribution fidelity."""
    keys = _support(p, q)
    bc = sum(math.sqrt(max(p.get(k, 0.0), 0.0) * max(q.get(k, 0.0), 0.0)) for k in keys)
    return float(min(1.0, bc * bc))


def jensen_shannon(p: dict, q: dict) -> float:
    keys = _support(p, q)

    def _kl(a, b):
        s = 0.0
        for k in keys:
            av = a.get(k, 0.0)
            bv = b.get(k, 0.0)
            if av > 0 and bv > 0:
                s += av * math.log(av / bv)
        return s

    m = {k: 0.5 * (p.get(k, 0.0) + q.get(k, 0.0)) for k in keys}
    return 0.5 * _kl(p, m) + 0.5 * _kl(q, m)


def shot_noise_tvd_ci(
    counts_a: dict, counts_b: dict, *, resamples: int = 200, seed: int = 0
) -> tuple[float, float]:
    """Bootstrap 95% CI on TVD attributable to shot noise alone."""
    rng = np.random.default_rng(seed)
    keys = _support(counts_a, counts_b)
    na = sum(counts_a.values()) or 1
    nb = sum(counts_b.values()) or 1
    pa = np.array([counts_a.get(k, 0) / na for k in keys])
    pb = np.array([counts_b.get(k, 0) / nb for k in keys])
    tvds = []
    for _ in range(resamples):
        sa = rng.multinomial(na, pa) / na
        sb = rng.multinomial(nb, pb) / nb
        tvds.append(0.5 * float(np.abs(sa - sb).sum()))
    lo, hi = np.percentile(tvds, [2.5, 97.5])
    return float(lo), float(hi)


def verdict_for(hf: float, tvd: float, within_shot_noise: bool) -> str:
    if hf >= 0.99 and within_shot_noise:
        return VERDICT_REPRODUCIBLE
    if hf >= 0.90:
        return VERDICT_DRIFTED
    if hf >= 0.70:
        return VERDICT_DIVERGENT
    return VERDICT_IRREPRODUCIBLE


def score(
    dist_a: dict,
    dist_b: dict,
    *,
    counts_a: dict | None = None,
    counts_b: dict | None = None,
) -> dict:
    hf = hellinger_fidelity(dist_a, dist_b)
    tvd = total_variation_distance(dist_a, dist_b)
    js = jensen_shannon(dist_a, dist_b)
    within = True
    ci = None
    if counts_a and counts_b:
        lo, hi = shot_noise_tvd_ci(counts_a, counts_b)
        ci = [lo, hi]
        within = tvd <= hi + 1e-9
    return {
        "hellinger_fidelity": hf,
        "tvd": tvd,
        "jensen_shannon": js,
        "shot_noise_tvd_ci": ci,
        "within_shot_noise": within,
        "verdict": verdict_for(hf, tvd, within),
    }
