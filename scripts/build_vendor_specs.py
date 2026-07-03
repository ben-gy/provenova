"""Emit datasets/vendor_specs/*.json from VENDOR-REPORTED headline specs.

These are manufacturer claims published on official pages. Each value below was
independently verified against the cited page (fetched twice: once by a sourcing
pass, once by a separate verification pass) and is stored with the exact quoted
phrase + source URL under ``metric_provenance``. They are clearly flagged
``source: "vendor-reported"`` and ``redistributable_raw: false`` so the UI never
presents them as independently-reproduced measurements.

Fidelity values are decimal fractions; #AQ / Quantum Volume / qubit counts are
integers. Nothing is estimated — a metric absent from a vendor's page is omitted.

Run:  python scripts/build_vendor_specs.py
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "datasets" / "vendor_specs"

# device -> record. Each metric carries (value, source_url, quoted phrase).
VENDORS = [
    {
        "provider": "ionq", "backend_id": "Forte", "captured_at": "2026-07-03T00:00:00+00:00",
        "license_ref": "vendor-reported · ionq.com", "raw_ref": "https://www.ionq.com/quantum-systems/forte",
        "metrics": {
            "algorithmic_qubits": (36, "https://www.ionq.com/quantum-systems/forte-enterprise",
                                   "#AQ 36 and 36 qubits"),
            "two_q_fidelity": (0.996, "https://www.ionq.com/quantum-systems/forte",
                               "0.4% 2-Qubit gate error (fidelity = 1 - 0.004)"),
            "n_qubits": (36, "https://www.ionq.com/quantum-systems/forte", "36 qubit count"),
        },
    },
    {
        "provider": "ionq", "backend_id": "Aria", "captured_at": "2026-07-03T00:00:00+00:00",
        "license_ref": "vendor-reported · ionq.com", "raw_ref": "https://www.ionq.com/quantum-systems/aria",
        "metrics": {
            "algorithmic_qubits": (25, "https://www.ionq.com/quantum-systems/aria", "With an #AQ of 25"),
            "two_q_fidelity": (0.994, "https://www.ionq.com/quantum-systems/aria",
                               "0.6% 2-Qubit gate error (fidelity = 1 - 0.006)"),
            "n_qubits": (25, "https://www.ionq.com/quantum-systems/aria", "25 qubit count"),
        },
    },
    {
        "provider": "quantinuum", "backend_id": "H2", "captured_at": "2026-07-03T00:00:00+00:00",
        "license_ref": "vendor-reported · quantinuum.com",
        "raw_ref": "https://www.quantinuum.com/products-solutions/quantinuum-systems/system-model-h2",
        "metrics": {
            "quantum_volume": (33554432, "https://www.quantinuum.com/products-solutions/quantinuum-systems/system-model-h2",
                               "33,554,432 (2^25) quantum volume"),
            "two_q_fidelity": (0.999, "https://www.quantinuum.com/products-solutions/quantinuum-systems/system-model-h2",
                               ">99.9% two-qubit gate fidelity"),
            "n_qubits": (56, "https://www.quantinuum.com/products-solutions/quantinuum-systems/system-model-h2",
                         "56 fully-connected qubits"),
        },
    },
    {
        "provider": "rigetti", "backend_id": "Ankaa-3", "captured_at": "2024-12-23T00:00:00+00:00",
        "license_ref": "vendor-reported · rigetti.com",
        "raw_ref": "https://www.rigetti.com/news/rigetti-computing-launches-84-qubit-ankaa-3-system-achieves-99-5-median-two-qubit-gate-fidelity-milestone",
        "metrics": {
            "n_qubits": (84, "https://www.rigetti.com/news/rigetti-computing-launches-84-qubit-ankaa-3-system-achieves-99-5-median-two-qubit-gate-fidelity-milestone",
                         "84-qubit Ankaa-3 system"),
            "two_q_fidelity": (0.995, "https://www.rigetti.com/news/rigetti-computing-launches-84-qubit-ankaa-3-system-achieves-99-5-median-two-qubit-gate-fidelity-milestone",
                               "99.5% median two-qubit gate fidelity"),
        },
    },
    {
        "provider": "iqm", "backend_id": "Garnet", "captured_at": "2026-07-03T00:00:00+00:00",
        "license_ref": "vendor-reported · aws.amazon.com/braket",
        "raw_ref": "https://aws.amazon.com/braket/quantum-computers/iqm/",
        "metrics": {
            "n_qubits": (20, "https://aws.amazon.com/braket/quantum-computers/iqm/",
                         "20 computational transmon qubits and 30 tunable coupler qubits"),
            "two_q_fidelity": (0.9951, "https://aws.amazon.com/braket/quantum-computers/iqm/",
                               "median 2-qubit gate fidelity of 99.51%"),
        },
    },
]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for v in VENDORS:
        dm = {k: val for k, (val, _u, _t) in v["metrics"].items()}
        dm["source"] = "vendor-reported"
        prov = [{"metric": k, "value": val, "source_url": u, "quote": t}
                for k, (val, u, t) in v["metrics"].items()]
        record = {
            "schema": "qlprov/corpus-record/1.0",
            "provider": v["provider"], "backend_id": v["backend_id"],
            "captured_at": v["captured_at"], "source": "vendor-reported",
            "license_ref": v["license_ref"], "raw_ref": v["raw_ref"],
            "redistributable_raw": False,
            "note": "Manufacturer-published specification (vendor claim), not independently reproduced.",
            "derived_metrics": dm,
            "snapshot_json": {
                "schema": "qlprov/corpus-record/1.0",
                "backend": {"vendor": v["provider"], "name": v["backend_id"]},
                "captured_at": v["captured_at"], "source": "vendor-reported",
                "provenance": {"source": "vendor-reported", "license_ref": v["license_ref"],
                               "disclaimer": "Vendor headline spec; verified against the cited page, "
                                             "not independently measured on this platform."},
                "metric_provenance": prov,
            },
        }
        path = OUT_DIR / f"{v['provider']}_{v['backend_id'].lower().replace(' ', '_')}.json"
        path.write_text(json.dumps(record, indent=2))
        print(f"  wrote {path.relative_to(REPO)}  metrics={list(dm)}")


if __name__ == "__main__":
    main()
