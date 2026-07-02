"""Offline fixture source — replays committed vendor-native JSON.

This is the *default* crawler source: it needs no credentials and makes no
network calls, so the full ingest pipeline (and the test suite) runs anywhere.

Each vendor fixture file bundles multiple longitudinal timepoints for one
device. :meth:`iter_readings` splits that bundle back into one raw payload per
timepoint, re-attaching the file-level vendor context (device name, qubit count,
connectivity, native gates) to each so the normalizer sees a self-contained
vendor-native snapshot.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from .base import CalibrationSource

# Which fixture files belong to which provider, and the source URL / license
# reference that live crawling would attach. Kept here so FixtureSource mirrors
# what the live path records for provenance.
_PROVIDER_FILES = {
    "ibm": ["ibm/ibm_kyiv.json", "ibm/ibm_sherbrooke.json"],
    "ionq": ["ionq/ionq_forte.json"],
    "braket": ["braket/rigetti_ankaa.json"],
}

_SOURCE_URLS = {
    "ibm": "https://quantum.cloud.ibm.com/api (BackendProperties)",
    "ionq": "https://api.ionq.co/v0.3/characterizations",
    "braket": "https://braket.aws (GetDevice deviceCapabilities)",
}

_LICENSE_REFS = {
    "ibm": "ibm-quantum-tos-2024",
    "ionq": "ionq-tos-2024",
    "braket": "aws-braket-tos-2024",
}


class FixtureSource(CalibrationSource):
    """Replay vendor-native calibration fixtures for one provider.

    Parameters
    ----------
    provider:
        ``"ibm"``, ``"ionq"`` or ``"braket"``.
    fixtures_dir:
        Path to the repo ``fixtures/`` directory (contains ``ibm/``, ``ionq/``,
        ``braket/`` subfolders).
    """

    mode = "fixture"

    def __init__(self, provider: str, fixtures_dir: str | Path):
        if provider not in _PROVIDER_FILES:
            raise ValueError(
                f"unknown provider {provider!r}; "
                f"expected one of {sorted(_PROVIDER_FILES)}"
            )
        self.provider = provider
        self.fixtures_dir = Path(fixtures_dir)
        self._cache: dict[str, dict] = {}

    # -- loading ---------------------------------------------------------------

    def _load_file(self, rel_path: str) -> dict:
        if rel_path not in self._cache:
            path = self.fixtures_dir / rel_path
            self._cache[rel_path] = json.loads(path.read_text(encoding="utf-8"))
        return self._cache[rel_path]

    def _backend_name(self, doc: dict) -> str:
        # IBM uses backend_name, IonQ uses name, Braket uses deviceName.
        return doc.get("backend_name") or doc.get("name") or doc.get("deviceName")

    def _files(self) -> list[str]:
        return _PROVIDER_FILES[self.provider]

    # -- CalibrationSource API -------------------------------------------------

    def list_backends(self) -> list[str]:
        backends = []
        for rel in self._files():
            doc = self._load_file(rel)
            backends.append(self._backend_name(doc))
        return backends

    def _doc_for(self, backend_id: str) -> dict:
        for rel in self._files():
            doc = self._load_file(rel)
            if self._backend_name(doc) == backend_id:
                return doc
        raise KeyError(f"no fixture for provider={self.provider} backend={backend_id!r}")

    def _snapshot_updated_at(self, snapshot: dict) -> str | None:
        # Each vendor stamps the timepoint differently.
        return (
            snapshot.get("last_update_date")  # IBM
            or snapshot.get("date")  # IonQ
            or snapshot.get("executionWindowUpdatedAt")  # Braket
        )

    def _snapshots(self, doc: dict) -> list[dict]:
        return doc.get("snapshots", [])

    def _make_raw(self, doc: dict, snapshot: dict) -> dict:
        """Build one self-contained vendor-native raw payload for a timepoint.

        We copy the file-level device context (name, qubit count, connectivity,
        native gates) alongside the single timepoint so the normalizer never
        needs the surrounding multi-snapshot envelope.
        """
        raw = dict(snapshot)
        for key in (
            "backend_name",
            "name",
            "deviceName",
            "providerName",
            "n_qubits",
            "qubits" if self.provider != "ibm" else None,  # IBM 'qubits' is per-timepoint
            "connectivity",
            "native_gates",
            "paradigm",
        ):
            if key and key in doc and key not in raw:
                raw[key] = doc[key]
        return raw

    def fetch_raw(self, backend_id: str) -> tuple[dict, dict]:
        """Return the *latest* timepoint for a backend (single reading)."""
        doc = self._doc_for(backend_id)
        snaps = self._snapshots(doc)
        if not snaps:
            raise ValueError(f"fixture for {backend_id!r} has no snapshots")
        latest = max(snaps, key=lambda s: self._snapshot_updated_at(s) or "")
        return self._make_raw(doc, latest), self._meta(backend_id, latest)

    def iter_readings(self, backend_id: str):
        """Yield every longitudinal timepoint for ``backend_id``."""
        doc = self._doc_for(backend_id)
        snaps = sorted(
            self._snapshots(doc),
            key=lambda s: self._snapshot_updated_at(s) or "",
        )
        for snap in snaps:
            yield self._make_raw(doc, snap), self._meta(backend_id, snap)

    def _meta(self, backend_id: str, snapshot: dict) -> dict:
        return {
            "updated_at": self._snapshot_updated_at(snapshot),
            "source_url": _SOURCE_URLS[self.provider],
            "license_ref": _LICENSE_REFS[self.provider],
            "mode": self.mode,
            "fetched_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }
