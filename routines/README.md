# Provenova growth routines

The autonomous content pipeline that scans new quantum-computing research and publishes
faithful, attributed reproduction cards + a weekly report to [provenova.net](https://provenova.net).

> **Setting this up on a new computer?** See **[HANDOFF.md](HANDOFF.md)** — a self-contained
> guide with the ready-to-paste routine prompts for the Claude-subscription path.

There are **two ways to run it** — pick one (don't run both, or you double the work; the
server's idempotency + daily caps keep it safe either way):

## 1. Portable agent — `scripts/growth_agent.py` (recommended, runs anywhere)

A self-contained Python CLI that uses the Anthropic API for the editorial judgement and
`httpx` for everything else. No Claude Code, no always-on server — runs on any machine or
in CI.

```bash
pip install -r scripts/requirements-agent.txt
export PROVENOVA_GROWTH_KEY=ql_live_…      # the growth-scoped API key
export ANTHROPIC_API_KEY=sk-ant-…          # billed to your Anthropic account
python scripts/growth_agent.py research-cards   # or: weekly-report, status
```

**Honesty by construction:** paper metadata (arXiv id, DOI, authors, URL) is fetched
deterministically and merged back by index — the model only decides *which* papers warrant
a card and *what* faithful textbook circuit to run, so it can never fabricate a citation.
Every card records a real deterministic simulator run; the server re-validates the circuit
and re-enforces all caps regardless of what the script sends.

Env vars: `PROVENOVA_BASE_URL` (default `https://provenova.net`),
`PROVENOVA_AGENT_MODEL` (default `claude-opus-4-8`),
`PROVENOVA_MAX_CARDS` (default 2; server hard-caps at 3/day),
`OPENALEX_MAILTO` (default `hi@ben.gy`).

### Scheduling via GitHub Actions (free, hands-off)

`.github/workflows/growth.yml` runs it on a cron: **research-cards Mon/Wed/Fri 21:00 UTC**,
**weekly-report Sun 22:00 UTC**, plus a manual "Run workflow" button. Set two repo secrets
(Settings → Secrets and variables → Actions):

- `PROVENOVA_GROWTH_KEY` — the `ql_live_…` key with the `growth` scope
- `ANTHROPIC_API_KEY` — an Anthropic API key

Then use **Run workflow → research-cards** once to smoke-test before the schedule kicks in.

Any other cron works too (crontab, systemd timer, a cheap VM) — it's just two commands.

## 2. Local Claude Code scheduled tasks

`growth-research-cards.md` and `growth-weekly-report.md` are the prompt files for the
Claude Code desktop app's scheduled tasks (Scheduled sidebar). These run **while the app is
open** (or on next launch) and hold the growth key in the local task file. Prefer option 1
for reliable, machine-independent scheduling.

## One-time bootstrap

The growth-scoped API key is minted by `scripts/bootstrap_growth.py` (run once against the
prod DB over a `fly proxy` tunnel). It creates the `ql-research` bot org + workspace and
prints a `ql_live_…` key. Revoke it anytime by setting `revoked = true` on the
`growth-routine` API key.
