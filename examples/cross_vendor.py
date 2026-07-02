"""Cross-vendor compare (UC2): record the same circuit on N backends, then diff.

    python examples/cross_vendor.py
"""

from __future__ import annotations

import quantumledger_core as qc
from qiskit import QuantumCircuit
from quantumledger_core.models import bootstrap_local
from quantumledger_core.reproduce import runner


def ghz(n=4):
    c = QuantumCircuit(n)
    c.h(0)
    for i in range(n - 1):
        c.cx(i, i + 1)
    return c


def main():
    engine = qc.init_db("sqlite:///:memory:")
    s = qc.session_factory(engine)()
    ws = bootstrap_local(s)
    s.commit()

    base = {"basis_gates": ["rz", "sx", "x", "cx", "id"], "kind": "qpu"}
    ibm = runner.record_run(s, workspace=ws, qc=ghz(4),
                            backend_spec={"vendor": "ibm", "name": "ibm_kyiv",
                                          "coupling_map": [[0, 1], [1, 2], [2, 3]], **base},
                            shots=4096, seed=7, project="vendor-shootout")
    s.commit()
    # reproduce the same circuit on IonQ (all-to-all) -> cross-vendor diff
    ionq_run, ev = runner.reproduce_run(
        s, ibm, workspace=ws, days=1, profile="typical",
        target_backend_spec={"vendor": "ionq", "name": "ionq_forte", "coupling_map": None, **base},
    )
    s.commit()
    print("backend substitution:", ev.diff["backend_substitution"])
    print("verdict:", ev.verdict, "HF:", round(ev.reproducibility_score, 4))
    print("transpilation delta:", ev.transpilation_delta)


if __name__ == "__main__":
    main()
