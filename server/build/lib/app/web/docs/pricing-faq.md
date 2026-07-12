# Pricing FAQ

Straight answers to the questions we get most about tiers, caps and what's free.

## What do I get on the Free plan?

The whole core loop, forever, at no cost:

- **Capture, reproduce and benchmark** runs — including the **Benchmark vs fleet** action that
  lights the **Benchmarked** badge. Every rung up to **Compliant** is earnable on Free; the top
  **Audit-ready** rung needs a signed attestation, included from Academic (free for verified
  academic domains) and up.
- **Unlimited public result cards and badges** — publishing is never capped.
- **250 private records** — private by default, capped by *volume*, not by privacy.
- **A FAIR compliance checklist** — enable the FAIR framework, evaluate it and watch your completion
  percentage. (Issuing a *signed* attestation requires the Academic plan — free for verified
  academic domains — or a paid tier; see below.)
- **Full `qlprov` export** — the open, portable provenance format is never gated, on any tier.

## What happens when I hit the 250 private-record cap?

Nothing is deleted, ever. When a workspace reaches its private-record cap:

- Existing records stay fully readable, exportable and reproducible.
- **Publishing a record to a public card still works** — and it frees a private slot, because public
  cards don't count against the private cap. So you're never blocked from sharing results.
- To capture *new private* records beyond the cap, publish some existing ones or move to a plan with
  an unlimited private cap (Academic and up).

A usage meter ("187 / 250 private records") is shown on your dashboard so there are no surprises.

## Is fleet comparison really free?

Yes. Fleet comparison — the **Benchmark vs fleet** button on a record — is available on Free. It
scores a run by the Hellinger fidelity of its measured distribution against the noiseless ideal for
the same circuit, ranks it within your workspace benchmark, and records the `BenchmarkEntry` that
earns the **Benchmarked** badge. Every rung up to **Compliant** is reachable on Free;
**Audit-ready** additionally needs a signed attestation (Academic and up).

## Compliance — what's free vs. paid?

- **Free** includes a **read-only FAIR view**: enable FAIR, see the evidence checklist and your
  completion percentage. The "Issue attestation" button is shown but locked, with an upgrade link.
- **Academic and up** can **issue signed, verifiable attestations** and enable **all frameworks**
  (Team is capped at 10 concurrently; Academic, Lab and Enterprise are unlimited).
- **Team and up** add **continuous monitoring & alerts**; **Lab and up** add a public **Trust Center**.

## Do you issue DOIs?

Every published result card gets a **free, citable persistent identifier (PID)** on every tier — no
network, no cost, works self-hosted and air-gapped. If the server is configured with a **Zenodo**
token, you can also **mint a real, free DOI** with one click: Provenova archives the run's provenance
record on Zenodo and stores the resulting DOI on the card. DOIs are metered per month (Free: 5/month;
unlimited on Academic and paid tiers) and, being permanent, are minted only by the explicit action —
never automatically. Hitting the cap never blocks publishing; the card keeps its always-free PID.
(A DataCite path also exists for operators who have their own membership, but it's off by default.)

## Is Provenova open source?

The client side is: the SDK, the vendor connectors, the reproduce engine, the calibration crawler
and the `qlprov` provenance format are all **Apache-2.0** — fork them, embed them, ship them. The
server is **source-available** under the Business Source License (BUSL-1.1): the code is public,
you can run all of it, and every release automatically becomes Apache-2.0 four years after it
ships. The only thing the license reserves is offering the server itself to third parties as a
hosted, managed, or embedded service or product — paid or free. The full breakdown, with examples
of what you can and can't do, is on the [Licensing](/docs/licensing) page.

## Can I self-host?

Yes — free, production included, no license key, no feature gates. Clone
[the repo](https://github.com/ben-gy/provenova) and use the docker-compose in `deploy/`; a
single-node SQLite deployment works out of the box. See
[Deployment & self-hosting](/docs/deployment).

What you can't self-host is *trust*. Attestations are only as credible as the key that signs
them, and a self-hosted instance signs with its own key — which third parties have no reason to
believe. That's what the paid tiers are actually about: the **Lab** tier's self-hostable signing
service comes with **verified keys** — your instance's public key is registered in Provenova's
trust directory, so attestations you sign verify against provenova.net — plus SSO/SAML and a
public **Trust Center**. Run it yourself for free; pay us when you need the world to believe it.

## How does academic verification work?

Sign up with an email at a recognised academic domain — `.edu`, `.ac.<country>` (e.g. `.ac.uk`),
`.edu.<country>` (e.g. `.edu.au`), and common `uni-*`/`univ-*` institution domains worldwide — and
your workspace is automatically granted the **Academic** plan (free) once your email is verified. If
your institution isn't recognised, [email us](mailto:hi@ben.gy?subject=Provenova%20academic%20verification)
and we'll add it.

## How do I pay / upgrade?

Paid tiers are **provisioned by our team** — there's no self-serve checkout. Pick a tier on the
[pricing page](/pricing), hit **Request access**, and we'll enable it on your workspace (invoicing is
handled off-platform). Prices are indicative; for volume, academic or air-gapped terms just
[talk to us](mailto:hi@ben.gy?subject=Provenova%20pricing).
