"""Benchmark a run against the fleet.

Scores a recorded run by the Hellinger fidelity of its measured distribution vs
the noiseless ideal for the same circuit, records a ``BenchmarkEntry`` (which
lights the "Benchmarked" badge) and ranks it within the workspace benchmark.
This is available from Free (feature ``compare_vs_fleet``).
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from provenova_core.models import Benchmark, BenchmarkEntry, Run
from provenova_core.reproduce.scoring import hellinger_fidelity
from provenova_core.simulate import bridge, engine

BENCHMARK_NAME = "Fidelity vs ideal"


def _get_or_create_benchmark(session: Session, workspace_id: str) -> Benchmark:
    bm = session.scalar(
        select(Benchmark).where(
            Benchmark.workspace_id == workspace_id, Benchmark.name == BENCHMARK_NAME
        )
    )
    if bm is None:
        bm = Benchmark(
            workspace_id=workspace_id,
            name=BENCHMARK_NAME,
            spec={"metric": "hellinger_fidelity_vs_ideal",
                  "description": "Hellinger fidelity of the recorded distribution vs the "
                                 "noiseless ideal for the same circuit."},
            scope="public_fleet",
        )
        session.add(bm)
        session.flush()
    return bm


def benchmark_run(session: Session, run: Run) -> BenchmarkEntry:
    bm = _get_or_create_benchmark(session, run.workspace_id)
    existing = session.scalar(
        select(BenchmarkEntry).where(
            BenchmarkEntry.benchmark_id == bm.id, BenchmarkEntry.run_id == run.id
        )
    )
    if existing is not None:
        return existing

    ir = bridge.dict_to_ir(json.loads(run.circuit.source))
    qc = bridge.qiskit_from_ir(ir)
    ideal = engine.ideal(qc)
    res = run.results[0]
    score = hellinger_fidelity(res.distribution, ideal)

    entry = BenchmarkEntry(benchmark_id=bm.id, run_id=run.id, backend_id=run.backend_id,
                           score=round(float(score), 6))
    session.add(entry)
    session.flush()

    # Re-rank the workspace benchmark (higher fidelity = better).
    ranked = session.scalars(
        select(BenchmarkEntry).where(BenchmarkEntry.benchmark_id == bm.id)
        .order_by(BenchmarkEntry.score.desc())
    ).all()
    for i, e in enumerate(ranked, start=1):
        e.rank = i
    session.flush()
    return entry


def entry_for(session: Session, run_id: str) -> BenchmarkEntry | None:
    return session.scalar(select(BenchmarkEntry).where(BenchmarkEntry.run_id == run_id))
