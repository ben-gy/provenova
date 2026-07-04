# Capturing runs

Capture is how quantum jobs get into the ledger. It happens client-side in the open-source `provenova`
SDK, works offline, and needs no account.

<div class="doc-split" markdown="1">
<div class="doc-prose" markdown="1">

## Three ways to capture

Pick whichever fits your code:

- **Decorator** — wrap the function that submits the job. Cleanest for scripts.
- **Context manager** — wrap the submission block when you can't decorate; bind it with `as run` and
  pass what you ran to `run.record(...)`.
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
import provenova as ql

@ql.capture(project="my-experiment")
def run():
    circuit = build_circuit()  # construct (or assign) the circuit inside the function
    return backend.run(circuit, shots=4096)
```

The decorator discovers the circuit by inspecting the wrapped function's local variables at return —
construct the circuit (or assign it to a local) inside the function. The backend may live outside; it
is recovered from the returned job.

</div>
<div data-lang="Context manager" markdown="1">

```python
import provenova as ql

with ql.capture(project="my-experiment") as run:
    job = backend.run(circuit, shots=4096)
    run.record(circuit=circuit, backend=backend, job=job)
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
| `simulator` | Qiskit Aer (local) | `provenova[aer]` |
| `qiskit_runtime` | IBM Quantum | `provenova[qiskit_runtime]` |
| `braket` | Amazon Braket | `provenova[braket]` |
| `azure_quantum` | Azure Quantum | `provenova[azure]` |
| `ionq` | IonQ | (bundled) |

```bash
ql connectors          # list what's discovered in your environment
```

## Writing a custom connector

Subclass `provenova.connectors.base.Connector`, implement extraction into a `CaptureBundle`, and register
it under the `provenova.connectors` entry-point group. It's then discoverable by `ql connectors` and
usable by `@ql.capture` with no further wiring.

Next: [Reproduce & drift](/docs/reproduce-and-drift).
