# Result cards & badges

A **Result Card** is a public, citable page for a run — the shareable face of an otherwise internal record.
Badges are the embeddable shields that advertise a result's maturity.

## Publishing a card

From a run at `/app/records/<run_id>`, use **Publish** to create a Result Card. A card has:

- a **slug** and public URL (`/cards/<slug>`),
- a **title** and summary,
- the run's **provenance** (backend, calibration, `run_hash`),
- the **result distribution**,
- **visibility** — `private`, `org`, or `public`,
- an optional **DOI** (or other persistent identifier) and license.

Cards can be unpublished (retracted) later. On a self-hosted install you can disable public cards entirely
(`QL_PUBLIC_CARDS=false`) for a VPC/air-gapped deployment, keeping cards and badges internal.

## Citing a result

Because a card carries a persistent identifier and an immutable `run_hash`, it can be cited unambiguously.
The card page and API offer ready-made citations:

- `GET /api/v1/cards/<slug>/citation` — **BibTeX** and **RIS**.
- `GET /api/v1/cards/<slug>` — machine-readable metadata (JSON).
- `GET /api/v1/cards/<slug>/embed` — an embeddable HTML snippet.

Minting a DOI/PID for a card is also what satisfies several FAIR compliance controls — see
[Compliance](/docs/compliance).

## Badges

Badges are shields.io-style images you can drop into a README, paper, or dashboard:

- `GET /badge/<slug>/<type>.svg` — the badge as SVG.
- `GET /badge/<slug>/<type>.json` — the same data as JSON (for automation).

### The badge ladder

Badges reflect a result's maturity as a ladder — each rung is a stronger claim than the last:

1. **Recorded** — the run is in the ledger with full provenance.
2. **Reproduced** — it has been re-run and scored.
3. **Benchmarked** — compared across backends/vendors via **Compare vs. the fleet**.
4. **Compliant** — it satisfies an enabled compliance framework.
5. **Audit-ready** — backed by a signed attestation.

Rungs 1–3 are reachable on the **Free** plan for your own runs — capture, reproduce, then hit
**Compare vs. the fleet** on the record page to record a benchmark entry and light the
**Benchmarked** badge, no upgrade required. Rung 4 needs an enabled framework and rung 5 needs a
signed attestation (a paid feature — see the [pricing FAQ](/docs/pricing-faq)).

External parties can even submit their own reproductions of a public card
(`POST /api/v1/cards/<slug>/reproductions`), feeding independent verification into the leaderboard.

Next: [Compliance & attestations](/docs/compliance).
