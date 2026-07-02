"""Shared test setup: isolate the server DB/keys into a temp dir."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="ql_test_"))
os.environ.setdefault("QL_DATABASE_URL", f"sqlite:///{_TMP/'ql_test.db'}")
os.environ.setdefault("QL_ATTESTATION_KEY_PATH", str(_TMP / "attestation.key"))
os.environ.setdefault("QL_SECRET_KEY", "test-secret")
os.environ.setdefault("QL_BASE_URL", "http://testserver")
