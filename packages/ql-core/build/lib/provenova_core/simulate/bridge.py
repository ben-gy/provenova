"""Bridge between Qiskit circuits and our minimal ``SimCircuit`` IR.

We transpile with Qiskit (real transpiler => genuine transpilation deltas) to a
small basis {rz, sx, x, cx, id} the pure-Python engine understands, then convert
back to the IR for deterministic execution and scoring.
"""

from __future__ import annotations

from .pure_python import Gate, SimCircuit

TARGET_BASIS = ["rz", "sx", "x", "cx", "id"]


def qiskit_to_ir(qc) -> SimCircuit:
    """Convert a (transpiled) Qiskit QuantumCircuit into our SimCircuit IR."""
    n = qc.num_qubits
    qubit_index = {bit: i for i, bit in enumerate(qc.qubits)}
    ir = SimCircuit(n_qubits=n)
    for inst in qc.data:
        op = inst.operation
        name = op.name
        qs = tuple(qubit_index[b] for b in inst.qubits)
        if name in ("measure", "barrier"):
            continue
        params = tuple(float(p) for p in op.params) if op.params else ()
        ir.gates.append(Gate(name, qs, params))
    return ir


# Explicit gate -> builder dispatch. This is a hard allowlist: a gate name that
# isn't here raises, instead of the old ``getattr(qc, name)(...)`` fallback which
# would invoke ANY QuantumCircuit method with attacker-controlled arguments.
# Covers exactly the gates that pass ``safety.assert_safe_circuit``.
_GATE_BUILDERS = {
    "h": lambda qc, p, q: qc.h(q[0]),
    "x": lambda qc, p, q: qc.x(q[0]),
    "y": lambda qc, p, q: qc.y(q[0]),
    "z": lambda qc, p, q: qc.z(q[0]),
    "s": lambda qc, p, q: qc.s(q[0]),
    "sdg": lambda qc, p, q: qc.sdg(q[0]),
    "t": lambda qc, p, q: qc.t(q[0]),
    "tdg": lambda qc, p, q: qc.tdg(q[0]),
    "sx": lambda qc, p, q: qc.sx(q[0]),
    "id": lambda qc, p, q: qc.id(q[0]),
    "rz": lambda qc, p, q: qc.rz(p[0], q[0]),
    "rx": lambda qc, p, q: qc.rx(p[0], q[0]),
    "ry": lambda qc, p, q: qc.ry(p[0], q[0]),
    "cx": lambda qc, p, q: qc.cx(q[0], q[1]),
    "cz": lambda qc, p, q: qc.cz(q[0], q[1]),
    "swap": lambda qc, p, q: qc.swap(q[0], q[1]),
    "ccx": lambda qc, p, q: qc.ccx(q[0], q[1], q[2]),
}


def qiskit_from_ir(ir: SimCircuit):
    """Build a Qiskit QuantumCircuit from our IR (for re-transpilation on reproduce).

    Only allowlisted gates are dispatched; an unknown gate name raises ValueError
    rather than being passed to ``getattr(qc, name)`` (arbitrary method call).
    """
    from qiskit import QuantumCircuit

    qc = QuantumCircuit(ir.n_qubits)
    for g in ir.gates:
        build = _GATE_BUILDERS.get(g.name)
        if build is None:
            raise ValueError(f"unsupported gate {g.name!r} in circuit IR")
        build(qc, g.params, g.qubits)
    return qc


def ir_to_dict(ir: SimCircuit) -> dict:
    return {
        "schema": "qlir/1.0",
        "n_qubits": ir.n_qubits,
        "gates": [{"name": g.name, "qubits": list(g.qubits), "params": list(g.params)} for g in ir.gates],
    }


def dict_to_ir(d: dict) -> SimCircuit:
    ir = SimCircuit(n_qubits=d["n_qubits"])
    for g in d["gates"]:
        ir.gates.append(Gate(g["name"], tuple(g["qubits"]), tuple(g.get("params", []))))
    return ir


def transpile_qiskit(qc, *, basis_gates=None, coupling_map=None, optimization_level=1, seed=42):
    """Transpile a Qiskit circuit to our target basis; returns (transpiled_qc)."""
    from qiskit import transpile

    return transpile(
        qc,
        basis_gates=basis_gates or TARGET_BASIS,
        coupling_map=coupling_map,
        optimization_level=optimization_level,
        seed_transpiler=seed,
    )


def circuit_metrics(qc) -> dict:
    """Depth, size and gate counts for a Qiskit circuit."""
    counts = {}
    for inst in qc.data:
        counts[inst.operation.name] = counts.get(inst.operation.name, 0) + 1
    return {
        "depth": qc.depth(),
        "size": qc.size(),
        "n_qubits": qc.num_qubits,
        "gate_counts": counts,
        "n_cx": counts.get("cx", 0),
    }
