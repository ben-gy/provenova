"""The public-progress surface: corpus sync, Metriq history, tracker model, plain language.

Corpus-sync and tracker tests run against their own throwaway database (the
real ``datasets/`` bundle is ~250 snapshots, which would crowd the shared test
DB other modules assert against).
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import pytest

import provenova_core as qc
from provenova_core.models import CorpusSnapshot

REPO = Path(__file__).resolve().parents[1]
DATASETS = REPO / "datasets"


@pytest.fixture()
def db(tmp_path):
    engine = qc.init_db(f"sqlite:///{tmp_path / 'corpus.db'}")
    s = qc.session_factory(engine)()
    yield s
    s.close()


# --- corpus sync -------------------------------------------------------------

def test_sync_loads_bundle_and_is_idempotent(db):
    from app.services.corpus_sync import sync_bundled

    first = sync_bundled(db, DATASETS)
    assert first["inserted"] == first["bundled"] > 200
    again = sync_bundled(db, DATASETS)
    assert again == {"bundled": first["bundled"], "inserted": 0, "deleted": 0}


def test_sync_removes_superseded_owned_rows_only(db):
    from app.services.corpus_sync import sync_bundled

    now = _dt.datetime.now(_dt.timezone.utc)
    # the old extractor's misdated IBM row, a stale Metriq identity, and a user source
    db.add_all([
        CorpusSnapshot(provider="ibm", backend_id="ibm_hanoi", captured_at=now,
                       content_hash="old-2024-06-01", snapshot_json={},
                       derived_metrics={"median_2q_error": 0.006, "source": "ibm"},
                       license_ref="Apache-2.0", redistributable_raw=True),
        CorpusSnapshot(provider="braket", backend_id="arn_aws_braket_x", captured_at=now,
                       content_hash="stale-identity", snapshot_json={},
                       derived_metrics={"eplg": 0.002, "source": "metriq"},
                       license_ref="CC-BY-4.0", redistributable_raw=True),
        CorpusSnapshot(provider="acme", backend_id="lab-1", captured_at=now,
                       content_hash="someone-else", snapshot_json={},
                       derived_metrics={"eplg": 0.002, "source": "acme-lab"},
                       license_ref="CC0", redistributable_raw=True),
    ])
    db.commit()
    result = sync_bundled(db, DATASETS)
    assert result["deleted"] == 2
    hashes = {r.content_hash for r in db.query(CorpusSnapshot)}
    assert "someone-else" in hashes
    assert not {"old-2024-06-01", "stale-identity"} & hashes


def test_merged_devices_keep_each_metrics_own_date(db):
    from app.services.corpus_sync import sync_bundled
    from provenova_crawler.corpus import fleet_leaderboard, list_devices

    sync_bundled(db, DATASETS)
    fez = next(d for d in list_devices(db) if (d["provider"], d["backend_id"]) == ("ibm", "ibm_fez"))
    # calibration (IBM) and benchmarks (Metriq) merge, each with its own date and source
    assert fez["metric_sources"]["median_2q_error"] == "ibm"
    assert fez["metric_sources"]["clops"] == "metriq"
    assert fez["metric_dates"]["median_2q_error"] < fez["metric_dates"]["clops"]
    board = fleet_leaderboard(db, metric="median_2q_error")
    assert [e["value"] for e in board] == sorted(e["value"] for e in board)
    top = board[0]
    assert top["captured_at"] == next(
        d for d in list_devices(db) if d["backend_id"] == top["backend_id"])["metric_dates"]["median_2q_error"]


# --- Metriq builder ------------------------------------------------------------

@pytest.mark.parametrize("folder, device, expected", [
    ("braket", "arn_aws_braket_us-west-1_device_qpu_rigetti_cepheus-1-108q", ("rigetti", "cepheus-1-108q")),
    ("aws", "rigetti_cepheus-1-108q", ("rigetti", "cepheus-1-108q")),
    ("aws", "arn_aws_braket_eu-north-1_device_qpu_iqm_garnet", ("iqm", "iqm_garnet")),
    ("aws", "arn_aws_braket_us-east-1_device_qpu_ionq_forte-1", ("ionq", "forte-1")),
    ("ibm", "ibm_fez", ("ibm", "ibm_fez")),
    ("quantinuum", "h2-2", ("quantinuum", "h2-2")),
])
def test_metriq_device_identity(folder, device, expected):
    from app.services.metriq import device_identity

    assert device_identity(folder, device) == expected


def test_metriq_series_is_per_day_and_never_carries_forward():
    from app.services.metriq import build_series

    files = {
        "metriq-gym/v0.6/ibm/ibm_x/2026-01-01_10-00-00_eplg_aaaaaa11.json":
            {"timestamp": "2026-01-01T10:00:00", "results": {"eplg_10": 0.003},
             "platform": {"device_metadata": {"num_qubits": 156}}},
        "metriq-gym/v0.6/ibm/ibm_x/2026-01-01_12-00-00_clops_bbbbbb22.json":
            {"timestamp": "2026-01-01T12:00:00", "results": {"clops_score": 300000}},
        "metriq-gym/v0.6/ibm/ibm_x/2026-02-01_09-00-00_eplg_cccccc33.json":
            {"timestamp": "2026-02-01T09:00:00", "results": {"eplg_10": 0.002}},
        "metriq-gym/v0.6/ibm/ibm_x/2026-02-01_09-00-00_qml_kernel_dddddd44.json":
            {"timestamp": "2026-02-01T09:00:00", "results": {"score": {"value": 1}}},
    }
    series, complete = build_series(list(files), lambda url: files[url.split("/main/", 1)[1]])
    assert complete and len(series) == 1
    snaps = series[0]["snapshots"]
    assert [s["captured_at"][:10] for s in snaps] == ["2026-01-01", "2026-02-01"]
    assert snaps[0]["derived_metrics"] == {"eplg": 0.003, "clops": 300000.0, "n_qubits": 156,
                                           "source": "metriq"}
    # February only re-measured EPLG: CLOPS is not carried forward
    assert snaps[1]["derived_metrics"] == {"eplg": 0.002, "source": "metriq"}
    assert {e["metric"] for e in snaps[0]["metric_provenance"]} == {"eplg", "clops"}


# --- tracker model ------------------------------------------------------------

def test_one_in_reads_naturally():
    from app.services.tracker import one_in

    assert one_in(0.3) == "about 1 in 3.3"
    assert one_in(0.01) == "about 1 in 100"
    assert one_in(0.00128) == "about 1 in 780"
    assert one_in(0.000084) == "about 1 in 11,900"
    assert one_in(0) == "—"


def test_tracker_headlines_come_from_the_data(db):
    from app.services import tracker
    from app.services.corpus_sync import sync_bundled

    sync_bundled(db, DATASETS)
    m = tracker.build(db, DATASETS)
    keys = [h["key"] for h in m["headlines"]]
    assert keys == ["gate_error", "qec", "ibm", "gap"]
    by_key = {h["key"]: h for h in m["headlines"]}
    # the first-experiment vs best-ever ratio is computed, not typed
    gate = m["charts"]["gate_error"]["points"]
    first = min(gate, key=lambda p: (p["year"], -p["value"]))
    best = min(gate, key=lambda p: p["value"])
    assert by_key["gate_error"]["value"].startswith(f"{float(f'{first['value'] / best['value']:.2g}'):,.0f}")
    assert by_key["gate_error"]["source_url"] == best["url"]
    # a surface code row credited only for its repetition codes doesn't count
    qec_rows = [p for p in m["charts"]["qec"]["points"] if p["below_threshold"]]
    assert all(p["code"].lower().split(" ")[0] in p["note"].lower() for p in qec_rows)
    assert by_key["qec"]["value"] == "2024"
    # IBM fleet: yearly medians, never plotted past the last snapshot
    fleet = m["charts"]["ibm_fleet"]
    assert fleet["yearly"][0]["year"] == 2019
    assert all(y["date"] <= m["fleet"]["last"] for y in fleet["yearly"])
    assert by_key["gap"]["source_url"].startswith("https://arxiv.org/abs/")


def test_tracker_without_experiments_degrades(db, tmp_path):
    from app.services import tracker

    (tmp_path / "context").mkdir()
    (tmp_path / "context" / "tracker.json").write_text(json.dumps({"rsa_2048_estimates": []}))
    m = tracker.build(db, tmp_path)
    assert m["headlines"] == [] and m["charts"] == {}


# --- plain language on device pages -------------------------------------------

def test_metric_readings_and_percentile():
    from app.services import explain

    assert explain.reading("median_2q_error", 0.0025) == "fails about 1 in 400"
    assert explain.reading("two_q_fidelity", 0.999) == "fails about 1 in 1,000"
    assert explain.reading("clops", 300000) is None
    p = explain.percentile("median_2q_error", 0.002, [0.001, 0.003, 0.004, 0.005, None])
    assert p == {"beats": 3, "of": 4, "pct": 75}
    assert explain.percentile("n_qubits", 156, [1, 2, 3, 4, 5]) is None
    assert explain.percentile("eplg", 0.002, [0.001, 0.003]) is None  # too few peers


def test_device_summary_is_plain_and_flags_vendor_claims():
    from app.services import explain

    d = {"provider": "ionq", "backend_id": "Forte", "source": "vendor-reported",
         "captured_at": "2026-07-03T00:00:00+00:00", "first_captured_at": "2026-07-03T00:00:00+00:00",
         "derived_metrics": {"two_q_fidelity": 0.996, "n_qubits": 36},
         "metric_dates": {"two_q_fidelity": "2026-07-03T00:00:00+00:00"}}
    text = " ".join(explain.device_summary(d, {}))
    assert "36-qubit IonQ quantum computer" in text
    assert "fails" not in text or "failing about 1 in 250" in text
    assert "claim, not an independent measurement" in text
