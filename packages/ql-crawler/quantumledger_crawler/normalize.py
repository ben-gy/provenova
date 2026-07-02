"""Vendor-native -> ``qlprov/calibration/1.0`` normalization.

Every provider reports calibration in its own units and shape. This module maps
them onto one open schema so the corpus, leaderboard and drift analytics never
special-case a vendor:

* IBM     - T1/T2 in **seconds** -> us; ``frequency`` in **Hz** -> GHz; gate
  ``gate_length`` in **seconds** -> ns; errors already fractional.
* IonQ    - fleet-level ``characterization`` (all-to-all, no per-qubit params).
  ``fidelity`` -> ``error = 1 - fidelity``; T1/T2 already in us; the all-to-all
  topology and missing per-qubit detail become :class:`GapFlag` s.
* Braket  - Rigetti device properties: T1/T2 in **seconds** -> us;
  ``gateFidelity`` -> error; connectivity graph -> per-edge 2q gates.

Any schema field a vendor does not expose is recorded as a
``{"field", "reason": "vendor_not_exposed"}`` gap so downstream tools can tell
"absent" from "zero".
"""

from __future__ import annotations

import datetime as _dt
from statistics import median

CALIBRATION_SCHEMA_ID = "qlprov/calibration/1.0"

# unit conversions -----------------------------------------------------------
_S_TO_US = 1_000_000.0
_S_TO_NS = 1_000_000_000.0
_HZ_TO_GHZ = 1e-9
_US = 1.0  # IonQ / already-us passthrough marker


def _gap(field: str, reason: str = "vendor_not_exposed", detail: str | None = None) -> dict:
    g = {"field": field, "reason": reason}
    if detail is not None:
        g["detail"] = detail
    return g


def _iso(value) -> str | None:
    """Coerce a vendor timestamp to a naive-UTC-free ISO8601 string."""
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_dt.timezone.utc)
        return value.isoformat()
    s = str(value)
    # Fixtures use trailing 'Z'; jsonschema date-time accepts either form, but
    # keep it as-is (valid RFC 3339) rather than risk a lossy reparse.
    return s


def _is_two_qubit(qubits: list[int]) -> bool:
    return len(qubits) == 2


# ---------------------------------------------------------------------------
# IBM
# ---------------------------------------------------------------------------

def _normalize_ibm(raw: dict) -> dict:
    name = raw.get("backend_name") or raw.get("name")
    n_qubits = raw.get("n_qubits")
    qubits_out: list[dict] = []
    gaps: list[dict] = []

    for q in raw.get("qubits", []):
        idx = q.get("qubit")
        t1 = q.get("T1")
        t2 = q.get("T2")
        freq = q.get("frequency")
        anh = q.get("anharmonicity")
        qubits_out.append(
            {
                "index": idx,
                "T1_us": None if t1 is None else t1 * _S_TO_US,
                "T2_us": None if t2 is None else t2 * _S_TO_US,
                "frequency_ghz": None if freq is None else freq * _HZ_TO_GHZ,
                "anharmonicity_ghz": None if anh is None else anh * _HZ_TO_GHZ,
                "readout_error": q.get("readout_error"),
                "prob_meas0_prep1": q.get("prob_meas0_prep1"),
                "prob_meas1_prep0": q.get("prob_meas1_prep0"),
                "readout_length_ns": (
                    None if q.get("readout_length") is None
                    else q["readout_length"] * _S_TO_NS
                ),
            }
        )

    gates_out: list[dict] = []
    for g in raw.get("gates", []):
        length = g.get("gate_length")
        gates_out.append(
            {
                "gate": g.get("gate"),
                "qubits": list(g.get("qubits", [])),
                "error": g.get("gate_error"),
                "length_ns": None if length is None else length * _S_TO_NS,
            }
        )

    if n_qubits is None:
        n_qubits = len(qubits_out) or None

    return {
        "backend": {"vendor": "ibm", "name": name, "n_qubits": n_qubits, "kind": "qpu"},
        "captured_at": _iso(raw.get("last_update_date")),
        "qubits": qubits_out,
        "gates": gates_out,
        "gaps": gaps,
    }


