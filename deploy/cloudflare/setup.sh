#!/usr/bin/env bash
# One-time move of Provenova from Fly.io to Cloudflare. Run from the repo root,
# one phase at a time (deploy/HOSTING-cloudflare.md walks through the order):
#
#   deploy/cloudflare/setup.sh secrets    # R2 bucket + creds, copy app secrets from Fly
#   deploy/cloudflare/setup.sh migrate    # stop Fly, copy Postgres -> SQLite -> R2
#   deploy/cloudflare/setup.sh cutover    # point provenova.net at the Worker
#
# Needs: flyctl (logged in), uv, node/npx, and wrangler logged in to the
# Cloudflare account in cloudflare/wrangler.jsonc. Secrets are only ever piped
# between tools or read with a hidden prompt; nothing is echoed or written to
# the repo. The R2 S3 credentials are kept in the macOS keychain for `migrate`.
set -euo pipefail

FLY_APP="${FLY_APP:-quantumledger-bengy}"
FLY_DB_APP="${FLY_DB_APP:-quantumledger-db}"
BUCKET="provenova-db"
KC_ID="provenova-r2-access-key-id"
KC_SECRET="provenova-r2-secret-access-key"
LITESTREAM_VERSION="0.5.17"
# sha256 of litestream-${LITESTREAM_VERSION}-darwin-{arm64,x86_64}.tar.gz (release checksums.txt)
LS_SHA_arm64="e211f68ff7658d19f193f2914417afdf8f89a053ff8f263e5d6b3b1d3bbc7b08"
LS_SHA_x86_64="891875af09db152e93a4b31a8a79f538ce7ce702c132803cfe0a831e7cb1b7db"
# Secrets the container reads (cloudflare/src/index.ts PASSTHROUGH) that may exist on Fly.
COPYABLE_SECRETS=(QL_SECRET_KEY QL_ATTESTATION_KEY_B64 QL_ZENODO_TOKEN QL_ZENODO_ENDPOINT
  QL_DOI_PROVIDER QL_DATACITE_ENDPOINT QL_DATACITE_REPOSITORY_ID QL_DATACITE_PASSWORD
  QL_DATACITE_PREFIX QL_SMTP_HOST QL_SMTP_PORT QL_SMTP_USER QL_SMTP_PASSWORD QL_SMTP_FROM
  QL_INDEXNOW_KEY QL_GITHUB_TOKEN)

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CF_DIR="$ROOT/cloudflare"
WORK="${TMPDIR:-/tmp}/provenova-migration"
mkdir -p "$WORK"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "$1 not found — $2"; }
confirm() { read -r -p "$1 [y/N] " a < /dev/tty; [[ "$a" == [yY]* ]]; }

wrangler() { (cd "$CF_DIR" && npx --no-install wrangler "$@"); }

account_id() {
  python3 -c 'import json,re,sys;s=open(sys.argv[1]).read();print(re.search(r"\"CLOUDFLARE_ACCOUNT_ID\":\s*\"([0-9a-f]+)\"",s).group(1))' \
    "$CF_DIR/wrangler.jsonc"
}
replica_path() {
  python3 -c 'import re,sys;s=open(sys.argv[1]).read();print(re.search(r"\"LITESTREAM_PATH\":\s*\"([^\"]+)\"",s).group(1))' \
    "$CF_DIR/wrangler.jsonc"
}

preflight() {
  need fly "brew install flyctl && fly auth login"
  need uv "brew install uv"
  need npx "install Node (nvm) first"
  need python3 "install Xcode command line tools"
  [[ -d "$CF_DIR/node_modules" ]] || (cd "$CF_DIR" && npm ci)
  fly auth whoami >/dev/null 2>&1 || die "flyctl is not logged in: run 'fly auth login'"
  local acct; acct="$(account_id)"
  wrangler whoami 2>/dev/null | grep -q "$acct" \
    || die "wrangler is not logged in to account $acct (run 'npx wrangler login' in cloudflare/)"
}

fly_env() {  # print one env var from the running Fly app (empty if unset)
  fly ssh console -a "$FLY_APP" --pty=false -C "printenv $1" 2>/dev/null | tr -d '\r' || true
}

wake_fly() {
  curl -fsS -o /dev/null --max-time 60 https://provenova.net/api/v1/health \
    || die "provenova.net (Fly) did not answer; start a machine with 'fly machine start -a $FLY_APP'"
}

