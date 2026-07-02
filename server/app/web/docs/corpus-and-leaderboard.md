# Corpus & leaderboard

Beyond your own runs, QuantumLedger maintains a **public calibration corpus**: a cross-vendor, longitudinal
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

## The leaderboard

`/leaderboard` ranks devices across vendors by calibration quality. You can switch the ranking metric:

- median two-qubit gate error,
- T1 / T2 coherence times,
- readout fidelity.

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
