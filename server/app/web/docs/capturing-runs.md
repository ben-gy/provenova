# Capturing runs

Capture is how quantum jobs get into the ledger. It happens client-side in the open-source `quantumledger`
SDK, works offline, and needs no account.

<div class="doc-split" markdown="1">
<div class="doc-prose" markdown="1">

## Three ways to capture

Pick whichever fits your code:

- **Decorator** — wrap the function that submits the job. Cleanest for scripts.
- **Context manager** — wrap just the submission block when you can't decorate.
- **CLI** — run an existing callable under capture without editing its source.

When the wrapped code returns, the SDK intercepts the circuit, backend, calibration and result and writes an
immutable run to your local ledger.

> **Note:** If a connector can't extract a field (e.g. live calibration is unavailable), it records a
> **gap flag** rather than fabricating data — honesty about missing data is part of the provenance guarantee.

</div>
<div class="doc-rail" markdown="1">

<div class="doc-tabs" markdown="1">
<div data-lang="Decorator" markdown="1">

```python
import quantumledger as ql

@ql.capture(project="my-experiment")
def run():
    return backend.run(circuit, shots=4096)
```

</div>
<div data-lang="Context manager" markdown="1">

```python
import quantumledger as ql

with ql.capture(project="my-experiment"):
    result = backend.run(circuit, shots=4096)
```

</div>
<div data-lang="CLI" markdown="1">

```bash
ql capture-target mymod:run --project my-experiment
```

</div>
</div>

</div>
</div>

## The local ledger

Captured runs land in a local **SQLite** store — the same schema the hosted server uses, so a run's
`run_hash` is identical whether it lives locally or in the cloud.

```bash
ql list
ql show <run_id>
```

## Vendor connectors

Extraction is pluggable: a **connector** knows how to pull the circuit, backend, calibration and result out
of one vendor's native objects. Connectors are discovered as entry-point plugins, so installing a connector
package makes it available automatically.

| Connector | Vendor / SDK | Install extra |
|-----------|--------------|---------------|
| `simulator` | Qiskit Aer (local) | `quantumledger[aer]` |
| `qiskit_runtime` | IBM Quantum | `quantumledger[qiskit_runtime]` |
| `braket` | Amazon Braket | `quantumledger[braket]` |
| `azure_quantum` | Azure Quantum | `quantumledger[azure]` |
| `ionq` | IonQ | (bundled) |

```bash
ql connectors          # list what's discovered in your environment
```

## Writing a custom connector

Subclass `quantumledger.connectors.base.Connector`, implement extraction into a `CaptureBundle`, and register
it under the `quantumledger.connectors` entry-point group. It's then discoverable by `ql connectors` and
usable by `@ql.capture` with no further wiring.

Next: [Reproduce & drift](/docs/reproduce-and-drift).