# --------------------------------------------------------------------------- #
cmd_secrets() {
  preflight
  wrangler deployments list >/dev/null 2>&1 \
    || die "the 'provenova' Worker isn't deployed yet: merge the PR so CI deploys it, then re-run"
  say "R2 bucket '$BUCKET'"
  if wrangler r2 bucket list 2>/dev/null | grep -qw "$BUCKET"; then
    echo "exists"
  else
    wrangler r2 bucket create "$BUCKET"
  fi

  say "R2 S3 credentials for Litestream"
  cat <<EOF
In the Cloudflare dashboard: R2 -> Manage API tokens -> Create API token
  - Permissions: Object Read & Write
  - Specify bucket: $BUCKET only
Paste the two values it shows (input is hidden).
EOF
  local kid ksec
  read -rs -p "Access Key ID: " kid < /dev/tty; echo
  read -rs -p "Secret Access Key: " ksec < /dev/tty; echo
  [[ -n "$kid" && -n "$ksec" ]] || die "both values are required"
  security delete-generic-password -s "$KC_ID" >/dev/null 2>&1 || true
  security delete-generic-password -s "$KC_SECRET" >/dev/null 2>&1 || true
  security add-generic-password -a provenova -s "$KC_ID" -w "$kid"
  security add-generic-password -a provenova -s "$KC_SECRET" -w "$ksec"
  printf '%s' "$kid" | wrangler secret put LITESTREAM_ACCESS_KEY_ID
  printf '%s' "$ksec" | wrangler secret put LITESTREAM_SECRET_ACCESS_KEY
  unset kid ksec

  say "Copying app secrets from Fly ($FLY_APP)"
  wake_fly
  local copied=0 name value
  for name in "${COPYABLE_SECRETS[@]}"; do
    value="$(fly_env "$name")"
    [[ -n "$value" ]] || continue
    printf '%s' "$value" | wrangler secret put "$name" >/dev/null
    echo "  copied $name"
    copied=$((copied + 1))
  done
  unset value
  for name in QL_SECRET_KEY QL_ATTESTATION_KEY_B64; do
    wrangler secret list 2>/dev/null | grep -q "\"$name\"" \
      || die "$name was not copied. It must match Fly's value (sessions, and every issued attestation, depend on it)."
  done
  echo "  $copied secret(s) copied"
  say "Done. Next: $0 migrate"
}

# --------------------------------------------------------------------------- #
litestream_bin() {
  local arch; arch="$(uname -m)"; [[ "$arch" == arm64 ]] || arch=x86_64
  local sha_var="LS_SHA_$arch" bin="$WORK/litestream"
  if [[ ! -x "$bin" ]]; then
    local tgz="$WORK/litestream.tgz"
    curl -fsSL -o "$tgz" "https://github.com/benbjohnson/litestream/releases/download/v${LITESTREAM_VERSION}/litestream-${LITESTREAM_VERSION}-darwin-${arch}.tar.gz"
    echo "${!sha_var}  $tgz" | shasum -a 256 -c - >/dev/null || die "litestream checksum mismatch"
    tar -xzf "$tgz" -C "$WORK" litestream
  fi
  echo "$bin"
}

write_litestream_config() {  # $1 = local db path
  cat > "$WORK/litestream.yml" <<EOF
dbs:
  - path: $1
    replica:
      type: s3
      bucket: $BUCKET
      path: $(replica_path)
      endpoint: https://$(account_id).r2.cloudflarestorage.com
      region: auto
      force-path-style: true
      access-key-id: \${LITESTREAM_ACCESS_KEY_ID}
      secret-access-key: \${LITESTREAM_SECRET_ACCESS_KEY}
EOF
}

