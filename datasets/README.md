# Datasets

Real, openly-licensed data used to populate QuantumLedger — no fabricated numbers.

## `ibm/` — IBM Quantum device calibration snapshots

Each file is a real calibration snapshot of an IBM Quantum device (T1/T2, qubit
frequencies, per-gate errors, readout errors), normalized to the
`qlprov/calibration/1.0` schema.

**Source & licence.** These snapshots ship inside Qiskit's `fake_provider`
(`qiskit_ibm_runtime.fake_provider`, e.g. `FakeSherbrooke`, `FakeTorino`), which
IBM open-sources under the **Apache-2.0** licence. They are genuine device
characterizations captured from the named hardware — redistributable with no
licensing concern. Each file records its provenance under `provenance`
(`source`, `license`, `package_version`).

They power the public **"State of Quantum Hardware"** leaderboard and
cross-fleet comparison (the `corpus_snapshots` table).

**Regenerate** (in an isolated environment — `qiskit-ibm-runtime` pulls Qiskit
2.x, which is separate from the runtime's pinned Qiskit 1.x):

```bash
python -m venv /tmp/extract && /tmp/extract/bin/pip install qiskit-ibm-runtime
/tmp/extract/bin/python scripts/extract_ibm_snapshots.py
```

## Benchmark runs

The runs recorded on the platform are **real, deterministic executions** of
canonical open-source benchmark circuits (Bell, GHZ, QFT, Grover,
Deutsch–Jozsa, Bernstein–Vazirani — standard textbook algorithms) on the ideal
**Qiskit Aer statevector** simulator (Apache-2.0), honestly labelled as a
simulator backend. See `scripts/seed_real.py`.

Nothing here is fabricated: real device snapshots (Apache-2.0) for the hardware
landscape, real reproducible simulator runs for the records.
