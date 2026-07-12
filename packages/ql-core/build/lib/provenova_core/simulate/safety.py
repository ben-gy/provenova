"""Safety validation for untrusted qlir circuit payloads.

Turning external JSON into a circuit is only safe on bounded, allowlisted input:

* ``qiskit_from_ir`` builds a real ``QuantumCircuit`` — an unknown gate name must
  never be dispatched to an arbitrary method;
* the statevector engine allocates ``2**n_qubits`` complex amplitudes and loops
  ``shots`` times — oversized values are a memory/CPU exhaustion vector.

Every entry point that materialises an externally-supplied circuit MUST pass it
through :func:`assert_safe_circuit` first. The bundled gate allowlist and caps
live here so the ingest API, the growth API and the bridge all agree.
"""

from __future__ import annotations

import math

# Gate allowlist: name -> (n_params, n_qubits). Anything not here is rejected
# before it can reach circuit construction.
ALLOWED_GATES: dict[str, tuple[int, int]] = {
    "h": (0, 1), "x": (0, 1), "y": (0, 1), "z": (0, 1),
    "s": (0, 1), "sdg": (0, 1), "t": (0, 1), "tdg": (0, 1), "sx": (0, 1),
    "id": (0, 1),
    "rz": (1, 1), "rx": (1, 1), "ry": (1, 1),
    "cx": (0, 2), "cz": (0, 2), "swap": (0, 2),
    "ccx": (0, 3),
}
MAX_QUBITS = 10   # bounds the statevector to 2**10
MAX_GATES = 256
MAX_SHOTS = 8192


class UnsafeCircuitError(ValueError):
    """Raised when an untrusted circuit dict violates the allowlist or caps."""


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def assert_safe_circuit(circuit: dict) -> dict:
    """Validate + canonicalise an untrusted qlir circuit dict.

    Raises :class:`UnsafeCircuitError` on anything outside the allowlist/caps.
    The optional ``schema`` tag is not required here (callers that mandate a
    specific schema check it themselves); the returned dict is canonicalised and
    always tagged ``qlir/1.0``.
    """
    if not isinstance(circuit, dict):
        raise UnsafeCircuitError("circuit must be an object")
    n = circuit.get("n_qubits")
    if not _is_int(n) or not (1 <= n <= MAX_QUBITS):
        raise UnsafeCircuitError(f"n_qubits must be an int in [1, {MAX_QUBITS}]")
    gates = circuit.get("gates")
    if not isinstance(gates, list) or not (1 <= len(gates) <= MAX_GATES):
        raise UnsafeCircuitError(f"gates must be a list of 1..{MAX_GATES}")
    canon = []
    for i, g in enumerate(gates):
        if not isinstance(g, dict):
            raise UnsafeCircuitError(f"gate[{i}] must be an object")
        name = g.get("name")
        if name not in ALLOWED_GATES:
            raise UnsafeCircuitError(f"gate[{i}].name {name!r} not in allowlist")
        n_params, n_qubits = ALLOWED_GATES[name]
        qubits = g.get("qubits")
        if (not isinstance(qubits, list) or len(qubits) != n_qubits
                or not all(_is_int(q) and 0 <= q < n for q in qubits)
                or len(set(qubits)) != len(qubits)):
            raise UnsafeCircuitError(
                f"gate[{i}].qubits must be {n_qubits} distinct ints in [0, {n})")
        params = g.get("params", [])
        if not isinstance(params, list) or len(params) != n_params:
            raise UnsafeCircuitError(f"gate[{i}].params must have {n_params} entries")
        fparams = []
        for pv in params:
            if not isinstance(pv, (int, float)) or isinstance(pv, bool) or not math.isfinite(pv):
                raise UnsafeCircuitError(f"gate[{i}].params must be finite numbers")
            fparams.append(float(pv))
        canon.append({"name": name, "qubits": list(qubits), "params": fparams})
    return {"schema": "qlir/1.0", "n_qubits": n, "gates": canon}


def clamp_shots(shots, default: int = 1024) -> int:
    """Coerce an untrusted shot count into [1, MAX_SHOTS]."""
    try:
        s = int(shots)
    except (TypeError, ValueError):
        return default
    return max(1, min(s, MAX_SHOTS))