# ---------------------------------------------------------------------------
# IonQ
# ---------------------------------------------------------------------------

def _normalize_ionq(raw: dict) -> dict:
    name = raw.get("name") or raw.get("backend")
    char = raw.get("characterization", {})
    n_qubits = char.get("qubits") or raw.get("qubits")
    gaps: list[dict] = []

    t1_us = char.get("t1")  # IonQ already reports in microseconds
    t2_us = char.get("t2")
    spam = char.get("spam")
    fid = char.get("fidelity", {})
    fid_1q = (fid.get("1q") or {}).get("mean")
    fid_2q = (fid.get("2q") or {}).get("mean")
    timing = char.get("timing", {})

    # IonQ publishes fleet-level (per-machine) numbers, not per-qubit rows.
    qubits_out: list[dict] = []
    for idx in range(int(n_qubits or 0)):
        qubits_out.append(
            {
                "index": idx,
                "T1_us": t1_us,
                "T2_us": t2_us,
                "frequency_ghz": None,
                "readout_error": spam,  # SPAM error is the readout analogue
                "prob_meas0_prep1": None,
                "prob_meas1_prep0": None,
            }
        )

    gaps.append(_gap("qubits.frequency_ghz", detail="IonQ trapped-ion machines do not expose per-qubit frequencies"))
    gaps.append(_gap("qubits.prob_meas0_prep1", detail="only symmetric SPAM error published"))
    gaps.append(_gap("qubits.prob_meas1_prep0", detail="only symmetric SPAM error published"))

    # All-to-all connectivity: a single representative 2q gate rather than a
    # per-edge list. Flag the missing per-pair breakdown.
    gates_out: list[dict] = []
    if fid_1q is not None:
        gates_out.append(
            {
                "gate": "gpi",
                "qubits": [0],
                "error": 1.0 - fid_1q,
                "length_ns": None if timing.get("1q") is None else timing["1q"] * _S_TO_NS,
            }
        )
    if fid_2q is not None:
        gates_out.append(
            {
                "gate": "ms",
                "qubits": [0, 1],
                "error": 1.0 - fid_2q,
                "length_ns": None if timing.get("2q") is None else timing["2q"] * _S_TO_NS,
            }
        )
    gaps.append(
        _gap(
            "gates.coupling",
            reason="all_to_all",
            detail="IonQ is fully connected; no per-edge 2q gate breakdown, one representative gate emitted",
        )
    )

    if not gates_out:
        gaps.append(_gap("gates"))

    return {
        "backend": {"vendor": "ionq", "name": name, "n_qubits": int(n_qubits) if n_qubits else None, "kind": "qpu"},
        "captured_at": _iso(raw.get("date")),
        "qubits": qubits_out,
        "gates": gates_out,
        "general": {
            "connectivity": "all-to-all",
            "native_gates": raw.get("native_gates"),
            "spam_error": spam,
            "fidelity_1q_mean": fid_1q,
            "fidelity_2q_mean": fid_2q,
        },
        "gaps": gaps,
    }


# ---------------------------------------------------------------------------
# Braket (Rigetti standardized device properties)
# ---------------------------------------------------------------------------

def _first_fidelity(entries: list | None) -> float | None:
    if not entries:
        return None
    return entries[0].get("fidelity")


