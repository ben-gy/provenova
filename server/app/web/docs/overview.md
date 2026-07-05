# What is Provenova?

**Provenova is the vendor-neutral system of record for quantum computing.**

A quantum result is only meaningful alongside the exact hardware state that produced it. Devices drift
hour to hour, transpilers change circuits, and every vendor reports calibration differently — so a raw
bitstring histogram, on its own, is not reproducible, comparable, or auditable. Provenova binds every
run to the circuit, backend, and **calibration snapshot** that produced it, into one immutable,
hash-chained record. That record is the "source of truth" that mature software has in every other
data-intensive field, applied to the noisy, drifting world of quantum.

> If you can't say *which device state* produced a result, you can't reproduce it, cite it, or attest to it.
> Provenova makes that state a first-class, tamper-evident part of every result.

## The six pillars

Provenova is one product with six connected areas. Each has its own guide:

1. **Capture** — a one-line `@ql.capture` decorator records your job locally, with no account required.
   → [Capturing runs](/docs/capturing-runs)
2. **Provenance store** — an immutable, content-addressed, Merkle-hashed ledger of runs.
   → [Core concepts](/docs/core-concepts)
3. **Reproduce & analyse** — re-run a stored circuit against a drifted device state and score how much
   changed. → [Reproduce & drift](/docs/reproduce-and-drift)
4. **Trust artifacts** — public, citable Result Cards and embeddable badges.
   → [Result cards & badges](/docs/result-cards-and-badges)
5. **Compliance** — pick a standard (FAIR, IEEE P7131, …); evidence is auto-collected from the runs already
   in the record; issue signed, revocable attestations. → [Compliance & attestations](/docs/compliance)
6. **Public corpus** — a cross-vendor, longitudinal corpus of calibration data powering the leaderboard.
   → [Corpus & leaderboard](/docs/corpus-and-leaderboard)

## How the pieces fit together

```
your quantum job
   │  @ql.capture                         (open-source SDK, runs anywhere)
   ▼
local ledger (SQLite, offline)  ──ql push──►  hosted Provenova
   │                                              │
   │ ql reproduce                                 ├─ Records + provenance
   ▼                                              ├─ Reproduce engine
 drift report                                     ├─ Result Cards + badges
 (Hellinger fidelity + diff)                      ├─ Compliance + attestations
                                                  └─ Public corpus + leaderboard
```

You can use Provenova entirely offline (capture, list, reproduce), or push your runs to a hosted or
self-hosted server to publish cards, run compliance, and compare across the public corpus.

## Open core

All of it is developed in the open at
[github.com/ben-gy/provenova](https://github.com/ben-gy/provenova). The client SDK, vendor
connectors, provenance schema, reproduce engine and calibration crawler are **Apache-2.0**. The
server (web app, cards, compliance rule engine, attestations, governance) is source-available
under **BUSL-1.1** — free to self-host, production included; the only reserved right is offering
it to third parties as a hosted, managed, or embedded service or product (paid or free). See
[Licensing](/docs/licensing) and
[Libraries & downloads](/docs/libraries).

## What Provenova is *not*

It is not quantum hardware or control electronics, not a circuit IDE or scheduler, not an error-mitigation
product, and not a general MLOps platform. It is **not an accredited auditor** — it produces the evidence
and signed attestations; your compliance team (or an auditor) verifies them.

## Next steps

- New here? Start with [Getting started](/docs/getting-started).
- Want the mental model? Read [Core concepts](/docs/core-concepts).
- Prefer a screen-by-screen tour? See the [Product tour](/docs/product-tour).
