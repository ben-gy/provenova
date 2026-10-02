#!/bin/sh
# Container entrypoint: restore the SQLite database from R2, then run the app
# under Litestream so every committed write is shipped back to R2.
#
# The container's disk is ephemeral (a fresh image disk on every start), so R2
# is the only durable copy. Hence: fail closed if replication isn't configured,
# and restore as a separate step so an R2 error stops the boot instead of the
# app starting on an empty database.
set -eu

DB=/data/provenova.db
APP="uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips=*"

missing=""
for var in CLOUDFLARE_ACCOUNT_ID LITESTREAM_BUCKET LITESTREAM_PATH \
           LITESTREAM_ACCESS_KEY_ID LITESTREAM_SECRET_ACCESS_KEY; do
  eval "val=\${$var:-}"
  [ -n "$val" ] || missing="$missing $var"
done

if [ -n "$missing" ]; then
  if [ "${QL_DEPLOYMENT:-hosted}" = "selfhost" ]; then
    echo "provenova: replication not configured (missing:$missing); running without a backup" >&2
    exec $APP
  fi
  echo "provenova: refusing to start, replication not configured (missing:$missing)." >&2
  echo "provenova: without it every write would be lost when the container stops." >&2
  exit 1
fi

export LITESTREAM_ENDPOINT="${LITESTREAM_ENDPOINT:-https://${CLOUDFLARE_ACCOUNT_ID}.r2.cloudflarestorage.com}"
export LITESTREAM_REGION="${LITESTREAM_REGION:-auto}"

# No-op when the DB already exists (warm restart) or when the replica path is
# empty (brand-new deployment); any other failure aborts the boot.
litestream restore -config /etc/litestream.yml -if-db-not-exists -if-replica-exists "$DB"

exec litestream replicate -config /etc/litestream.yml -exec "$APP"
