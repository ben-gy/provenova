"""LocalLedger — the offline, no-account store.

Backed directly by the ql-core schema, so the local store and the hosted store
are literally the same schema (PRD E2.5). Records are written through the shared
``runner.record_run`` path, so a locally-captured run has the identical
immutable, Merkle-bound, hash-chained provenance as a hosted one.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

import provenova_core as qc
from provenova_core.models import Run, Workspace, bootstrap_local
from provenova_core.provenance import build_run_doc
from provenova_core.reproduce import runner

from .connectors.base import CaptureBundle


class LocalLedger:
    def __init__(self, db_url: str):
        Path(db_url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True) if db_url.startswith(
            "sqlite:///"
        ) and "/" in db_url.replace("sqlite:///", "") else None
        self.engine = qc.init_db(db_url)
        self._Session = qc.session_factory(self.engine)

    def session(self) -> Session:
        return self._Session()

    def workspace(self, s: Session) -> Workspace:
        return bootstrap_local(s)

    # -- capture -----------------------------------------------------------
    def record_bundle(self, bundle: CaptureBundle, *, project: str | None = None) -> str:
        s = self.session()
        try:
            ws = self.workspace(s)
            s.commit()
            backend_spec = {
                "vendor": bundle.provider,
                "name": bundle.backend_name,
                "kind": bundle.backend_kind,
                "basis_gates": bundle.basis_gates,
                "coupling_map": bundle.coupling_map,
            }
            run = runner.record_run(
                s,
                workspace=ws,
                qc=bundle.circuit,
                backend_spec=backend_spec,
                calibration_payload=bundle.calibration,
                shots=bundle.shots or 1024,
                precomputed_counts=bundle.counts,
                project=project,
                capture_status="partial" if bundle.gaps else "complete",
            )
            if bundle.gaps:
                run.gaps = [g for g in bundle.gaps]
            s.commit()
            return run.id
        finally:
            s.close()

    # -- read --------------------------------------------------------------
    def list_runs(self, limit: int = 50) -> list[dict]:
        s = self.session()
        try:
            runs = s.scalars(select(Run).order_by(Run.created_at.desc()).limit(limit)).all()
            return [
                {
                    "id": r.id,
                    "project": r.project,
                    "vendor": r.backend.vendor,
                    "backend": r.backend.name,
                    "shots": r.shots,
                    "status": r.status,
                    "capture_status": r.capture_status,
                    "run_hash": r.run_hash,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in runs
            ]
        finally:
            s.close()

    def get_run_doc(self, run_id: str) -> dict | None:
        s = self.session()
        try:
            run = s.get(Run, run_id)
            return build_run_doc(run) if run else None
        finally:
            s.close()

    def export_bundle(self, run_id: str) -> dict | None:
        """A full push bundle: provenance doc + the payloads the doc references."""
        s = self.session()
        try:
            run = s.get(Run, run_id)
            if run is None:
                return None
            res = run.results[0]
            return {
                "provenance": build_run_doc(run),
                "circuit": {"fmt": run.circuit.fmt, "source": run.circuit.source,
                            "n_qubits": run.circuit.n_qubits, "n_clbits": run.circuit.n_clbits},
                "compilation": None if not run.compilation else {
                    "fmt": run.compilation.fmt, "source": run.compilation.transpiled_source,
                    "optimization_level": run.compilation.optimization_level,
                    "transpiler_options": run.compilation.transpiler_options,
                    "metrics": run.compilation.metrics, "toolchain": run.compilation.toolchain},
                "backend": {"vendor": run.backend.vendor, "name": run.backend.name,
                            "kind": run.backend.kind, "n_qubits": run.backend.n_qubits,
                            "basis_gates": run.backend.basis_gates, "coupling_map": run.backend.coupling_map},
                "calibration": run.calibration.payload,
                "result": {"counts": res.counts, "distribution": res.distribution,
                           "counts_sha256": res.counts_sha256, "shots": res.shots, "retained": res.retained},
                "run": {"shots": run.shots, "seed_simulator": run.seed_simulator,
                        "seed_transpiler": run.seed_transpiler, "execution_params": run.execution_params,
                        "project": run.project, "capture_status": run.capture_status,
                        "provenance_schema_version": run.provenance_schema_version},
            }
        finally:
            s.close()

    def reproduce(self, run_id: str, *, days: float = 30.0, profile: str = "typical"):
        s = self.session()
        try:
            run = s.get(Run, run_id)
            if run is None:
                raise KeyError(run_id)
            ws = s.get(Workspace, run.workspace_id)
            new_run, event = runner.reproduce_run(s, run, workspace=ws, days=days, profile=profile)
            s.commit()
            from provenova_core.reproduce.report import build_report

            report = build_report(run, new_run, event)
            return report
        finally:
            s.close()

    def pending_push(self) -> list[str]:
        s = self.session()
        try:
            return [r.id for r in s.scalars(select(Run).order_by(Run.created_at)).all()]
        finally:
            s.close()
