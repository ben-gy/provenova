"""Integrity checks for the committed datasets.

Guards the openly-licensed data assets so a bad edit (missing licence, wrong
source flag, non-numeric metric, a lost date or source link) fails CI instead
of silently shipping to /tracker, /hardware and /leaderboard.
"""

from __future__ import annotations

import csv
import datetime as _dt
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "datasets"
IBM = sorted((DATA / "ibm").glob("*.json"))
METRIQ = sorted((DATA / "metriq").glob("*.json"))
VENDOR = sorted((DATA / "vendor_specs").glob("*.json"))

_SERIES_REQUIRED = {"schema", "provider", "backend_id", "source", "license_ref", "snapshots"}
_RECORD_REQUIRED = {"provider", "backend_id", "captured_at", "source", "license_ref", "derived_metrics"}


def _load(p: Path) -> dict:
    return json.loads(p.read_text())


def _dt_of(s: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _numeric_metrics(dm: dict, name: str) -> None:
    for k, v in dm.items():
        if k == "source":
            continue
        assert isinstance(v, (int, float)), f"{name}: metric {k} not numeric ({v!r})"


def test_datasets_present():
    assert len(IBM) >= 50, "expected the IBM calibration history under datasets/ibm/"
    assert METRIQ, "expected Metriq series under datasets/metriq/"
    assert VENDOR, "expected vendor-reported records under datasets/vendor_specs/"


@pytest.mark.parametrize("p", IBM + METRIQ, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_series_shape(p):
    doc = _load(p)
    assert doc["schema"] == "qlprov/corpus-series/1.0"
    assert _SERIES_REQUIRED <= set(doc), f"{p.name} missing {_SERIES_REQUIRED - set(doc)}"
    snaps = doc["snapshots"]
    assert snaps, f"{p.name}: no snapshots"
    dates = [s["captured_at"] for s in snaps]
    assert dates == sorted(dates), f"{p.name}: snapshots not oldest-first"
    assert len(set(dates)) == len(dates), f"{p.name}: duplicate snapshot dates"
    for s in snaps:
        assert _dt_of(s["captured_at"]).tzinfo is not None
        dm = s["derived_metrics"]
        assert dm.get("source") == doc["source"]
        _numeric_metrics(dm, p.name)


@pytest.mark.parametrize("p", IBM, ids=lambda p: p.name)
def test_ibm_history_provenance(p):
    doc = _load(p)
    assert doc["provider"] == "ibm" and doc["source"] == "ibm"
    assert doc["backend_id"].startswith(("ibm_", "ibmq")), "only files naming a real device"
    assert "Apache-2.0" in doc["license_ref"] and doc["redistributable_raw"] is True
    for s in doc["snapshots"]:
        # every number re-derivable: a commit-pinned permalink plus the raw file's hash
        assert re.match(r"^https://github\.com/Qiskit/[\w.-]+/blob/[0-9a-f]{40}/.+props_.+\.json$",
                        s["raw_ref"]), s["raw_ref"]
        assert re.fullmatch(r"[0-9a-f]{64}", s["raw_sha256"])
        if "median_2q_error" in s["derived_metrics"]:
            assert 0 < s["derived_metrics"]["median_2q_error"] < 0.5


def test_ibm_history_keeps_real_dates():
    """The old extractor stamped every device 2024-06-01; the history must span years."""
    dates = {s["captured_at"][:10] for p in IBM for s in _load(p)["snapshots"]}
    years = {d[:4] for d in dates}
    assert {"2019", "2020", "2021", "2025"} <= years, sorted(years)
    assert len(dates) > 50


@pytest.mark.parametrize("p", METRIQ, ids=lambda p: p.name)
def test_metriq_licence_identity_and_sources(p):
    doc = _load(p)
    assert doc["source"] == "metriq"
    assert "CC-BY-4.0" in doc["license_ref"]
    assert doc["redistributable_raw"] is True
    # cloud hosts are not makers: Braket devices are filed under who built them
    assert doc["provider"] not in {"aws", "braket"}
    for s in doc["snapshots"]:
        prov = {e["metric"]: e for e in s["metric_provenance"]}
        measured = set(s["derived_metrics"]) - {"source", "n_qubits"}
        assert measured <= set(prov), f"{p.name}: metric without a source file"
        assert all(e["source_url"].startswith("https://raw.githubusercontent.com/unitaryfoundation/")
                   for e in prov.values())


@pytest.mark.parametrize("p", VENDOR, ids=lambda p: p.name)
def test_vendor_record_shape(p):
    rec = _load(p)
    assert _RECORD_REQUIRED <= set(rec), f"{p.name} missing {_RECORD_REQUIRED - set(rec)}"
    assert rec["derived_metrics"].get("source") == rec["source"]
    _numeric_metrics(rec["derived_metrics"], p.name)
    prov = rec.get("snapshot_json", {}).get("metric_provenance", [])
    assert prov and all(e.get("source_url") for e in prov), f"{p.name}: provenance missing source_url"


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
    providers = {_load(p)["provider"] for p in IBM + METRIQ + VENDOR}
    assert {"ibm", "quantinuum", "ionq", "rigetti", "iqm", "origin"} <= providers, providers


def test_experiments_dataset_pinned_and_licensed():
    d = DATA / "experiments"
    src = json.loads((d / "SOURCE.json").read_text())
    assert re.fullmatch(r"[0-9a-f]{40}", src["commit"])
    assert src["license"] == "MIT" and "MIT License" in (d / "LICENSE").read_text()
    with open(d / "entangled_state_error_exp.csv", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows and {"Entangled State Error", "Year", "Platform", "Link"} <= set(rows[0])
    for name in ("qubit_count.csv", "qec_exp.csv"):
        with open(d / name, newline="") as fh:
            assert {"Year", "Platform", "Link"} <= set(next(csv.DictReader(fh)))


def test_tracker_context_is_cited():
    ctx = json.loads((DATA / "context" / "tracker.json").read_text())
    _dt.date.fromisoformat(ctx["as_of"])
    for e in ctx["rsa_2048_estimates"]:
        assert e["url"].startswith("https://arxiv.org/abs/") and e["physical_qubits"] > 0
        assert 0 < e["assumed_gate_error"] < 0.1
    assert ctx["darpa_qbi"]["sources"] and all(s["url"].startswith("https://")
                                               for s in ctx["darpa_qbi"]["sources"])
    assert ctx["advantage_tracker"]["url"].startswith("https://")
