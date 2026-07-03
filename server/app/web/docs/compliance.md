# Compliance & attestations

Compliance in Provenova is "Vanta for quantum": you pick a standard, and the evidence is **collected
automatically from the runs already in your ledger**. You don't upload documents or fill in checklists —
your records *are* the evidence. The console lives at `/app/compliance` (available on every plan —
Free is limited to the FAIR framework; issuing signed attestations requires Academic or a paid plan).

## The model: frameworks → controls → evidence rules

- A **framework** is a standard expressed as data — FAIR, IEEE P7131, a metrology policy, an internal
  reproducibility policy. Framework definitions are plain YAML under `frameworks/`, loaded at startup.
- A framework contains **controls**. A control is one requirement, e.g. *"every published result has a
  persistent identifier."* Each control carries human-readable `requirement_text`, a `severity`
  (`high`/`medium`), and `remediation` guidance.
- A control is checked by one or more **evidence rules** — machine-checkable predicates evaluated against
  your runs, results, cards, calibrations, and reproductions. A rule can require a value to exist, match a
  set, or count matching items, and can demand the evidence be *fresh* (within a time window).

Because frameworks are pure data, the rule engine is the *only* code that interprets them — adding a
standard means adding a YAML file, not writing code. The live definitions are rendered on the
[Frameworks reference](/docs/frameworks) page.

## Evaluating

Click **Evaluate all** (or **Re-evaluate** on a single framework). This is **safe to run any time** and is
idempotent — it simply recomputes status against your current runs. For each control the engine:

1. finds candidate records (runs, cards, calibrations, …),
2. tests each evidence rule's predicate and freshness window,
3. records an **evidence item** for every match — a pointer back into your immutable record plus the
   target's content hash,
4. rolls the result up: a control **passes** when all its rules are satisfied; a framework passes when all
   its controls pass.

## Reading the result — statuses

| Status | Meaning |
|--------|---------|
| **pass** | Every control has the evidence it needs. |
| **gap** | At least one control is missing required evidence. |
| **unknown** | Not evaluated yet. |

Separately, a **drift alert** fires when a rule's evidence exists but has aged out of its freshness
window. Stale items stop counting toward the rule, so if a control no longer has enough fresh evidence
it reports **gap** until the evidence is refreshed.

## Understanding a gap

A **gap** is a failing control — and the framework detail page tells you exactly why. Open a framework
(**View controls & gaps**) to see, per control:

- a ✓/✗ status and the control's `requirement_text` ("what the standard requires"),
- each **check** in plain English, with the failing ones marked ✗,
- a highlighted **"how to fix it"** block with the control's remediation steps,
- the **evidence collected so far**, with its source and content hash.

So "gap" is never a dead end: you can see which check failed, what the standard wanted, and the concrete
action that closes it. Fix the underlying data (e.g. mint a DOI, publish a card, refresh a calibration),
then **Re-evaluate**.

## Attestations {#attestations}

When a framework passes, you can issue an **attestation** — a cryptographically signed claim that its
controls were satisfied at a point in time.

- The server computes an **evidence root**: a Merkle root over all the evidence items the attestation
  covers.
- It signs that root with an **Ed25519** key, producing a signed statement stored with the workspace.
- Anyone can verify it: `GET /api/v1/attestations/<id>/verify`, using the public keys published at
  `/.well-known/quantumledger-jwks.json`.

Two properties make attestations trustworthy:

- **Tamper-evident.** The signature is over hashes that point into your immutable records. Mutate any
  referenced run, card, or calibration and verification **fails** — you can't quietly change the evidence
  after attesting.
- **Revocable.** Revoking an attestation (`POST /api/v1/attestations/<id>/revoke`) makes it stop validating.

Published attestations also surface on your public **Trust Center** (`/trust/<org>`), so partners can check
your compliance posture without asking you.

## Frameworks that ship

FAIR (data-sharing principles), IEEE P7131 (quantum computing reproducibility), a metrology/traceability
policy, and an internal reproducibility policy. See every control, check, and remediation on the live
[Frameworks reference](/docs/frameworks).

Next: [Corpus & leaderboard](/docs/corpus-and-leaderboard).
