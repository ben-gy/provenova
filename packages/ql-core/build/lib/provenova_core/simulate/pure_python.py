"""A small, deterministic pure-Python statevector simulator (numpy only).

This is the always-available engine and the *ideal* (noiseless) oracle used for
scoring. It intentionally supports the gate set our example circuits and Qiskit's
default transpilation to a simple basis produce. Noise (depolarizing + readout
confusion) is applied from a calibration snapshot so device state genuinely
drives outcomes, and everything is seeded so a run is bit-for-bit reproducible.

It is not a high-performance simulator — it exists so the reproduce/diff "aha"
demo runs with zero heavy dependencies when qiskit-aer is unavailable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Gate:
    name: str
    qubits: tuple[int, ...]
    params: tuple[float, ...] = ()


@dataclass
class SimCircuit:
    """A minimal circuit IR the engines consume."""

    n_qubits: int
    gates: list[Gate] = field(default_factory=list)
    # measured (qubit -> classical bit); default: measure all in order
    measure: list[tuple[int, int]] | None = None

    def h(self, q):
        self.gates.append(Gate("h", (q,)))
        return self

    def x(self, q):
        self.gates.append(Gate("x", (q,)))
        return self

    def rz(self, theta, q):
        self.gates.append(Gate("rz", (q,), (theta,)))
        return self

    def sx(self, q):
        self.gates.append(Gate("sx", (q,)))
        return self

    def cx(self, c, t):
        self.gates.append(Gate("cx", (c, t)))
        return self


# --- single-qubit unitaries -------------------------------------------------
_ISQRT2 = 1 / math.sqrt(2)
_H = np.array([[_ISQRT2, _ISQRT2], [_ISQRT2, -_ISQRT2]], dtype=complex)
_X = np.array([[0, 1], [1, 0]], dtype=complex)
_SX = 0.5 * np.array([[1 + 1j, 1 - 1j], [1 - 1j, 1 + 1j]], dtype=complex)


def _rz(theta: float) -> np.ndarray:
    return np.array([[np.exp(-1j * theta / 2), 0], [0, np.exp(1j * theta / 2)]], dtype=complex)


def _apply_1q(state: np.ndarray, u: np.ndarray, q: int, n: int) -> np.ndarray:
    state = state.reshape([2] * n)
    state = np.tensordot(u, state, axes=([1], [q]))
    state = np.moveaxis(state, 0, q)
    return state.reshape(-1)


def _apply_cx(state: np.ndarray, c: int, t: int, n: int) -> np.ndarray:
    state = state.reshape([2] * n)
    # For basis components where control qubit == 1, flip target.
    idx_c1 = [slice(None)] * n
    idx_c1[c] = 1
    sub = state[tuple(idx_c1)]
    sub = np.flip(sub, axis=t if t < c else t - 1)
    state[tuple(idx_c1)] = sub
    return state.reshape(-1)


def _statevector(circ: SimCircuit) -> np.ndarray:
    n = circ.n_qubits
    state = np.zeros(2**n, dtype=complex)
    state[0] = 1.0
    for g in circ.gates:
        if g.name == "h":
            state = _apply_1q(state, _H, g.qubits[0], n)
        elif g.name == "x":
            state = _apply_1q(state, _X, g.qubits[0], n)
        elif g.name == "sx":
            state = _apply_1q(state, _SX, g.qubits[0], n)
        elif g.name == "rz":
            state = _apply_1q(state, _rz(g.params[0]), g.qubits[0], n)
        elif g.name == "cx":
            state = _apply_cx(state, g.qubits[0], g.qubits[1], n)
        elif g.name in ("id", "barrier", "measure"):
            continue
        else:  # pragma: no cover - unsupported gate
            raise ValueError(f"pure-python engine: unsupported gate {g.name!r}")
    return state


def ideal_distribution(circ: SimCircuit) -> dict[str, float]:
    """Exact measurement probabilities over little-endian bitstrings."""
    state = _statevector(circ)
    probs = np.abs(state) ** 2
    n = circ.n_qubits
    out: dict[str, float] = {}
    for i, p in enumerate(probs):
        if p < 1e-12:
            continue
        bits = format(i, f"0{n}b")[::-1]  # qubit 0 is least significant
        out[bits] = float(p)
    return out


def _readout_flip(bit: str, calib, rng: np.random.Generator) -> str:
    """Apply per-qubit readout confusion from calibration to a sampled bitstring."""
    if calib is None:
        return bit
    qmap = {q["index"]: q for q in calib.get("qubits", [])}
    chars = list(bit)
    for qi, ch in enumerate(chars):
        q = qmap.get(qi)
        if not q:
            continue
        if ch == "0":
            p = q.get("prob_meas1_prep0") or (q.get("readout_error") or 0.0) * 0.5
            if rng.random() < (p or 0.0):
                chars[qi] = "1"
        else:
            p = q.get("prob_meas0_prep1") or (q.get("readout_error") or 0.0) * 0.5
            if rng.random() < (p or 0.0):
                chars[qi] = "0"
    return "".join(chars)


def _depolarize_prob(circ: SimCircuit, calib) -> float:
    """Aggregate a simple per-run depolarizing probability from gate errors."""
    if calib is None:
        return 0.0
    gate_err = {(g["gate"], tuple(g["qubits"])): g.get("error", 0.0) for g in calib.get("gates", [])}
    total = 0.0
    for g in circ.gates:
        e = gate_err.get((g.name, g.qubits))
        if e is None:
            # fall back to any error for that gate type
            for (gn, _q), ev in gate_err.items():
                if gn == g.name:
                    e = ev
                    break
        total += e or 0.0
    return min(0.75, total)


def run_counts(circ: SimCircuit, calib: dict | None, shots: int, seed: int) -> dict[str, int]:
    """Sample ``shots`` outcomes with calibration-driven noise, deterministically."""
    rng = np.random.default_rng(seed)
    dist = ideal_distribution(circ)
    n = circ.n_qubits
    bitstrings = list(dist.keys())
    probs = np.array([dist[b] for b in bitstrings])
    probs = probs / probs.sum()

    depol = _depolarize_prob(circ, calib)
    counts: dict[str, int] = {}
    for _ in range(shots):
        if depol > 0 and rng.random() < depol:
            # depolarized shot: uniform random bitstring
            val = int(rng.integers(0, 2**n))
            bit = format(val, f"0{n}b")[::-1]
        else:
            bit = bitstrings[int(rng.choice(len(bitstrings), p=probs))]
        bit = _readout_flip(bit, calib, rng)
        counts[bit] = counts.get(bit, 0) + 1
    return counts
