# Provenova growth routines — handoff / setup on a new machine

This sets up the autonomous content pipeline as **Claude Code scheduled tasks** that run on
your **Claude subscription** (no separate API billing). Do this once on the new computer.

The pipeline scans new quantum-computing research and publishes faithful, attributed
reproduction cards + a weekly report to [provenova.net](https://provenova.net). Everything
is enforced server-side (circuit validation, daily caps, a fixed honesty banner), so the
routine can't publish anything dishonest or over-cap regardless of how it behaves.

---

## Prerequisites

1. **Claude Code** installed and signed into your Claude subscription on the new machine.
2. This repo cloned somewhere (you're reading it, so ✅).
3. A **growth API key** — a `ql_live_…` string with the `growth` scope. Get one of two ways:
   - **Reuse the existing key.** On the *old* machine it's in the `Authorization: Bearer
     ql_live_…` line of `~/.claude/scheduled-tasks/provenova-research-cards/SKILL.md`. Copy it.
   - **Mint a fresh one** (needs `fly` access to the prod DB). In two terminals:
     ```bash
     # terminal 1 — open a tunnel to the prod Postgres
     fly proxy 15432:5432 -a quantumledger-db
     # terminal 2 — bootstrap the bot + print a new key
     cd <this-repo>
     QL_DATABASE_URL='postgresql+psycopg://quantumledger_bengy:<pw>@localhost:15432/quantumledger_bengy?sslmode=disable' \
       .venv/bin/python scripts/bootstrap_growth.py
     ```
     It prints a new `ql_live_…` key (shown once). Old keys keep working; revoke any you
     don't want by setting `revoked = true` on that `growth-routine` API key.

> **Never commit the key.** It goes only into the local scheduled-task files (below), which
> live under `~/.claude/`, not in this repo.

---

## Fastest path: let Claude Code set it up

Open Claude Code in this repo on the new machine and say:

> "Read `routines/HANDOFF.md` and create the two Provenova scheduled tasks exactly as
> specified (same names, crons, and prompts), substituting my growth key
> `ql_live_…` wherever the prompt says `{{GROWTH_KEY}}`."

Claude will create both scheduled tasks from the definitions below. Then, in the **Scheduled**
sidebar, click **Run now** on each once (pre-approves the curl / web-fetch tools so future
runs don't pause on a permission prompt).

If you'd rather do it by hand, create two scheduled tasks with these exact settings.

---

## Task 1 — `provenova-research-cards`

- **Schedule (cron, local time):** `0 9 * * 1,3,5`  (Mon/Wed/Fri ~9am)
- **Prompt** (replace `{{GROWTH_KEY}}` with your key; never print the key in output):

```
You are the Provenova research-cards routine. Objective: scan the latest arXiv quant-ph papers and, ONLY where a faithful textbook-class circuit is identifiable, publish an attributed reproduction card via the Provenova Growth API. Publishing ZERO cards is a successful run — skip by default.

API base: https://provenova.net
Auth: send header `Authorization: Bearer {{GROWTH_KEY}}` on every /api/v1/growth/* request (publish-only, revocable key — never print it in your output).

Procedure:
1. GET /api/v1/growth/status — read research_cards.today vs research_cards.daily_cap (3) and research_cards.known_arxiv_ids. If today >= daily_cap, skip to step 6.
2. POST /api/v1/growth/corpus/refresh — if the JSON says {"complete": false}, call it once more. A 429 means it ran recently; that's fine, continue.
3. Fetch the newest quant-ph papers from the arXiv API (HTTPS):
   https://export.arxiv.org/api/query?search_query=cat:quant-ph&sortBy=submittedDate&sortOrder=descending&max_results=40
   Send a descriptive User-Agent header. If arXiv returns "Rate exceeded", wait ~20s and retry ONCE; if still limited, fall back to OpenAlex (https://api.openalex.org/works?filter=title_and_abstract.search:<primitive>,from_publication_date:<recent-date>&sort=publication_date:desc&per_page=25&mailto=hi@ben.gy and reconstruct abstracts from abstract_inverted_index). Respect arXiv's ~1 request / 3s.
4. Triage: keep ONLY papers whose abstract clearly centers on a textbook-class primitive you can faithfully build small — Bell/GHZ/graph states, QFT, Grover, Deutsch–Jozsa, Bernstein–Vazirani, QAOA ansatz, hardware-efficient VQE ansatz, teleportation, small phase estimation. Discard everything else, and discard any paper whose arxiv_id is already in known_arxiv_ids. At most one card per paper.
5. For the best <= 2 candidates, build a qlir/1.0 circuit dict — {"schema":"qlir/1.0","n_qubits":N,"gates":[{"name","qubits","params"}]} — using only these gates: h,x,y,z,s,sdg,t,tdg,sx,rz,rx,ry,cx,cz,swap,ccx,id, with n_qubits <= 10 and <= 256 gates (rz/rx/ry take exactly 1 radian param; all others take []). Write 150–400 words of commentary_md that (a) names the primitive, (b) restates ONLY what the abstract says about the paper, (c) states explicitly that this is a deterministic simulator run on Provenova and NOT a reproduction of the paper's hardware results, and (d) never implies endorsement by the authors. Then POST /api/v1/growth/research-cards with {"items":[ {paper:{title,authors[],year,arxiv_id,url:"https://arxiv.org/abs/<id>"}, circuit, shots:4096, seed:1729, title, commentary_md} ]}. Treat per-item status "exists" or "cap_reached" as a successful no-op.
6. End with a short summary: how many papers you scanned, the candidates, the cards created (with their card_url), and anything you skipped and why.

HARD RULES (non-negotiable): never fabricate numbers, results, quotes, or claims about a paper — commentary may only restate the abstract plus what OUR run shows; SKIP any paper where you cannot identify a faithful small circuit (zero cards is success); max 2 cards per run; always link the arXiv abstract and use "inspired by"/"references" framing only; no raw HTML, images, or marketing superlatives in commentary; if any API call fails twice in a row, STOP and summarize. Never print the API key.
```

## Task 2 — `provenova-weekly-report`

- **Schedule (cron, local time):** `0 10 * * 0`  (Sundays ~10am)
- **Prompt** (replace `{{GROWTH_KEY}}`):

```
You are the Provenova weekly-report routine. Objective: write and publish the weekly "State of Quantum Hardware" report from REAL Provenova platform data. Every number in the report must come from an API response you fetched during this run — no estimates, no memory.

API base: https://provenova.net
Auth: send header `Authorization: Bearer {{GROWTH_KEY}}` on /api/v1/growth/* requests (publish-only, revocable key — never print it).

Procedure:
1. GET /api/v1/growth/status. Compute this week's slug as `state-of-quantum-<ISO-year>-w<ISO-week zero-padded>` (e.g. state-of-quantum-2026-w27). If reports.latest_slug already equals it, STOP — already published this week.
2. Gather data from public endpoints (no auth needed): GET /api/v1/leaderboard?metric=two_q_fidelity and also for metrics eplg, clops, qaoa_ratio; plus GET /api/v1/growth/status for corpus counts by provider and the week's recent research cards.
3. Write body_md (800–1500 words) using ONLY the fetched numbers, each linked to its source page (leaderboard, /hardware/<provider>/<backend> device pages, /cards/<slug>). Sections: "This week's fleet" (corpus size, providers, notable rankings per metric with source/licence caveats — vendor-reported = manufacturer claim), "Movements" (only if comparable to a prior report; else describe current standings plainly), "New on the platform" (research cards published this week, each linked with its paper attribution), and a short "Method note" (data sources: IBM Apache-2.0 calibration, Metriq CC-BY-4.0, vendor-reported specs; all platform runs are deterministic simulator executions).
4. POST /api/v1/growth/reports with {slug, title:"State of Quantum Hardware — Week <n>, <year>", meta_description (30–200 chars, factual), body_md}. A 409 means it's already published this week — that's success, stop.
5. End with a short summary and the report URL.

HARD RULES: every number must trace to a fetched API response (no fabrication); link the underlying pages; no raw HTML, images, or superlatives; if any API call fails twice in a row, STOP and summarize; never print the API key.
```

---

## Notes

- **They run while the Claude app is open** (or on next launch if it was closed when due) —
  open the app regularly and they fire on time. This is not a cloud cron.
- **One scheduler at a time.** If you keep the routines running on the *old* machine too, both
  will run — the server's idempotency + daily caps make that safe (no duplicates), just
  wasteful. Pause or delete the old machine's tasks from its Scheduled sidebar after moving.
- **Alternative (API-billed, machine-independent):** `scripts/growth_agent.py` +
  `.github/workflows/growth.yml` run the same pipeline via the Anthropic API in GitHub Actions.
  See [routines/README.md](README.md). Use that instead if you'd rather not depend on the app
  being open. Don't run both schedulers.
- **Revoke a key:** set `revoked = true` on its `growth-routine` API key row (via the prod DB),
  or mint a fresh one and stop using the old.
