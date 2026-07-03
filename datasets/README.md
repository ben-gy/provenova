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

## Cross-vendor corpus records

To make the leaderboard genuinely multi-vendor (not IBM-only), three additional
directories hold **corpus-record** files (`qlprov/corpus-record/1.0`). Each file
carries its own `provider`, `backend_id`, `captured_at`, `source`, `license_ref`,
`raw_ref` (the source URL), and either an explicit `derived_metrics` block or a
raw `calibration` payload. `scripts/seed_real.py::load_corpus()` loads all four
directories; every value is copied from the file — nothing is invented — and each
row is badged by source & licence in the UI.

### `metriq/` — community benchmarks (CC-BY-4.0)

Cross-vendor benchmark submissions (Algorithmic Qubits, CLOPS, Quantum Volume,
2-qubit gate fidelity, …) from **[Metriq](https://metriq.info)**, Unitary Fund's
open benchmark aggregator, licensed **CC-BY-4.0**. `source: "metriq"`;
`license_ref` and `raw_ref` cite the specific submission. Fetch/refresh with
`scripts/fetch_metriq.py`.

### `iqm/` — raw calibration from Zenodo (CC-BY/CC0), if available

A slot for real raw calibration from an openly-licensed **Zenodo** dataset.
`scripts/fetch_zenodo_iqm.py` searches Zenodo and **only** writes a file if the
record's licence is CC-BY/CC-BY-SA/CC0 (verified and recorded per file).

As of this writing **no IQM Garnet raw-calibration dataset exists on Zenodo** (an
exhaustive search returned only unrelated records), so this directory is empty
and nothing is loaded from it — the fetch script is kept ready for when such a
dataset is published. IQM hardware still appears in the corpus via **Metriq**
(the Braket-hosted `iqm_garnet` / `iqm_emerald` benchmark rows, CC-BY-4.0) and
via **vendor-reported** Garnet specs below.

### `vendor_specs/` — vendor-reported specifications

Headline specs published by manufacturers (IonQ, Quantinuum, Rigetti, IQM). These
are **manufacturer claims**, clearly labelled `source: "vendor-reported"` with
the press/spec URL in `raw_ref`, and rendered with a distinct "Vendor-reported"
badge so they are never confused with independently-reproduced measurements.

## Benchmark runs

The runs recorded on the platform are **real, deterministic executions** of
canonical open-source benchmark circuits (Bell, GHZ, QFT, Grover,
Deutsch–Jozsa, Bernstein–Vazirani — standard textbook algorithms) on the ideal
**Qiskit Aer statevector** simulator (Apache-2.0), honestly labelled as a
simulator backend. See `scripts/seed_real.py`.

Nothing here is fabricated: real device snapshots (Apache-2.0) for the hardware
landscape, real reproducible simulator runs for the records.
