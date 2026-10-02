# Hosting Provenova on Cloudflare

provenova.net runs entirely on Cloudflare:

```
browser / SDK ──► Worker "provenova" ──► Container (one instance)
                  (cloudflare/)          FastAPI + Qiskit (deploy/cloudflare/Dockerfile)
                  edge-caches badges     SQLite on local disk ──Litestream──► R2 bucket "provenova-db"
                  and /static
```

- **Worker** (`cloudflare/src/index.ts`): forwards every request to a single named
  container, passes the real client IP, and caches public badges and `/static` at the edge
  so README badge traffic doesn't wake the container.
- **Container**: the unchanged FastAPI app (`standard-1`: 1/2 vCPU, 4 GiB). It sleeps after
  20 idle minutes. A cold start takes roughly 10–30 s: it restores the database, imports Qiskit,
  then boots.
- **Database**: SQLite at `/data/provenova.db`. The container disk is ephemeral, so
  [Litestream](https://litestream.io) ships every write to R2 within about 1 s and restores on
  boot (`deploy/cloudflare/entrypoint.sh`). The container refuses to start if replication
  isn't configured.
  **Exactly one writer:** `max_instances` must stay `1`.
- **Deploys**: `.github/workflows/cloudflare.yml` runs on every push to `main`:
  1. pytest;
  2. an image build plus a persistence round trip against MinIO;
  3. `wrangler deploy`.

Pull requests run steps 1 and 2.

## Configuration

Non-secret settings live in `cloudflare/wrangler.jsonc` under `vars`. Secrets are set with
`npx wrangler secret put NAME` (run in `cloudflare/`):

| Secret | |
|---|---|
| `QL_SECRET_KEY` | Signs sessions and JWTs. Copied from Fly so existing sessions and links survive. |
| `QL_ATTESTATION_KEY_B64` | Ed25519 attestation key. **Never rotate**: every issued attestation verifies against it. |
| `LITESTREAM_ACCESS_KEY_ID`, `LITESTREAM_SECRET_ACCESS_KEY` | R2 S3 credentials, Object Read & Write on `provenova-db` only. |
| Optional | `QL_ZENODO_TOKEN`, `QL_INDEXNOW_KEY`, `QL_GITHUB_TOKEN`, `QL_SMTP_*`, `QL_DATACITE_*` |

GitHub repository secrets for the deploy job:

- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_API_TOKEN`: create it from the **Edit Cloudflare Workers** token template,
  scoped to this account and the provenova.net zone, then add the account's **Containers**
  permission with Edit access. The template alone can't push the image.

## One-time move from Fly.io

`deploy/cloudflare/setup.sh` does each step. Run it from the repo root.

Prerequisites:

- `brew install flyctl uv`
- `fly auth login`
- `cd cloudflare && npm ci && npx wrangler whoami` shows the account in `wrangler.jsonc`.

1. **Deploy the Worker.** Add the two GitHub secrets above, then merge to `main`. CI deploys
   the Worker and container image. Nothing boots until the first request.
2. **Secrets.** Run `deploy/cloudflare/setup.sh secrets`. It:
   - creates the R2 bucket;
   - prompts (hidden input) for the R2 S3 credentials and stores them in the Worker and your
     keychain;
   - copies `QL_SECRET_KEY`, `QL_ATTESTATION_KEY_B64` and any optional secrets straight from
     the running Fly app into the Worker.
3. **Data.** Run `deploy/cloudflare/setup.sh migrate`. **The downtime starts here.** It:
   - scales the Fly app to zero so nothing is written after the copy;
   - copies Fly Postgres to SQLite with `scripts/migrate_db.py`, which checks per-table row
     counts, SQLite integrity and foreign keys, and that every run's provenance document is
     identical and its `run_hash` still verifies;
   - uploads the copy to R2 as a Litestream snapshot;
   - restores it back and compares the copies.
4. **Cutover.** Run `WORKERS_URL=https://provenova.<subdomain>.workers.dev deploy/cloudflare/setup.sh cutover`.
   It:
   - wakes the container and checks its health;
   - asks you to delete Fly's DNS records for provenova.net and www (Wrangler can't attach a
     custom domain over an existing record);
   - enables `routes` in `wrangler.jsonc` and deploys the Worker;
   - waits until provenova.net is served by Cloudflare.

   **Commit and push the `wrangler.jsonc` change**, so later CI deploys keep the domains.
5. **Decommission.** After a few quiet days, run `fly apps destroy quantumledger-bengy` and
   `fly apps destroy quantumledger-db`.

**Rollback** (before step 5):

1. Run `fly scale count 1 -a quantumledger-bengy`.
2. Restore the DNS records to Fly.
3. Remove the Worker's custom domains.

Any writes made on Cloudflare in the meantime stay only in R2.

## Operations

- **Logs:** run `npx wrangler tail` in `cloudflare/`, or use the Workers dashboard, which shows
  container logs too.
- **Redo the data import:** bump `LITESTREAM_PATH` in `wrangler.jsonc` (for example
  `…-v2`), deploy, then re-run `setup.sh migrate`. `migrate` refuses to write into a
  non-empty replica path.
- **Point-in-time restore:** Litestream keeps 7 days of snapshots and WAL in R2. Run
  `litestream restore -timestamp …` against the bucket with the keychain credentials. See
  `write_litestream_config` in `setup.sh` for the config.
- **Running scripts against production** (`scripts/bootstrap_growth.py`, `seed_real.py`,
  `refresh_corpus.py`): there's no remote database to tunnel to any more. The scripts run
  inside the container:
  1. Add `"ssh": {"enabled": true}` and your public key under `authorized_keys` to the
     container in `wrangler.jsonc`, then deploy.
  2. Run `npx wrangler containers instances provenova-provenovacontainer` to find the instance.
  3. Run `npx wrangler containers ssh <instance> -- python /app/scripts/<script>.py`.

  The scripts use the container's `QL_DATABASE_URL`, and Litestream replicates their writes.
- **Background worker:** the crawler/monitor loop (`server/app/worker.py`) wasn't deployed on
  Fly either. Its crawl data is static fixtures.
