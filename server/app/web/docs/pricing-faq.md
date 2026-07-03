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

Every published result card already gets a **free, citable internal PID** on every tier — that never
costs anything. External **DOIs are minted via DataCite** when the server is configured with
DataCite credentials, metered per month (Free: 5/month; unlimited on Academic and paid tiers).
Hitting the cap never blocks publishing — the card simply keeps its always-free PID.

## Can I self-host?

Yes — clone the repo and use the docker-compose in `deploy/`; a single-node SQLite deployment works
out of the box. See [Deployment & self-hosting](/docs/deployment). The **Lab** tier adds a
**self-hostable attestation signing service**, SSO/SAML and a public **Trust Center** on top of
self-hosting, which is what most labs actually need for auditable, signed artifacts.

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