def _normalize_braket(raw: dict) -> dict:
    name = raw.get("deviceName") or raw.get("name")
    paradigm = raw.get("paradigm", {})
    n_qubits = paradigm.get("qubitCount")
    std = raw.get("standardized", {})
    gaps: list[dict] = []

    one_q = std.get("oneQubitProperties", {})
    qubits_out: list[dict] = []
    for key in sorted(one_q, key=lambda k: int(k)):
        props = one_q[key]
        idx = int(key)
        t1 = (props.get("T1") or {}).get("value")
        t2 = (props.get("T2") or {}).get("value")
        readout_fid = (props.get("READOUT") or {}).get("fidelity")
        qubits_out.append(
            {
                "index": idx,
                "T1_us": None if t1 is None else t1 * _S_TO_US,
                "T2_us": None if t2 is None else t2 * _S_TO_US,
                "frequency_ghz": None,
                "readout_error": None if readout_fid is None else 1.0 - readout_fid,
                "prob_meas0_prep1": None,
                "prob_meas1_prep0": None,
            }
        )

    gaps.append(_gap("qubits.frequency_ghz", detail="Braket standardized properties omit qubit frequency"))
    gaps.append(_gap("qubits.prob_meas0_prep1", detail="only symmetric readout fidelity published"))
    gaps.append(_gap("qubits.prob_meas1_prep0", detail="only symmetric readout fidelity published"))
    gaps.append(_gap("gates.length_ns", detail="Braket standardized gate properties omit gate duration"))

    two_q = std.get("twoQubitProperties", {})
    gates_out: list[dict] = []
    for pair in sorted(two_q):
        a, b = pair.split("-")
        entry = two_q[pair]
        fids = entry.get("twoQubitGateFidelity", [])
        gate_name = fids[0].get("gateName") if fids else "2q"
        fid = _first_fidelity(fids)
        gates_out.append(
            {
                "gate": gate_name,
                "qubits": [int(a), int(b)],
                "error": None if fid is None else 1.0 - fid,
                "length_ns": None,
            }
        )

    return {
        "backend": {"vendor": "rigetti", "name": name, "n_qubits": n_qubits, "kind": "qpu"},
        "captured_at": _iso(raw.get("executionWindowUpdatedAt")),
        "qubits": qubits_out,
        "gates": gates_out,
        "general": {
            "provider_name": raw.get("providerName"),
            "connectivity_graph": (paradigm.get("connectivity") or {}).get("connectivityGraph"),
            "native_gates": paradigm.get("nativeGateSet"),
        },
        "gaps": gaps,
    }


# ---------------------------------------------------------------------------
# Fleet metrics + assembly
# ---------------------------------------------------------------------------

def _fleet_metrics(qubits: list[dict], gates: list[dict]) -> dict:
    t1s = [q["T1_us"] for q in qubits if q.get("T1_us") is not None]
    t2s = [q["T2_us"] for q in qubits if q.get("T2_us") is not None]
    two_q_errors = [
        g["error"]
        for g in gates
        if g.get("error") is not None and _is_two_qubit(g.get("qubits", []))
    ]
    metrics: dict = {}
    if t1s:
        metrics["median_t1_us"] = median(t1s)
    if t2s:
        metrics["median_t2_us"] = median(t2s)
    if two_q_errors:
        metrics["median_2q_error"] = median(two_q_errors)
        metrics["best_2q_fidelity"] = 1.0 - min(two_q_errors)
    return metrics


_DISPATCH = {
    "ibm": _normalize_ibm,
    "ionq": _normalize_ionq,
    "braket": _normalize_braket,
    "rigetti": _normalize_braket,
}


def to_snapshot(provider: str, raw: dict) -> dict:
    """Normalize a vendor-native ``raw`` reading into a ``qlprov/calibration/1.0`` payload."""
    fn = _DISPATCH.get(provider)
    if fn is None:
        raise ValueError(f"no normalizer for provider {provider!r}")
    partial = fn(raw)

    qubits = partial.get("qubits", [])
    gates = partial.get("gates", [])
    gaps = partial.get("gaps", [])

    if partial.get("captured_at") is None:
        # Schema requires captured_at; fall back to now and flag the gap.
        partial["captured_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
        gaps.append(_gap("captured_at", detail="vendor timestamp missing; used ingest time"))

    payload: dict = {
        "schema": CALIBRATION_SCHEMA_ID,
        "backend": partial["backend"],
        "captured_at": partial["captured_at"],
        "qubits": qubits,
        "gates": gates,
        "fleet_metrics": _fleet_metrics(qubits, gates),
        "provenance": {"source": "crawler", "provider": provider},
        "gaps": gaps,
    }
    if partial.get("general"):
        payload["general"] = partial["general"]
    return payload
