# Result cards & badges

A **Result Card** is a public, citable page for a run — the shareable face of an otherwise internal record.
Badges are the embeddable shields that advertise a result's maturity.

## Publishing a card

From a run at `/app/records/<run_id>`, use **Publish** to create a Result Card. A card has:

- a **slug** and public URL (`/cards/<slug>`),
- a **title** and summary,
- the run's **provenance** (backend, calibration, `run_hash`),
- the **result distribution**,
- **visibility** — `private` or `public`,
- a free **persistent identifier (PID)**, an optional real **DOI** minted on demand via Zenodo, and a license.

Cards can be unpublished (retracted) later.

## Citing a result

Because a card carries a persistent identifier and an immutable `run_hash`, it can be cited unambiguously.
The card page and API offer ready-made citations:

- `GET /api/v1/cards/<slug>/citation?format=<bibtex|csl|ris>` — **BibTeX** (default), **CSL JSON**, or **RIS**.
- `GET /api/v1/cards/<slug>` — machine-readable metadata (JSON).
- `GET /api/v1/cards/<slug>/embed` — JSON of copy-paste snippets (a Markdown/HTML badge and an iframe tag).
- `GET /cards/<slug>/embed.html` — the self-contained, iframe-embeddable Result Card widget itself (served
  with `frame-ancestors *`), also discoverable via oEmbed at `GET /api/v1/oembed`.

A card's free **PID** already satisfies the FAIR persistent-identifier control (FAIR-F1); an optional
**DOI** — minted for free on demand via Zenodo, which also archives the provenance record — strengthens
it with an external, globally-resolvable identifier. See [Compliance](/docs/compliance).

## Badges

Badges are shields.io-style images you can drop into a README, paper, or dashboard:

- `GET /badge/<slug>/<type>.svg` — the badge as SVG.
- `GET /badge/<slug>/<type>.json` — the same data as JSON (for automation).

### The badge ladder

Badges reflect a result's maturity as a ladder — each rung is a stronger claim than the last:

1. **Recorded** — the run is in the ledger with full provenance.
2. **Reproduced** — it has been re-run and scored.
3. **Benchmarked** — scored against the noiseless ideal and ranked within your workspace via
   **Benchmark vs fleet**.
4. **Compliant** — it satisfies an enabled compliance framework.
5. **Audit-ready** — backed by a signed attestation.

Rungs 1–3 are reachable on the **Free** plan for your own runs — capture, reproduce, then hit
**Benchmark vs fleet** on the record page to record a benchmark entry and light the
**Benchmarked** badge, no upgrade required. Rung 4 needs an enabled framework and rung 5 needs a
signed attestation (a paid feature — see the [pricing FAQ](/docs/pricing-faq)).

External parties can even submit their own reproductions of a public card
(`POST /api/v1/cards/<slug>/reproductions`) — independent verification that upgrades the card's
**Reproduced** badge.

Next: [Compliance & attestations](/docs/compliance).
