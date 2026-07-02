# Product tour

A screen-by-screen guide to every page in the web app and what each button does. The top navigation bar is
always present: **Home · Records · State of Quantum Hardware · Compliance · Trust Center · Docs** (plus
**Admin** for superadmins), with your account and plan on the right.

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
- **Publish** (button) — turn this run into a public [Result Card](/docs/result-cards-and-badges).

## State of Quantum Hardware (`/leaderboard`)

The public cross-vendor leaderboard, ranking devices by a calibration metric you can switch (2-qubit error,
T1, T2, readout fidelity). Backed by the [public corpus](/docs/corpus-and-leaderboard).

## Compliance (`/app/compliance`)

The compliance console (Pro+). A "How it works" panel and a status legend sit at the top, followed by a card
per framework.

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

The public face of a published run: title, summary, provenance, distribution, badges, an embed snippet, and
citation formats (BibTeX/RIS). See [Result cards & badges](/docs/result-cards-and-badges).

## Settings (`/app/settings`)

Your account: profile and password, two-factor authentication (TOTP — set up via QR code), and **API keys**
for pushing runs from the SDK (`ql login --token …`). Generate a key here, copy it once, and store it safely.

## Admin (`/app/admin`, superadmin only)

Organization management: view every org and its effective plan, and change plans (upgrade/downgrade).

## Docs (`/docs`)

This documentation. The left sidebar groups every page; reference pages ([CLI](/docs/cli),
[API](/docs/api), [Frameworks](/docs/frameworks)) are generated live from the running system.

Next: [Libraries & downloads](/docs/libraries).
