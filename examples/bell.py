"""The <5-minute, one-line capture demo.

    pip install provenova[aer]
    python examples/bell.py

Records the circuit + calibration snapshot + result to a local, offline ledger.
Then: `ql list`, `ql show <id>`, `ql reproduce <id>`.
"""

from __future__ import annotations

import provenova as ql
from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator


@ql.capture(project="bell")  # <-- the one line
def run_bell():
    qc = QuantumCircuit(2)
    qc.h(0)
    qc.cx(0, 1)
    qc.measure_all()
    backend = AerSimulator()
    return backend.run(qc, shots=4096)


if __name__ == "__main__":
    job = run_bell()  # you still get your native result, unchanged
    print("native result counts:", job.result().get_counts())
    rows = ql.LocalLedger(ql.load_config().db_url).list_runs(limit=1)
    if rows:
        print("recorded run:", rows[0]["id"], "run_hash:", rows[0]["run_hash"][:16])
        print("try:  ql show", rows[0]["id"], " |  ql reproduce", rows[0]["id"])
