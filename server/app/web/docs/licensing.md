# Licensing

Provenova is **open-core**. One monorepo, two licenses, one rule of thumb: *the code is free; the
trust network is the product.* This page explains exactly what that means — with examples — so you
never have to parse license legalese to know where you stand.

## The split

| Component | Package / dir | License |
|---|---|---|
| SDK, CLI & vendor connectors | [`provenova`](https://pypi.org/project/provenova/) · `packages/ql-sdk` | Apache-2.0 |
| Provenance engine (hashing, immutability, reproduce, `qlprov` schemas) | [`provenova-core`](https://pypi.org/project/provenova-core/) · `packages/ql-core` | Apache-2.0 |
| Calibration crawler & public corpus tooling | [`provenova-crawler`](https://pypi.org/project/provenova-crawler/) · `packages/ql-crawler` | Apache-2.0 |
| Compliance frameworks (FAIR, IEEE P7131, …, as data) | `frameworks/` | Apache-2.0 |
| Server (web app, API, cards, compliance engine, attestations, admin) | `server/` | BUSL-1.1 |

Everything lives in one public repo: [github.com/ben-gy/provenova](https://github.com/ben-gy/provenova).
Each directory carries its own `LICENSE` file.

**Apache-2.0** is plain open source: use, modify, embed, redistribute, commercially or not. The
provenance format your results live in, and every tool that reads or writes it, is permanently open
— your data is never hostage to us.

**BUSL-1.1** (the [Business Source License](https://mariadb.com/bsl11/), used by MariaDB and
HashiCorp) means the server source is public and free to run — including in production — with one
reservation, spelled out below. And it's time-limited: **each release automatically becomes
Apache-2.0 four years after it ships** (this release converts on 2030-07-03). Provenova can't lock
the server down retroactively even if it wanted to.

## What you can do

- **Self-host in production, free.** Your lab runs `docker compose up -d` in `deploy/` and uses
  the full platform — dashboard, compliance engine, attestations, everything. No license key, no
  feature gates, no time limit. This is the intended use, not a loophole.
- **Run it for your whole organization.** A company or university operating one instance for all
  its own teams, departments and affiliates is squarely inside the grant.
- **Host it for an academic collaboration.** A national quantum hub running one instance for its
  member labs, on a non-commercial basis, is explicitly allowed — the grant carves this in.
- **Run it air-gapped.** Set `QL_PUBLIC_CARDS=false` and nothing leaves your network. See
  [Deployment & self-hosting](/docs/deployment).
- **Fork the SDK, build on the format, ship products on top.** All Apache-2.0. A QPU vendor can
  bundle the *client* in their tooling today, no permission needed.

## What you can't do

- **Offer Provenova as a service.** You can't take the server and sell (or give away) hosted
  Provenova to third parties — "QuantumProvenance Cloud, powered by our fork" is exactly what the
  license reserves until the code converts to Apache-2.0.
- **Embed the server in a competing product.** A vendor console that ships the server's
  functionality to its customers is an offering to third parties, hosted or not.

If your use case is genuinely ambiguous, or your procurement team can't accept BUSL at all,
[email us](mailto:hi@ben.gy?subject=Provenova%20commercial%20license) — commercial licenses with
conventional terms exist for exactly this.

## Why this model?

An attestation is like an audit report: its value is not the PDF, it's *who signed it*. You can
self-host the entire signing machinery, but an attestation signed by your own key proves nothing
to a journal reviewer or a procurement office — just as a company can't self-issue its SOC 2. What
paid tiers buy is the part that can't be forked: attestations that verify against provenova.net,
a public [Trust Center](/docs/compliance), verified keys for self-hosted instances, and the
public corpus behind the [leaderboard](/hardware). Keeping the code open costs us nothing we were
selling — and it means you can audit the ledger you're trusting, which for a provenance product
is rather the point.

See also: [Pricing FAQ](/docs/pricing-faq) · [Deployment & self-hosting](/docs/deployment) ·
[Libraries & downloads](/docs/libraries)
