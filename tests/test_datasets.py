"""Integrity checks for the committed cross-vendor corpus-record datasets.

Guards the openly-licensed data assets so a bad edit (missing licence, wrong
source flag, non-numeric metric) fails CI instead of silently shipping.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
METRIQ = sorted((REPO / "datasets" / "metriq").glob("*.json"))
VENDOR = sorted((REPO / "datasets" / "vendor_specs").glob("*.json"))

_REQUIRED = {"provider", "backend_id", "captured_at", "source", "license_ref", "derived_metrics"}


def _load(p: Path) -> dict:
    return json.loads(p.read_text())


def test_datasets_present():
    assert METRIQ, "expected Metriq corpus records under datasets/metriq/"
    assert VENDOR, "expected vendor-reported records under datasets/vendor_specs/"


@pytest.mark.parametrize("p", METRIQ + VENDOR, ids=lambda p: p.name)
def test_record_shape(p):
    rec = _load(p)
    assert _REQUIRED <= set(rec), f"{p.name} missing {_REQUIRED - set(rec)}"
    dm = rec["derived_metrics"]
    assert isinstance(dm, dict) and dm.get("source") == rec["source"]
    # every metric value (besides the source tag) is numeric
    for k, v in dm.items():
        if k == "source":
            continue
        assert isinstance(v, (int, float)), f"{p.name}: metric {k} not numeric ({v!r})"
    # provenance carries a source URL for every metric
    prov = rec.get("snapshot_json", {}).get("metric_provenance", [])
    assert prov, f"{p.name}: no metric_provenance"
    assert all(e.get("source_url") for e in prov), f"{p.name}: provenance missing source_url"


@pytest.mark.parametrize("p", METRIQ, ids=lambda p: p.name)
def test_metriq_licence_and_flags(p):
    rec = _load(p)
    assert rec["source"] == "metriq"
    assert "CC-BY-4.0" in rec["license_ref"]
    assert rec["redistributable_raw"] is True


@pytest.mark.parametrize("p", VENDOR, ids=lambda p: p.name)
def test_vendor_specs_flags(p):
    rec = _load(p)
    assert rec["source"] == "vendor-reported"
    assert rec["license_ref"].startswith("vendor-reported")
    # vendor claims are never marked as redistributable raw data
    assert rec["redistributable_raw"] is False
    # fidelity metrics are sane fractions
    fid = rec["derived_metrics"].get("two_q_fidelity")
    if fid is not None:
        assert 0.9 < fid <= 1.0, f"{p.name}: implausible fidelity {fid}"


def test_expected_vendor_coverage():
    # Genuinely multi-vendor across the openly-licensed + vendor-reported records
    # (IBM is additionally covered by datasets/ibm/ raw calibration).
    providers = {_load(p)["provider"] for p in METRIQ + VENDOR}
    assert {"quantinuum", "ionq", "rigetti", "iqm", "origin"} <= providers, providers
