#!/usr/bin/env bash
# Full mock end-to-end test for Provenova.
#
# Installs the libraries, stands up the server against a throwaway database on a
# free port, seeds a walkable dataset, then drives every layer (SDK/CLI, ingest
# + read API, reproduce, cards/badges, compliance/attestations, corpus, web UI,
# multi-tenant security, and interop error handling) and asserts the results.
#
# Fully isolated and repeatable: each run uses a fresh temp DB / SDK home /
# signing key and never touches your real ~/.provenova (or legacy ~/.quantumledger) or repo database.
#
#   bash scripts/e2e/run.sh          # or: make e2e
#
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"
VENV="$REPO/.venv"
PY="$VENV/bin/python"

echo "==================================================================="
echo " Provenova — full end-to-end test"
echo "==================================================================="

# 1) venv + editable install of every package -------------------------------
if [ ! -x "$PY" ]; then
  echo "[setup] creating venv at .venv ..."
  (python3.12 -m venv "$VENV" 2>/dev/null) || python3 -m venv "$VENV"
fi
echo "[setup] installing packages (editable) — this can take a while on first run ..."
"$PY" -m pip install -q --upgrade pip >/dev/null
"$PY" -m pip install -q -e packages/ql-core -e "packages/ql-sdk[aer]" \
      -e packages/ql-crawler -e server

# 2) isolated, throwaway environment ----------------------------------------
TMP="$(mktemp -d 2>/dev/null || mktemp -d -t qle2e)"
export QL_HOME="$TMP/qlhome"                       # SDK/CLI local ledger + config
export QL_DATABASE_URL="sqlite:///$TMP/e2e.db"      # fresh server DB
export QL_ATTESTATION_KEY_PATH="$TMP/attestation.key"
export QL_SECRET_KEY="e2e-insecure-secret"
# NB: use a real TLD — the API's EmailStr validator rejects reserved domains
# like .local, so a *.local admin email can't log in via /api/v1/auth/login.
export QL_ADMIN_EMAIL="e2e-admin@example.com"
export QL_E2E_ADMIN_EMAIL="$QL_ADMIN_EMAIL"
export QL_E2E_ADMIN_PASSWORD="e2e-pass-123456"
export QL_DEPLOYMENT="selfhost"
export QL_PUBLIC_CARDS="true"
export QL_RATELIMIT_ENABLED="false"   # e2e drives many requests from one IP

PORT="$("$PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
export QL_E2E_ENDPOINT="http://127.0.0.1:$PORT"
export QL_BASE_URL="$QL_E2E_ENDPOINT"

SERVER_LOG="$TMP/server.log"
SERVER_PID=""
cleanup() {
  [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null || true
  rm -rf "$TMP"
}
trap cleanup EXIT
echo "[setup] isolated workdir: $TMP"
echo "[setup] endpoint: $QL_E2E_ENDPOINT"

# 3) provision the database (bootstrap admin + password + seed dataset) ------
echo "[provision] bootstrapping + seeding database ..."
PYTHONPATH=server "$PY" scripts/e2e/provision.py

# 4) start the server --------------------------------------------------------
echo "[server] starting uvicorn ..."
PYTHONPATH=server "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" \
  >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!

echo -n "[server] waiting for health "
UP=""
for _ in $(seq 1 60); do
  if curl -fs "$QL_E2E_ENDPOINT/api/v1/health" >/dev/null 2>&1; then UP=1; echo " ok"; break; fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo " server exited early:"; tail -40 "$SERVER_LOG"; exit 1
  fi
  echo -n "."; sleep 0.5
done
if [ -z "$UP" ]; then echo " timed out"; tail -40 "$SERVER_LOG"; exit 1; fi

# 5) run the assertions ------------------------------------------------------
set +e
PYTHONPATH=server "$PY" scripts/e2e/driver.py
RC=$?
set -e

echo "==================================================================="
if [ "$RC" -eq 0 ]; then
  echo " E2E PASSED"
else
  echo " E2E FAILED (rc=$RC)"
  echo "--- server log (tail) ---"
  tail -40 "$SERVER_LOG"
fi
echo "==================================================================="
exit "$RC"
