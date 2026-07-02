# Getting started

Record and reproduce quantum runs in about five minutes — fully offline, no account required. Follow the
steps on the left; the commands are on the right.

<div class="doc-split" markdown="1">
<div class="doc-prose" markdown="1">

## 1. Install

Create a virtualenv and install the provenance engine (`ql-core`) plus the client and `ql` CLI (`ql-sdk`).
The `[aer]` extra pulls in Qiskit Aer so you can run circuits on a local simulator.

Other connector extras: `[qiskit_runtime]`, `[braket]`, `[azure]`.

## 2. Capture your first run

Wrap any function that submits a quantum job with `@ql.capture`. When it returns, the SDK records the
circuit, backend, calibration and result to a local ledger.

> **Note:** Capture writes to a local SQLite ledger — nothing leaves your machine and no account is needed.

## 3. Explore what you recorded

Every run carries its full provenance: circuit, backend, the calibration snapshot it ran against, the
result distribution, and a portable `run_hash`.

## 4. Reproduce with drift — the "aha"

Re-run the stored circuit against a device state drifted forward in time, then score how much the result
changed with a Hellinger fidelity and a plain-language verdict. Deterministic and offline.

## 5. Push to a server (optional)

To publish Result Cards, run compliance, or compare against the public corpus, push your local runs to a
hosted or self-hosted server. Create an API key under **Settings → API keys**, then:

> **Tip:** `ql push` is idempotent — it keys on `run_hash`, so re-pushing the same run is a no-op.

</div>
<div class="doc-rail" markdown="1">

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e packages/ql-core -e "packages/ql-sdk[aer]"
```

```python
# examples/bell.py
import quantumledger as ql
from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator

@ql.capture(project="bell")
def run_bell():
    qc = QuantumCircuit(2)
    qc.h(0)
    qc.cx(0, 1)
    qc.measure_all()
    return AerSimulator().run(qc, shots=4096)

run_bell()
```

```bash
ql list                 # every recorded run
ql show <run_id>        # circuit + backend + calibration + result + hash
```

```bash
ql reproduce <run_id> --days 90 --profile bad_day --html report.html
```

```bash
ql login --token <api-key> --endpoint http://localhost:8000
ql push
```

</div>
</div>

## Running the full platform locally

To bring up the web app + API (dashboard, compliance, leaderboard, cards):

```bash
pip install -e packages/ql-core -e packages/ql-sdk -e packages/ql-crawler -e server
PYTHONPATH=server python scripts/seed_demo.py       # seed a walkable demo
PYTHONPATH=server uvicorn app.main:app --port 8000  # web + API at :8000
```

Already signed in and staring at an empty dashboard? Use **Load demo data** to populate your workspace with
sample runs, a reproduction, a published card and an evaluated framework — no CLI required.

Next: [Core concepts](/docs/core-concepts) · [Product tour](/docs/product-tour).
