# Tracker, corpus & leaderboard

Beyond your own runs, Provenova keeps a **public record of how quantum hardware is progressing**. It has
three public views:

- **[/tracker](/tracker)** — plain-English, long-run trends: how often operations fail, whether error
  correction is working, how big machines are getting, how today's cloud fleet is improving, and how far
  all of it is from tasks like breaking RSA-2048. Every number links to its source.
- **[/hardware](/hardware)** — one page per device: a plain-English summary, every metric with its own
  date and source, how it compares with the rest of the record, and its history.
- **[/leaderboard](/leaderboard)** — devices ranked on one measurement at a time (current devices by
  default; older ones on request).

## Evidence labels

Not every number deserves the same trust, so every figure carries a label:

| Label | What it means |
|---|---|
| **Published experiments** | A result reported in a paper (peer-reviewed or preprint), usually a record on a few qubits in a lab. |
| **Device calibration** | The machine's own measurement of every qubit and gate on a given day, published by the vendor. |
| **Independent benchmark** | The same open test run on many vendors' machines by a third party (Metriq). |
| **Vendor claim** | A number from a company's own announcement or spec sheet, not independently checked. |
| **Resource estimate** | A paper's calculation of what a future task would need, under its stated assumptions. |
| **Programme announcement** | A decision announced by a funder or government programme. |

## Sources & licensing

Everything public lives in the repository's [`datasets/`](https://github.com/ben-gy/provenova/tree/main/datasets)
directory, so every change is reviewable:

- **IBM calibration history** (`datasets/ibm/`, **Apache-2.0**) — real calibration snapshots of about 70
  IBM Quantum devices, 2019 onwards, taken from the git history of Qiskit's fake backends so each snapshot
  keeps the device's own `last_update_date`. Each one stores aggregate metrics (median two-qubit error,
  T1, T2, readout error, qubit count) plus a permalink to the exact raw file at its commit and that file's
  SHA-256, so every number can be re-derived. Built by `scripts/build_ibm_history.py`.
- **Metriq benchmarks** (`datasets/metriq/`, **CC-BY-4.0**) — every dated
  [metriq-gym](https://metriq.info) run of EPLG, CLOPS, BSEQ, QFT accuracy and QAOA ratio: one snapshot
  per device per day, holding only that day's measurements, each citing its source file. Built by
  `scripts/fetch_metriq.py`.
- **Published experiments** (`datasets/experiments/`, **MIT**) — François-Marie Le Régent's
  [Awesome Quantum Computing Experiments](https://github.com/francois-marie/awesome-quantum-computing-experiments)
  database, vendored at a pinned commit by `scripts/fetch_experiments.py`. Corrections belong upstream.
- **Vendor-reported** (`datasets/vendor_specs/`) — headline manufacturer specifications, shown as
  *claims* with a distinct badge and the source URL recorded in each snapshot.
- **Context** (`datasets/context/tracker.json`) — hand-curated, cited facts for the tracker (resource
  estimates, programme decisions), with an `as_of` date.

Nothing is fabricated: each value is copied from its cited source, and the `license_ref` travels with
every snapshot.

## How data reaches the site

Two paths, both automatic:

- **Daily collectors.** Once a day the platform checks its upstreams and stores anything new, with
  no redeploy: every new Metriq run (daily), new IBM calibration files in Qiskit (weekly, downloading
  only files that changed) and new versions of the experiments dataset (weekly, published only if
  the licence is still MIT and the columns still match). Each collector's last run is recorded and
  shown on `/tracker` as "sources checked".
- **The committed bundle.** On startup the server reconciles the corpus with `datasets/`
  (`app.services.corpus_sync`): missing snapshots are inserted, and bundle rows that no longer
  appear in the bundle (a corrected date, a renamed device) are removed. Rows the collectors added
  are never removed, and both paths compute the same content hash, so they never duplicate.
  Refreshing the committed copy is: run the build script, review the diff, commit, deploy.

Device pages merge snapshots: each metric shows its **latest measurement and that measurement's date**,
since a benchmark run measures one thing at a time.

## The leaderboard

`/leaderboard` ranks devices on one metric at a time — calibration metrics (median two-qubit error,
readout error, T1 / T2, best pair fidelity) and cross-vendor benchmarks where published (#AQ, Quantum
Volume, CLOPS, two-qubit fidelity, EPLG, BSEQ, QFT accuracy, QAOA ratio). Error rates are also shown as
"fails about 1 in N". By default it ranks devices measured in the last two years; add `?historical=true`
to include everything.

## API

```
GET /api/v1/tracker                                  # every series behind /tracker, with sources
GET /api/v1/leaderboard?metric=median_2q_error       # ranked devices
GET /api/v1/backends/<provider>/<backend_id>/trend   # one device's dated snapshots
```

## The crawler

The `provenova-crawler` package can also collect vendor calibration data, normalize it to the open
`qlprov/calibration/1.0` schema, apply each vendor's terms-of-service redistribution policy (a **ToS
gate**), deduplicate and ingest it. Its offline `FixtureSource` powers the demo and tests; `LiveSource`
is a skeleton for vendor APIs.

Next: [Product tour](/docs/product-tour).
