# Corpus & leaderboard

Beyond your own runs, Provenova maintains a **public calibration corpus**: a cross-vendor, longitudinal
record of how real quantum hardware behaves over time. It powers the **State of Quantum Hardware**
leaderboard at `/leaderboard`.

## The corpus

The corpus is a collection of **calibration snapshots** normalized to the open `qlprov/calibration/1.0`
schema, so an IBM device and an IonQ device can be compared on the same axes. Snapshots are
content-addressed, so identical captures are stored once and referenced many times.

## The crawler

The `quantumledger-crawler` package collects vendor calibration data, normalizes it, applies each vendor's
terms-of-service redistribution policy, deduplicates, and ingests it into the corpus. It has two sources:

- **`FixtureSource`** (default, offline) — reads representative payloads from `fixtures/{vendor}/*.json`.
  This is what the demo and tests use; no network required.
- **`LiveSource`** (optional) — a skeleton for calling real vendor APIs (IBM `BackendProperties`, IonQ
  characterization, Amazon Braket device properties).

A **ToS gate** governs what may be redistributed, so the public corpus respects each vendor's terms.

## Sources & licensing

The corpus is genuinely multi-vendor and every row is labelled by **source and licence** so mixed
provenance is transparent:

- **IBM** — real device calibration snapshots from Qiskit's `fake_provider` (**Apache-2.0**).
- **Metriq** — community benchmark submissions (Algorithmic Qubits, CLOPS, Quantum Volume, 2Q
  fidelity) from [metriq.info](https://metriq.info), **CC-BY-4.0**.
- **Zenodo** — a ready slot for openly-licensed raw calibration datasets, loaded only when a
  record's licence is verified **CC-BY / CC0** (none published for the tracked devices yet; IQM
  hardware currently appears via the Metriq and vendor-reported rows).
- **Vendor-reported** — headline specifications published by manufacturers, shown as *claims* with a
  distinct badge and the source URL recorded in each snapshot's provenance — never presented as
  independently reproduced.

Nothing is fabricated: each value is copied from its cited source. The `redistributable_raw` flag and
`license_ref` travel with every snapshot.

## The leaderboard

`/leaderboard` ranks devices across vendors and switches the ranking metric. Alongside the calibration
metrics (median two-qubit gate error, T1 / T2 coherence, best 2Q fidelity) it also ranks the
cross-vendor benchmark metrics where published:

- **Algorithmic qubits (#AQ)**, **Quantum Volume**, **CLOPS** (higher is better),
- **2-qubit gate fidelity** (higher is better), **EPLG** (error per layered gate — lower is better).

Devices that don't report the selected metric are dropped from that ranking, and each remaining row
carries its **source / licence** badge.

Because snapshots are time-stamped, the corpus also exposes **trends** per device:

```
GET /api/v1/backends/<provider>/<backend_id>/trend
```

— a time series of a backend's calibration quality, so you can see a device improving or degrading over
weeks and months.

## Why it exists

The corpus turns one-off calibration captures into a shared, comparable, historical view of the quantum
hardware fleet — the empirical backdrop against which your own runs and reproductions can be judged.

Next: [Product tour](/docs/product-tour).
