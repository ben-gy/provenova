# Hosting QuantumLedger on Oracle Cloud (Always Free, ~$0/mo)

A step-by-step runbook to run the **entire** QuantumLedger stack — Postgres, the
API/web app, the background worker, and a Caddy reverse proxy that provisions
TLS automatically — on a single Oracle Cloud "Always Free" Ampere A1 VM.

This guide assumes you already have:

- A clone of the repo (`quantumledger`) available locally or ready to `git clone` on the VM.
- A domain on Cloudflare. Examples use **`quantumledger.ben.gy`**; substitute your own.
- An SSH key pair (`~/.ssh/id_ed25519` / `~/.ssh/id_ed25519.pub` or similar).

Referenced artifacts in this repo: `deploy/docker-compose.yml`,
`deploy/Dockerfile`, `deploy/Caddyfile`, `deploy/.env.example`,
`deploy/oracle-setup.sh`, `scripts/gen_attestation_key.py`, `scripts/seed_demo.py`.

---

## 1. What you get

One VM.Standard.A1.Flex (ARM/aarch64) instance running four containers via Docker
Compose:

| Container | Image | Role |
|-----------|-------|------|
| `postgres` | `postgres:16` | Durable database (named volume `pgdata`) |
| `api` | built from `deploy/Dockerfile` | FastAPI web app + API (`uvicorn app.main:app`) |
| `worker` | same image | Background jobs (`python -m app.worker`) |
| `proxy` | `caddy:2` | Reverse proxy + automatic Let's Encrypt TLS |

The API is **not** exposed to the host directly — only Caddy publishes ports 80/443,
and it reverse-proxies to `api:8000` on the internal Docker network.

Oracle's Always Free tier grants up to **4 OCPU / 24 GB RAM** of Ampere A1 capacity
plus 200 GB of block storage at no cost, indefinitely. A 1 OCPU / 6 GB slice is more
than enough for this stack, so the running cost is effectively **$0/mo**.

The build uses aarch64 `qiskit` wheels and intentionally omits `qiskit-aer` (the
reproduce engine defaults to a pure-Python simulator), so there is **no heavy native
compile** on ARM.

---

## 2. Create the VM (Oracle Cloud console)

1. Sign up for **Oracle Cloud** at <https://www.oracle.com/cloud/free/> and complete the
   Always Free registration (requires a card for identity verification; Always Free
   resources are not charged).
2. In the console: **Menu → Compute → Instances → Create instance**.
3. Configure:
   - **Image:** Canonical **Ubuntu 22.04**.
   - **Shape:** click *Change shape* → **Ampere** → **VM.Standard.A1.Flex**.
     Set **1 OCPU / 6 GB** memory (you may go up to 4 OCPU / 24 GB total across your
     Always Free Ampere allocation).
   - **Add SSH keys:** *Paste public keys* and paste the contents of your public key:
     ```bash
     cat ~/.ssh/id_ed25519.pub
     ```
   - Leave the default VCN/subnet (a new one is created for you).
4. Click **Create**. When it reaches *Running*, note the **Public IP address** shown on
   the instance details page.

> **Capacity note:** ARM (A1) capacity is frequently scarce. If you see
> **"Out of host capacity"** / **"Out of capacity for shape VM.Standard.A1.Flex"**,
> retry — ideally switching the **Availability Domain** (AD-1 / AD-2 / AD-3) in the
> create dialog, and retry over the following minutes/hours. Capacity frees up
> continuously.

---

## 3. Open the network (ingress)

The VM's subnet has a **Security List** (a virtual firewall) that must allow inbound
80/443 before Caddy can serve traffic or complete the TLS challenge. The **in-VM
`iptables`** rules are opened automatically by `deploy/oracle-setup.sh` in step 5 —
you only need to handle the cloud-side Security List here.

