# Product tour

A screen-by-screen guide to the main pages of the web app and what each button does. The top navigation
bar is always present: **Tracker · Hardware · Leaderboard · Records · Compliance · Docs** (plus
**Admin** for superadmins), with your account on the right. The logo takes you home; the Trust Center
is linked from the footer.

## Home / dashboard (`/`)

Signed out, this is a landing page. Signed in, it's your dashboard: recent runs and summary stats (run
count, reproductions, published cards). Use it as a jumping-off point to Records or Compliance.

## Records (`/app/records`)

A table of every run in your workspace — status, backend, shots, and created time. Click a row to open its
detail. If the table is empty, you haven't captured or pushed any runs yet; see
[Capturing runs](/docs/capturing-runs).

### Record detail (`/app/records/<run_id>`)

The full provenance of one run:

- **Provenance** — circuit, backend, calibration snapshot, and the `run_hash` / chain hashes. This is the
  Merkle-bound, offline-verifiable identity of the run.
- **Result distribution** — the measured bitstring counts.
- **Reproduce** (form + button) — choose *days to drift* and a *profile* (`typical` / `bad_day` /
  `recalibrated`) and submit to re-run the circuit against a drifted device state. See
  [Reproduce & drift](/docs/reproduce-and-drift).
- **Reproduction report** — after reproducing: the Hellinger-fidelity score, the verdict, and the diff
  (calibration drift, transpilation delta, top bitstring shifts).
- **Benchmark vs fleet** (button) — score the run's distribution against the noiseless ideal and the
  public fleet, earning the **Benchmarked** badge.
- **Publish** (button) — turn this run into a public [Result Card](/docs/result-cards-and-badges).

## Tracker (`/tracker`)

Plain-English, long-run trends in quantum computing from public, cited data: published experiments, IBM's
cloud fleet calibration history, independent benchmarks and resource estimates. Every figure carries an
evidence label and links to its source; the data behind it is at `/api/v1/tracker`.

## Hardware (`/hardware`) and leaderboard (`/leaderboard`)

One page per device (a plain-English summary, every metric with its date, source and fleet comparison,
and its history) and a cross-vendor ranking on a metric you can switch (median 2Q error, readout error,
T1/T2, Quantum Volume, CLOPS, EPLG and more). Backed by the
[public corpus](/docs/corpus-and-leaderboard).

## Compliance (`/app/compliance`)

The compliance console — every framework and signed attestations, available to every account. A "How it works" panel and a status legend sit at the top,
followed by a card per framework.

- **Enable** — turn a framework on for your workspace and evaluate it for the first time.
- **Evaluate all** — recompute every enabled framework against your current runs. Safe to run repeatedly.
- **Re-evaluate** (per framework) — recompute just that one.
- **View controls & gaps** — open the framework detail page.
- **Attest** — appears when a framework passes; issues a signed attestation.

### Framework detail (`/app/compliance/frameworks/<id>`)

The drill-down: an overall pass/gap rollup with a progress bar, then every control with its ✓/✗ status,
what the standard requires, each check in plain English (failing ones marked), remediation for gaps, and the
evidence collected (with source and content hash). Full concepts in [Compliance](/docs/compliance).

### Attestations table

Below the frameworks: every attestation you've issued, its evidence root, active/revoked status, and a
**Verify** link that checks the signature.

## Trust Center (`/trust/<org>`)

A **public** page for an organization showing its framework statuses and active, signed attestations —
so partners can verify your compliance posture without contacting you. Revoked attestations stop appearing.

## Result card (`/cards/<slug>`)

The public face of a published run: title, summary, provenance, distribution, badges, an embeddable card
widget, and citation formats (BibTeX, CSL JSON, RIS). See [Result cards & badges](/docs/result-cards-and-badges).

## Settings (`/app/settings`)

Your account: profile and password, two-factor authentication (TOTP — set up via QR code), and **API keys**
for pushing runs from the SDK (`ql login --token …`). Generate a key here, copy it once, and store it safely.

## Admin (`/app/admin`, superadmin only)

Organization overview: every org on the instance, with a link to its public Trust Center.

## Docs (`/docs`)

This documentation. The left sidebar groups every page; reference pages ([CLI](/docs/cli),
[API](/docs/api), [Frameworks](/docs/frameworks)) are generated live from the running system.

Next: [Libraries & downloads](/docs/libraries).
