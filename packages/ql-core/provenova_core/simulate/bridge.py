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


def qiskit_from_ir(ir: SimCircuit):
    """Build a Qiskit QuantumCircuit from our IR (for re-transpilation on reproduce)."""
    from qiskit import QuantumCircuit

    qc = QuantumCircuit(ir.n_qubits)
    for g in ir.gates:
        if g.name == "h":
            qc.h(g.qubits[0])
        elif g.name == "x":
            qc.x(g.qubits[0])
        elif g.name == "sx":
            qc.sx(g.qubits[0])
        elif g.name == "id":
            qc.id(g.qubits[0])
        elif g.name == "rz":
            qc.rz(g.params[0], g.qubits[0])
        elif g.name == "cx":
            qc.cx(g.qubits[0], g.qubits[1])
        else:  # pragma: no cover - keep other named gates best-effort
            getattr(qc, g.name)(*g.params, *g.qubits)
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