1. Instance page → **Primary VNIC → Subnet** (click the subnet link).
2. Open the subnet's **Default Security List → Add Ingress Rules**.
3. Add two rules (leave Stateless unchecked):

   | Source CIDR | IP Protocol | Destination Port |
   |-------------|-------------|------------------|
   | `0.0.0.0/0` | TCP | `80` |
   | `0.0.0.0/0` | TCP | `443` |

4. Leave the existing **TCP 22 (SSH)** rule in place. For safety, tighten its source
   from `0.0.0.0/0` to your own IP (e.g. `203.0.113.5/32`).

---

## 4. DNS (Cloudflare)

Point your hostname at the VM's public IP. Use a **DNS-only (grey cloud)** record at
first so Caddy can complete the Let's Encrypt **HTTP-01** challenge on port 80.

1. Cloudflare dashboard → **ben.gy** → **DNS → Records → Add record**:
   - **Type:** `A`
   - **Name:** `quantumledger`
   - **IPv4 address:** `<VM public IP>`
   - **Proxy status:** **DNS only** (grey cloud)
   - **TTL:** Auto
2. Verify propagation:
   ```bash
   dig +short quantumledger.ben.gy
   ```
   It should print the VM's public IP.

> **Claude can do this for you.** Once you have the public IP, ask Claude to
> "set the Cloudflare DNS A record for quantumledger.ben.gy to `<IP>`, DNS-only" — it
> uses its Cloudflare skill and does not require you to re-enter an API token.

> **Switching to proxied (orange cloud) later:** after the first certificate is issued,
> you can enable Cloudflare's proxy for DDoS protection and caching. To keep TLS working
> end-to-end, install a **Cloudflare Origin Certificate** on the VM (and set the SSL/TLS
> mode to **Full (strict)**). Do this *after* the initial issuance; leaving the record
> grey-clouded also works perfectly well.

---

## 5. Deploy

SSH in, run the one-time host setup, fill secrets, and bring the stack up.

```bash
# From your workstation
ssh ubuntu@<VM public IP>
```

Clone the repo on the VM (or `scp -r` your local copy):

```bash
git clone <your-quantumledger-remote> quantumledger
cd quantumledger
```

Run the host bootstrap (installs Docker + the Compose plugin, opens the in-VM
`iptables` for 80/443, and adds `ubuntu` to the `docker` group):

```bash
bash deploy/oracle-setup.sh
```

Re-login so your shell picks up the `docker` group membership (otherwise `docker`
commands need `sudo`):

```bash
exit          # then ssh back in
# — or, without logging out —
newgrp docker
```

Create and fill the environment file:

```bash
cp deploy/.env.example deploy/.env
```

Generate the two random secrets now (the attestation key comes in the build step below,
straight out of the image). **`QL_ATTESTATION_KEY_B64` must be generated once and then
kept forever** (see the warning below):

```bash
openssl rand -hex 32   # -> QL_SECRET_KEY   (session signing)
openssl rand -hex 16   # -> POSTGRES_PASSWORD
```

Edit `deploy/.env` and set the values compose consumes:

```bash
nano deploy/.env
```

```ini
QL_DOMAIN=quantumledger.ben.gy
QL_SECRET_KEY=<output of `openssl rand -hex 32`>
POSTGRES_PASSWORD=<output of `openssl rand -hex 16`>
QL_ATTESTATION_KEY_B64=<output of gen_attestation_key.py>
QL_PUBLIC_CARDS=true
QL_ADMIN_EMAIL=hi@ben.gy
```

> `QL_BASE_URL` and `QL_DATABASE_URL` are **derived automatically** in
> `docker-compose.yml` (`https://${QL_DOMAIN}` and the internal Postgres DSN using
> `POSTGRES_PASSWORD`), so you do not set them in `.env`.

Build the image, then generate the attestation signing key **from inside it** and paste
the value into `.env` — no Python needed on the host:

```bash
cd deploy
docker compose build api
docker compose run --rm api python scripts/gen_attestation_key.py
```

> **First build takes a few minutes on ARM.** `qiskit` installs from prebuilt aarch64
> wheels; `qiskit-aer` is intentionally omitted, so there is no long native compilation.
> Subsequent builds are cached and fast.

