# Hosting QuantumLedger on Fly.io

Fly builds and runs `deploy/Dockerfile` directly, terminates TLS at its edge (no
Caddy needed), and scales the web machine to zero when idle. Config lives in
`fly.toml` at the repo root.

## What you do (interactive, one-time)

```bash
brew install flyctl
fly auth login          # opens a browser; create the account + add a card
```

Then tell Claude "authed" — everything below is CLI-driven and Claude runs it.

## What Claude runs (after you're authed)

1. **App:** `fly apps create quantumledger` (global name — a suffix is added if taken;
   `fly.toml`'s `app =` is updated to match).
2. **Database — pick one:**
   - *Fly Postgres (all-in-Fly):* `fly postgres create` (a small single-node cluster),
     then the connection string is set as the `QL_DATABASE_URL` secret as
     `postgresql+psycopg://…`.
   - *Neon (free):* create a project at neon.tech, copy the pooled connection string,
     and Claude sets it as `QL_DATABASE_URL` (`postgresql+psycopg://…?sslmode=require`).
3. **Secrets:**
   ```bash
   fly secrets set \
     QL_SECRET_KEY=$(openssl rand -hex 32) \
     QL_ATTESTATION_KEY_B64="$(fly ... gen key)" \
     QL_DATABASE_URL="postgresql+psycopg://…" \
     QL_BASE_URL="https://quantumledger.ben.gy"
   ```
   `QL_ATTESTATION_KEY_B64` is generated once with `scripts/gen_attestation_key.py`
   (via the built image) and **kept forever** — rotating it invalidates prior
   attestations.
4. **Deploy:** `fly deploy` (builds the ARM/amd64 image on Fly's builders, boots the app,
   runs startup bootstrap against Postgres).
5. **Custom domain:** `fly certs add quantumledger.ben.gy`, then a Cloudflare record
   (CNAME → `quantumledger.fly.dev`, or the A/AAAA Fly prints) — Claude sets the DNS via
   its Cloudflare skill. Fly provisions the TLS cert automatically.
6. **Seed demo (optional):** `fly ssh console -C "python /app/scripts/seed_demo.py"`.
7. **Verify:** `curl https://quantumledger.ben.gy/api/v1/health`.

## Notes
- The background worker (crawler/monitor) is omitted initially (fixture-driven, low
  urgency). It can be added later as a second Fly `[processes]` entry, or run on a
  schedule.
- `fly logs` to tail; `fly status` for machine state; `fly deploy` to redeploy.
- Scale-to-zero means the first request after idle has a cold start (a few seconds).
  Set `min_machines_running = 1` in `fly.toml` if you want it always warm.
