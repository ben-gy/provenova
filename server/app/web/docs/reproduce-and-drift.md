# Reproduce & drift

Reproduction is QuantumLedger's "aha": take a run you recorded, re-run its exact circuit against a device
state that has **drifted** over time, and measure how much the result changed — with a single score and a
plain-language verdict.

## Running a reproduction

**CLI (offline):**

```bash
ql reproduce <run_id> --days 90 --profile bad_day --html report.html
```

**In the app:** open a run at `/app/records/<run_id>` and use the **Reproduce** form — pick a number of
days to drift and a profile, then submit. The reproduction (and its report) attach to the run.

## Drift profiles

Drift is modelled **deterministically** (seeded), so the same inputs always give the same result — no
flakiness. The number of days advances the calibration snapshot's timestamp and degrades T1/T2, readout,
and gate errors accordingly. Three profiles:

| Profile | Meaning |
|---------|---------|
| `typical` | Gradual, realistic day-to-day degradation. |
| `bad_day` | Aggressive degradation — a stress test. |
| `recalibrated` | The device *improved* (a fresh calibration). |

## Scoring: how similar are the two distributions?

The primary metric is **Hellinger fidelity** — a 0–1 similarity between the original and reproduced
measurement distributions, where **1.0 means identical**. Secondary metrics give more texture:

- **Total Variation Distance (TVD)** — the largest single probability gap; lower is more similar.
- **Jensen–Shannon divergence** — a symmetric information-theoretic distance.
- **Shot-noise confidence intervals** — whether the difference is within what sampling noise alone explains.

## Verdicts

The fidelity is bucketed into a human verdict so you don't have to interpret a raw number:

| Verdict | Meaning (approx. Hellinger fidelity) |
|---------|--------------------------------------|
| **reproducible** | ≈ identical, within shot noise (≥ 0.99) |
| **drifted** | close but measurably changed (≥ 0.90) |
| **divergent** | noticeably different (≥ 0.70) |
| **irreproducible** | far apart (< 0.70) |

## Reading the diff report

The report explains *why* the distribution changed, not just that it did:

- **Calibration drift** — per-qubit T1 / T2 / readout changes, with percentages.
- **Gate errors** — per-gate error changes (CX, SX, …).
- **Transpilation delta** — changes in compiled-circuit depth, size, and CX count.
- **Backend substitution** — flagged if the reproduction ran on a different device/vendor.
- **Top bitstring shifts** — the measurement outcomes whose probabilities moved the most.

Together these turn "the result changed" into "the result changed *because* qubit 3's readout error
doubled and the transpiler added two CX gates" — an explanation you can act on or cite.

Next: [Result cards & badges](/docs/result-cards-and-badges).