Copy the printed `QL_ATTESTATION_KEY_B64=…` line into `deploy/.env`, then start everything:

```bash
docker compose up -d
```

---

## 6. Seed demo data (optional)

`scripts/seed_demo.py` builds a walkable demo workspace (recorded runs, a reproduction
verdict, a published card, corpus snapshots, frameworks, an attestation) and mints a
demo API key for `ql push`.

The image includes `scripts/`, so seed against the running stack (from `deploy/`, with
the containers `up`):

```bash
docker compose exec api python scripts/seed_demo.py
```

It prints the workspace slug, run/reproduction/card summaries, the attestation id and
its `/api/v1/attestations/<id>/verify` link, the admin login email, and a demo API key
for `ql login --token`. Copy the API key somewhere safe — it is shown only once.

---

## 7. Verify

```bash
# Health check through Caddy + TLS
curl -sS https://quantumledger.ben.gy/api/v1/health
# → {"status":"ok","service":"quantumledger","version":"0.1.0","deployment":"selfhost"}
```

Open the site in a browser: <https://quantumledger.ben.gy>

Watch Caddy issue the certificate (the proxy service is named **`proxy`**, not `caddy`):

```bash
cd deploy
docker compose logs -f proxy
```

Look for `certificate obtained successfully` for `quantumledger.ben.gy`. If issuance
hangs, the usual causes are: the Cloudflare record is still **proxied (orange)** instead
of DNS-only, the Security List ingress for port 80 is missing (step 3), or DNS has not
propagated yet (step 4).

Check all containers are healthy:

```bash
docker compose ps
```

---

## 8. Operations

All commands run from the `deploy/` directory on the VM.

**Redeploy** (pull latest code and rebuild):

```bash
cd ~/quantumledger
git pull
cd deploy
docker compose up -d --build
```

**Database backup** (Postgres user and database are both `quantumledger`):

```bash
docker compose exec postgres pg_dump -U quantumledger quantumledger > backup-$(date +%F).sql
```

Restore into a running Postgres:

```bash
cat backup-YYYY-MM-DD.sql | docker compose exec -T postgres psql -U quantumledger quantumledger
```

> **Attestation key must stay constant.** `QL_ATTESTATION_KEY_B64` in `deploy/.env` is
> the Ed25519 private key that signs every attestation. **Never rotate or regenerate it
> once live** — a new key silently invalidates every attestation ever issued (their
> signatures no longer verify against the published public key). Back up `deploy/.env`
> (or at least this value) somewhere durable and off-box. This env-var approach is
> precisely what keeps the signing key stable across redeploys on Oracle's ephemeral
> container filesystem.

**Logs:**

```bash
docker compose logs -f            # everything
docker compose logs -f api        # web/API
docker compose logs -f worker     # background jobs
docker compose logs -f proxy      # Caddy / TLS
```

**Stop / start / restart:**

```bash
docker compose stop               # stop containers (keeps data volumes)
docker compose start              # start again
docker compose down               # stop + remove containers (volumes preserved)
docker compose restart api        # restart a single service
```

Data lives in named volumes (`pgdata`, `caddy_data`, `caddy_config`) and survives
`down`/`up`. Do **not** run `docker compose down -v` unless you intend to erase the
database and the issued TLS certificates.

---

## 9. What Claude can and can't do

**Claude cannot:**

- Create your Oracle Cloud account or launch the VM — the signup and the instance
  creation flow are interactive (identity verification, capacity retries, console
  clicks). That part is on you (steps 2–3).

**Claude can:**

- **Set the Cloudflare DNS record** (step 4) via its Cloudflare skill once you share the
  VM's public IP — no API token needed from you.
- **Walk through the SSH deploy** (steps 5–7) with you once you're on the box: running
  `oracle-setup.sh`, generating and filling secrets, `docker compose up -d --build`,
  seeding demo data, and verifying `/api/v1/health` and TLS issuance.