cmd_migrate() {
  preflight
  local ls; ls="$(litestream_bin)"
  export LITESTREAM_ACCESS_KEY_ID LITESTREAM_SECRET_ACCESS_KEY
  LITESTREAM_ACCESS_KEY_ID="$(security find-generic-password -w -s "$KC_ID")" \
    || die "no R2 credentials in the keychain; run '$0 secrets' first"
  LITESTREAM_SECRET_ACCESS_KEY="$(security find-generic-password -w -s "$KC_SECRET")"

  local db="$WORK/provenova.db"
  rm -f "$db" "$db-wal" "$db-shm" "$WORK/verify.db" "$WORK/probe.db"
  write_litestream_config "$db"

  say "Checking the R2 replica path is empty ($(replica_path))"
  "$ls" restore -config "$WORK/litestream.yml" -if-replica-exists -o "$WORK/probe.db" "$db" >/dev/null 2>&1 || true
  if [[ -f "$WORK/probe.db" ]]; then
    die "R2 already holds a database at $(replica_path) (the container may have booted before the import).
Bump LITESTREAM_PATH in cloudflare/wrangler.jsonc (e.g. -v2), deploy, and re-run."
  fi

  say "Reading the database URL from Fly"
  wake_fly
  local src; src="$(fly_env QL_DATABASE_URL)"
  [[ -n "$src" ]] || die "could not read QL_DATABASE_URL from $FLY_APP"

  cat <<EOF

This stops the Fly app ($FLY_APP) so nothing is written after the copy.
provenova.net is down from now until '$0 cutover' finishes (usually ~5 minutes).
Roll back at any point with:  fly scale count 1 -a $FLY_APP
EOF
  confirm "Stop Fly and migrate now?" || die "aborted"
  fly scale count 0 -a "$FLY_APP" --yes

  say "Opening a tunnel to $FLY_DB_APP"
  fly proxy 15432:5432 -a "$FLY_DB_APP" >"$WORK/proxy.log" 2>&1 &
  local proxy_pid=$!
  trap 'kill $proxy_pid 2>/dev/null || true' EXIT
  for _ in $(seq 30); do nc -z 127.0.0.1 15432 2>/dev/null && break; sleep 1; done
  nc -z 127.0.0.1 15432 || die "fly proxy did not come up (see $WORK/proxy.log)"

  local local_url
  local_url="$(python3 - "$src" <<'PY'
import sys
from urllib.parse import urlsplit, urlunsplit
u = urlsplit(sys.argv[1])
scheme = "postgresql+psycopg"
auth = u.netloc.rsplit("@", 1)[0]
print(urlunsplit((scheme, f"{auth}@127.0.0.1:15432", u.path, "sslmode=disable", "")))
PY
)"

  say "Copying Postgres -> SQLite"
  (cd "$ROOT" && UV_CONSTRAINT=deploy/requirements.lock uv run --no-project --quiet --python 3.12 \
     --with ./packages/ql-core --with 'psycopg[binary]>=3.1' \
     python scripts/migrate_db.py "$local_url" "$db")
  unset local_url src

  say "Uploading to R2 (Litestream snapshot)"
  "$ls" replicate -config "$WORK/litestream.yml" -once -force-snapshot

  say "Verifying the R2 copy restores identically"
  "$ls" restore -config "$WORK/litestream.yml" -o "$WORK/verify.db" "$db"
  local q="SELECT (SELECT count(*) FROM runs),(SELECT count(*) FROM result_cards),(SELECT count(*) FROM attestations),(SELECT count(*) FROM accounts);"
  local a b
  a="$(sqlite3 "$db" "$q")"; b="$(sqlite3 "$WORK/verify.db" "$q")"
  [[ "$a" == "$b" ]] || die "restored copy differs: $a vs $b"
  [[ "$(sqlite3 "$WORK/verify.db" 'PRAGMA integrity_check;')" == ok ]] || die "restored copy failed integrity_check"
  echo "runs|cards|attestations|accounts = $b"

  say "Done. The container restores this on its first request. Next: $0 cutover"
}

# --------------------------------------------------------------------------- #
cmd_cutover() {
  preflight
  local dev_url="${WORKERS_URL:-}"
  if [[ -z "$dev_url" ]]; then
    read -r -p "Worker URL (https://provenova.<subdomain>.workers.dev): " dev_url < /dev/tty
  fi
  say "Waking the container on $dev_url (first boot restores from R2, up to ~2 min)"
  local ok=""
  for _ in $(seq 40); do
    if curl -fsS --max-time 30 "$dev_url/api/v1/health" | grep -q '"provenova"'; then ok=1; break; fi
    sleep 5
  done
  [[ -n "$ok" ]] || die "container did not become healthy; check 'npx wrangler tail' in cloudflare/"
  curl -fsS "$dev_url/sitemap.xml" | grep -c "/cards/" | xargs echo "public cards in sitemap:"

  cat <<'EOF'

In the Cloudflare dashboard, open the provenova.net zone -> DNS and DELETE the
records that point at Fly (the A/AAAA or CNAME for provenova.net and www).
Wrangler cannot attach the domains while those records exist.
EOF
  confirm "Records deleted?" || die "aborted"

  say "Enabling the custom domains in cloudflare/wrangler.jsonc"
  python3 - "$CF_DIR/wrangler.jsonc" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p).read()
block = re.search(r'  // "routes": \[\n(?:  //.*\n)+?  // \],\n', s)
if block:
    s = s.replace(block.group(0), re.sub(r"^  // ", "  ", block.group(0), flags=re.M))
    open(p, "w").write(s)
    print("routes enabled")
else:
    print("routes already enabled")
PY
  wrangler deploy --containers-rollout=none

  say "Waiting for https://provenova.net to serve from Cloudflare"
  for _ in $(seq 60); do
    if curl -fsS --max-time 20 https://provenova.net/api/v1/health 2>/dev/null | grep -q '"provenova"' \
       && ! curl -sI https://provenova.net/ | grep -qi '^fly-request-id'; then
      say "provenova.net is live on Cloudflare."
      cat <<EOF
Now:
  1. Commit and push cloudflare/wrangler.jsonc (routes enabled) so CI deploys keep them.
  2. After a few days of no issues: fly apps destroy $FLY_APP && fly apps destroy $FLY_DB_APP
EOF
      return 0
    fi
    sleep 5
  done
  die "provenova.net did not switch over; check the custom domains under Workers -> provenova -> Domains"
}

case "${1:-}" in
  secrets) cmd_secrets ;;
  migrate) cmd_migrate ;;
  cutover) cmd_cutover ;;
  *) sed -n '2,12p' "$0"; exit 2 ;;
esac
