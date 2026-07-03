# provenova — SDK & CLI

The open-source client for [Provenova](https://provenova.net), the
vendor-neutral system of record for quantum computing. Capture every quantum
run — circuit, backend, calibration snapshot and result — into an immutable,
offline-verifiable local ledger, then reproduce, score and publish.

```bash
pip install "provenova[aer]"
```

```python
import provenova as ql
from qiskit_aer import AerSimulator

@ql.capture(project="bell")
def run():
    return AerSimulator().run(qc, shots=4096)
```

```console
$ ql list                                  # inspect your local ledger
$ ql reproduce <id> --days 90 --profile bad_day
$ ql push                                  # sync to a hosted / self-hosted server
```

## What you get

- **`@ql.capture`** decorator / context manager — records runs from existing
  code with no rewrites; fully offline, no account required.
- **Vendor connectors** (plugin system): Qiskit Aer, IBM Qiskit Runtime,
  Amazon Braket, Azure Quantum, IonQ. Extras: `[aer]`, `[qiskit_runtime]`,
  `[braket]`, `[azure]`.
- **Local ledger** — SQLite, schema-identical to the hosted store; every run
  bound to the exact calibration that produced it via a Merkle `run_hash`
  that verifies offline.
- **`ql` CLI** — list, show, reproduce (with deterministic drift profiles),
  and content-hash-idempotent push.
- **Open provenance format** (`qlprov/run/1.0`) — portable JSON that verifies
  its own hash without any server.

## Learn more

- Quickstart: https://provenova.net/docs/getting-started
- Capturing runs: https://provenova.net/docs/capturing-runs
- Open schemas: https://provenova.net/docs/open-schemas

License: Apache-2.0. Part of the Provenova monorepo (`packages/ql-sdk`).
