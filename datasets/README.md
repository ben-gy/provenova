# Datasets

Real, openly-licensed data behind Provenova's public pages (`/tracker`, `/hardware`,
`/leaderboard`) — no fabricated numbers. The server reconciles its public corpus with this
directory on every startup (`server/app/services/corpus_sync.py`), so publishing a data
change is: run the build script, review the diff, commit, deploy.

Between deploys, scheduled collectors (`server/app/services/collectors.py`, run daily by the
Cloudflare Worker cron) add new Metriq runs, new IBM calibration files and new versions of
the experiments dataset straight to the database. This directory is the reviewed snapshot;
re-run the scripts below now and then to bring it level with what the collectors found.

| Directory | Source | Licence | Built by |
|---|---|---|---|
| `ibm/` | IBM Quantum calibration history from Qiskit's fake backends (git history, 2019 →) | Apache-2.0 | `scripts/build_ibm_history.py` |
| `metriq/` | Every dated metriq-gym benchmark run (EPLG, CLOPS, BSEQ, QFT, QAOA) | CC-BY-4.0 | `scripts/fetch_metriq.py` |
| `experiments/` | Awesome Quantum Computing Experiments (published lab records) | MIT | `scripts/fetch_experiments.py` |
| `vendor_specs/` | Manufacturer-published headline specs, shown as claims | vendor claim | `scripts/build_vendor_specs.py` |
| `context/` | Hand-curated, cited facts for `/tracker` (resource estimates, programmes) | — | edit by hand |

## File shapes

- **`qlprov/corpus-series/1.0`** (`ibm/`, `metriq/`): one device, many dated `snapshots`.
  Each snapshot holds `captured_at`, `derived_metrics` and its provenance — for IBM a
  `raw_ref` permalink to the exact source file at its commit plus that file's `raw_sha256`;
  for Metriq a `metric_provenance` list citing each benchmark run's file.
- **`qlprov/corpus-record/1.0`** (`vendor_specs/`): one device, one reading, with
  `derived_metrics` and `snapshot_json.metric_provenance` source URLs.

## IBM calibration history

IBM ships real device calibration (`props_<device>.json`: T1/T2, gate and readout errors,
stamped with the device's own `last_update_date`) in Qiskit's open-source fake backends. Those
files have been refreshed over the years, so their git history is a genuine longitudinal record.
`build_ibm_history.py` walks it in `Qiskit/qiskit-ibm-runtime` and the two older locations in
`Qiskit/qiskit`, keeps only files naming a real device (`ibm_*`, `ibmq_*`), dedupes on
(device, `last_update_date`) and stores aggregates — so the files stay small while every number
can be re-derived from the linked raw file.

```bash
GITHUB_TOKEN=... python scripts/build_ibm_history.py
```

## Metriq

[Metriq](https://metriq.info) (Unitary Foundation) publishes each metriq-gym run as a timestamped
JSON file in `unitaryfoundation/metriq-data` under CC-BY-4.0. `fetch_metriq.py` keeps one
snapshot per device per day with only that day's measurements (never carried-forward values),
and files Braket-hosted devices under their maker (e.g. `rigetti/cepheus-1-108q`). The server's
Growth API refresh uses the same builder, so both produce identical content hashes.

## Published experiments

[Awesome Quantum Computing Experiments](https://github.com/francois-marie/awesome-quantum-computing-experiments)
by François-Marie Le Régent (MIT; paper arXiv:2507.03678) — one row per landmark experiment with its
year, platform, headline number and paper link. Vendored verbatim at a pinned commit (see
`experiments/SOURCE.json`); corrections belong upstream.

## Vendor-reported specs

Headline specifications published by manufacturers (press releases, spec pages). They are
labelled `vendor-reported` everywhere, never marked as redistributable raw data, and never
presented as independently reproduced.
