"""Plain-language readings of hardware metrics, for people who aren't physicists.

Every corpus metric gets a one-sentence meaning, a direction, and (for error
rates) the "fails about 1 in N" reading the tracker uses. ``device_summary``
writes a short, deterministic description of a device from its own numbers.
"""

from __future__ import annotations

import datetime as _dt

from .tracker import one_in

# kind: error (lower is better, read as 1-in-N) · fidelity (1 - error) ·
# time · count · score
METRIC_INFO: dict[str, dict] = {
    "median_2q_error": {"kind": "error", "plain": "How often a typical two-qubit operation on this chip fails, from the vendor's own calibration."},
    "best_2q_fidelity": {"kind": "fidelity", "plain": "Success rate of the chip's single best pair of qubits. The best pair flatters the chip; the typical one matters more."},
    "two_q_fidelity": {"kind": "fidelity", "plain": "The two-qubit success rate the maker quotes for this machine."},
    "median_t1_us": {"kind": "time", "plain": "How long a typical qubit holds its state before it decays, in microseconds. Longer leaves more time to compute."},
    "median_t2_us": {"kind": "time", "plain": "How long a typical qubit keeps its delicate quantum phase, in microseconds."},
    "median_readout_error": {"kind": "error", "plain": "How often reading a typical qubit's value at the end gives the wrong answer."},
    "algorithmic_qubits": {"kind": "count", "plain": "IonQ's #AQ: the largest size at which a set of benchmark algorithms still runs successfully."},
    "quantum_volume": {"kind": "score", "plain": "IBM's Quantum Volume: 2ⁿ means the machine reliably runs random circuits n qubits wide and n layers deep."},
    "clops": {"kind": "count", "plain": "Circuit layers per second: how fast the machine gets through work, overheads included."},
    "eplg": {"kind": "error", "plain": "Metriq's independent test of a 10-qubit chain: how often a layer of two-qubit operations fails, measured end to end."},
    "bseq": {"kind": "count", "plain": "The largest connected group of qubits that all pass a Bell-state entanglement test."},
    "qft_accuracy": {"kind": "score", "plain": "How well the machine runs a small quantum Fourier transform benchmark: 1 is perfect, 0 is no better than noise."},
    "qaoa_ratio": {"kind": "score", "plain": "How much better than random guessing a simple optimisation algorithm does on this machine: 1 is perfect, 0 is no better than chance."},
    "n_qubits": {"kind": "count", "plain": "Physical qubits on the device. More only helps if they are good enough to use together."},
}

MAKERS = {"ibm": "IBM", "iqm": "IQM", "ionq": "IonQ", "quantinuum": "Quantinuum",
          "rigetti": "Rigetti", "origin": "Origin Quantum", "aws": "AWS"}


def reading(key: str, value) -> str | None:
    """'fails about 1 in 780' / '99.6% success' — None when no plain reading helps."""
    kind = METRIC_INFO.get(key, {}).get("kind")
    if not isinstance(value, (int, float)):
        return None
    if kind == "error" and value > 0:
        return f"fails {one_in(value)}"
    if kind == "fidelity" and 0 < value < 1:
        return f"fails {one_in(1 - value)}"
    return None


def percentile(key: str, value, peers: list) -> dict | None:
    """Share of *other* devices this one beats on ``key`` (needs a direction)."""
    from provenova_crawler.corpus import HIGHER_IS_BETTER

    kind = METRIC_INFO.get(key, {}).get("kind")
    if key == "n_qubits" or kind is None or not isinstance(value, (int, float)):
        return None
    others = [v for v in peers if isinstance(v, (int, float))]
    if len(others) < 4:
        return None
    higher = key in HIGHER_IS_BETTER
    beats = sum(1 for v in others if (value > v if higher else value < v))
    return {"beats": beats, "of": len(others), "pct": round(100 * beats / len(others))}


def _date(iso: str | None) -> str:
    if not iso:
        return "an unknown date"
    day = _dt.date.fromisoformat(iso[:10])
    return f"{day.day} {day.strftime('%B %Y')}"


def device_summary(d: dict, pct: dict[str, dict]) -> list[str]:
    """Two or three plain sentences about a device, built only from its data."""
    dm, dates = d.get("derived_metrics") or {}, d.get("metric_dates") or {}
    maker = MAKERS.get(d["provider"].lower(), d["provider"].capitalize())
    out = []
    n = dm.get("n_qubits")
    first = (d.get("first_captured_at") or "")[:4]
    last = (d.get("captured_at") or "")[:4]
    span = f", with records from {first} to {last}" if first and last and first != last else ""
    out.append(f"{d['backend_id']} is {'a ' + str(n) + '-qubit' if n else 'a'} {maker} quantum computer{span}.")

    if dm.get("median_2q_error"):
        s = (f"On its latest calibration ({_date(dates.get('median_2q_error'))}), a typical two-qubit "
             f"operation failed {one_in(dm['median_2q_error'])}")
        p = pct.get("median_2q_error")
        s += (f", better than {p['pct']}% of the {p['of']} other devices in this record." if p else ".")
        out.append(s)
    if dm.get("eplg"):
        out.append(f"In Metriq's independent test ({_date(dates.get('eplg'))}), a layer of operations "
                   f"across a 10-qubit chain failed {one_in(dm['eplg'])}.")
    if dm.get("two_q_fidelity") and d.get("source") == "vendor-reported":
        out.append(f"{maker} quotes a two-qubit success rate of {dm['two_q_fidelity'] * 100:.2f}%, "
                   f"which means failing {one_in(1 - dm['two_q_fidelity'])}. That is the maker's "
                   "claim, not an independent measurement.")
    if d.get("captured_at"):
        last_dt = _dt.datetime.fromisoformat(d["captured_at"])
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=_dt.timezone.utc)
        age = (_dt.datetime.now(_dt.timezone.utc) - last_dt).days
        if age > 365:
            out.append(f"The newest record is from {_date(d['captured_at'])}, so the machine may since "
                       "have been upgraded or retired.")
    return out
