"""End-to-end tests for the public-QPU calibration crawler.

Builds an in-memory DB via ``provenova_core.init_db``, replays the committed
vendor fixtures through the full ingest pipeline, and asserts:

* corpus snapshots are created for every device across every timepoint,
* content-hash dedup means a second sweep adds nothing,
* every normalized payload validates against ``calibration_1_0``,
* the ToS gate keeps only aggregate metrics for non-redistributable providers,
* ``fleet_leaderboard`` returns ranked devices and ``device_timeseries`` is
  longitudinal and ordered.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import provenova_core as qc
from provenova_core.models import CorpusSnapshot
from sqlalchemy import func, select

from provenova_crawler import (
    FixtureSource,
    build_scheduler,
    compliance,
    device_timeseries,
    fleet_leaderboard,
    run_once,
    to_snapshot,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
PROVIDERS = ["ibm", "ionq", "braket"]

# device_id -> provider, and expected distinct timepoints in each fixture.
EXPECTED_DEVICES = {
    "ibm_kyiv": ("ibm", 3),
    "ibm_sherbrooke": ("ibm", 3),
    "ionq_forte": ("ionq", 3),
    "Ankaa-3": ("braket", 3),
}


@pytest.fixture()
def session():
    engine = qc.init_db("sqlite:///:memory:")
    sf = qc.session_factory(engine)
    sess = sf()
    yield sess
    sess.close()


@pytest.fixture()
def sources():
    return [FixtureSource(p, FIXTURES_DIR) for p in PROVIDERS]


@pytest.fixture()
def schema_validator():
    schema = qc.load_schema("calibration_1_0")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _count(session) -> int:
    return session.execute(select(func.count()).select_from(CorpusSnapshot)).scalar_one()


# ---------------------------------------------------------------------------


def test_fixtures_load_and_list_backends(sources):
    listed = {b for s in sources for b in s.list_backends()}
    assert listed == set(EXPECTED_DEVICES)


def test_run_once_creates_snapshots_across_timepoints(session, sources):
    summary = run_once(session, sources)
    # No source errored.
    assert all(v >= 0 for v in summary.values()), summary

    total_expected = sum(n for _, n in EXPECTED_DEVICES.values())
    assert _count(session) == total_expected

    for device, (provider, n_points) in EXPECTED_DEVICES.items():
        rows = session.execute(
            select(CorpusSnapshot).where(
                CorpusSnapshot.provider == provider,
                CorpusSnapshot.backend_id == device,
            )
        ).scalars().all()
        assert len(rows) == n_points, f"{device}: {len(rows)} != {n_points}"
        # Distinct content hashes => genuine drift between timepoints.
        assert len({r.content_hash for r in rows}) == n_points


def test_dedup_second_run_adds_nothing(session, sources):
    run_once(session, sources)
    first = _count(session)
    summary = run_once(session, sources)
    assert first == _count(session)
    # Every provider reports zero *new* snapshots on the second sweep.
    assert all(v == 0 for v in summary.values()), summary


def test_normalized_payloads_validate(schema_validator):
    for provider in PROVIDERS:
        src = FixtureSource(provider, FIXTURES_DIR)
        for backend_id in src.list_backends():
            for raw, _meta in src.iter_readings(backend_id):
                payload = to_snapshot(provider, raw)
                errors = sorted(schema_validator.iter_errors(payload), key=str)
                assert not errors, f"{provider}/{backend_id}: {[e.message for e in errors]}"
                assert payload["schema"] == "qlprov/calibration/1.0"
                assert payload["backend"]["kind"] == "qpu"


def test_unit_conversions_ibm():
    src = FixtureSource("ibm", FIXTURES_DIR)
    raw, _ = next(src.iter_readings("ibm_kyiv"))
    payload = to_snapshot("ibm", raw)
    q0 = payload["qubits"][0]
    # 0.00012843 s -> ~128.43 us
    assert 90 < q0["T1_us"] < 170
    # 4.9238412e9 Hz -> ~4.92 GHz
    assert 4.0 < q0["frequency_ghz"] < 6.0
    cx = [g for g in payload["gates"] if g["gate"] == "cx"][0]
    # 4.622e-07 s -> 462.2 ns
    assert 100 < cx["length_ns"] < 800
    assert 0.006 <= cx["error"] <= 0.012


def test_ionq_all_to_all_gap_flagged():
    src = FixtureSource("ionq", FIXTURES_DIR)
    raw, _ = next(src.iter_readings("ionq_forte"))
    payload = to_snapshot("ionq", raw)
    reasons = {g.get("reason") for g in payload["gaps"]}
    assert "all_to_all" in reasons
    # fidelity -> error mapping
    ms = [g for g in payload["gates"] if g["gate"] == "ms"][0]
    assert 0.0 < ms["error"] < 0.05


def test_tos_gate_strips_raw_for_ibm(session, sources):
    run_once(session, sources)
    ibm_row = session.execute(
        select(CorpusSnapshot).where(CorpusSnapshot.provider == "ibm").limit(1)
    ).scalar_one()
    # IBM disallows raw redistribution -> aggregate-only view, no per-qubit rows.
    assert ibm_row.redistributable_raw is False
    assert ibm_row.snapshot_json.get("aggregate_only") is True
    assert "qubits" not in ibm_row.snapshot_json
    assert ibm_row.snapshot_json.get("fleet_metrics")
    # Braket permits raw redistribution -> full payload retained.
    braket_row = session.execute(
        select(CorpusSnapshot).where(CorpusSnapshot.provider == "braket").limit(1)
    ).scalar_one()
    assert braket_row.redistributable_raw is True
    assert "qubits" in braket_row.snapshot_json


def test_compliance_gate_return_contract():
    payload = {"fleet_metrics": {"median_2q_error": 0.01}}
    ok, lic = compliance.gate("braket", payload)
    assert ok is True and lic == "aws-braket-tos-2024"
    ok, lic = compliance.gate("ibm", payload)
    assert ok is False and lic == "ibm-quantum-tos-2024"


def test_fleet_leaderboard_ranks_devices(session, sources):
    run_once(session, sources)
    board = fleet_leaderboard(session, metric="median_2q_error")
    assert board, "leaderboard should not be empty"
    # One entry per device that has the metric.
    assert len(board) >= 3
    # Ascending for an error metric; ranks are 1..n.
    values = [e["value"] for e in board]
    assert values == sorted(values)
    assert [e["rank"] for e in board] == list(range(1, len(board) + 1))

    # A higher-is-better metric ranks descending.
    fid_board = fleet_leaderboard(session, metric="best_2q_fidelity")
    fid_values = [e["value"] for e in fid_board]
    assert fid_values == sorted(fid_values, reverse=True)


def test_fleet_leaderboard_new_metrics_and_provenance(session):
    """Cross-vendor benchmark metrics rank correctly with mixed availability,
    and each entry surfaces its source + licence."""
    import datetime as _dt

    def _snap(provider, backend, dm, source, lic):
        return CorpusSnapshot(
            provider=provider, backend_id=backend,
            captured_at=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
            content_hash=f"{provider}:{backend}:{source}", snapshot_json={},
            derived_metrics={**dm, "source": source}, license_ref=lic,
            redistributable_raw=(source != "vendor-reported"))

    session.add_all([
        _snap("quantinuum", "H2-1", {"two_q_fidelity": 0.9987, "quantum_volume": 1048576},
              "metriq", "CC-BY-4.0 · metriq.info"),
        _snap("ionq", "Forte", {"algorithmic_qubits": 36, "two_q_fidelity": 0.996},
              "vendor-reported", "vendor-reported · ionq.com"),
        _snap("rigetti", "Ankaa-3", {"two_q_fidelity": 0.995}, "vendor-reported",
              "vendor-reported · rigetti.com"),
        _snap("iqm", "Garnet", {"n_qubits": 20}, "iqm-zenodo", "CC-BY-4.0 · zenodo.org"),
    ])
    session.commit()

    board = fleet_leaderboard(session, metric="two_q_fidelity")
    # Only the three devices reporting the metric appear (IQM row dropped).
    assert [e["backend_id"] for e in board] == ["H2-1", "Forte", "Ankaa-3"]
    assert [e["value"] for e in board] == sorted([e["value"] for e in board], reverse=True)
    # Provenance surfaced.
    top = board[0]
    assert top["source"] == "metriq"
    assert "CC-BY-4.0" in top["license_ref"]
    # algorithmic_qubits (higher-is-better) ranks IonQ first, others dropped.
    aq = fleet_leaderboard(session, metric="algorithmic_qubits")
    assert [e["backend_id"] for e in aq] == ["Forte"]


def test_device_timeseries_is_longitudinal(session, sources):
    run_once(session, sources)
    series = device_timeseries(session, "ibm", "ibm_kyiv")
    assert len(series) == 3
    times = [s["captured_at"] for s in series]
    assert times == sorted(times)  # oldest first
    assert len({s["content_hash"] for s in series}) == 3


def test_build_scheduler_smoke(session, sources):
    # Scheduler must build without starting / making network calls.
    scheduler = build_scheduler(lambda: session, sources, interval_minutes=30)
    job = scheduler.get_job("provenova-crawl")
    assert job is not None
