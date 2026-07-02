.PHONY: e2e test install

# Full mock end-to-end test: installs packages, starts the server on a throwaway
# DB, and drives every layer (SDK/CLI, API, reproduce, cards, compliance,
# corpus, web UI, multi-tenant security, interop errors).
e2e:
	bash scripts/e2e/run.sh

# Fast unit/integration suite.
test:
	PYTHONPATH=server .venv/bin/python -m pytest

# Editable install of every package into .venv.
install:
	.venv/bin/python -m pip install -e packages/ql-core -e "packages/ql-sdk[aer]" \
	  -e packages/ql-crawler -e server
