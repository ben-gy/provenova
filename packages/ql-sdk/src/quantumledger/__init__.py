"""QuantumLedger open-source client.

    import quantumledger as ql

    @ql.capture(project="bell")
    def run():
        qc = QuantumCircuit(2, 2); qc.h(0); qc.cx(0, 1); qc.measure_all()
        return AerSimulator().run(qc, shots=1024)

Records the circuit + calibration snapshot + result to a local, offline ledger —
no account, no network. ``ql push`` later syncs to a hosted store.
"""

from __future__ import annotations

from .agent import CaptureAgent, CaptureContext, capture
from .config import QLConfig, load_config, save_config
from .registry import registry
from .store import LocalLedger

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "capture",
    "CaptureAgent",
    "CaptureContext",
    "LocalLedger",
    "QLConfig",
    "load_config",
    "save_config",
    "registry",
]
