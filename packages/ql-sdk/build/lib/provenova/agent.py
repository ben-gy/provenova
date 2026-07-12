"""Capture agent: the one-line decorator and the context manager (E1.4).

Both resolve to the same ``CaptureAgent``. A capture failure degrades to a logged
warning + a ``partial`` record — it never breaks the user's job.
"""

from __future__ import annotations

import functools
import logging
import sys

from .config import load_config
from .registry import registry
from .store import LocalLedger

_log = logging.getLogger("provenova.capture")


def _is_qiskit_circuit(obj) -> bool:
    return type(obj).__module__.startswith("qiskit") and type(obj).__name__ == "QuantumCircuit"


def _looks_like_backend(obj) -> bool:
    return hasattr(obj, "run") and (
        hasattr(obj, "configuration") or hasattr(obj, "name") or hasattr(obj, "options")
    )


class CaptureAgent:
    def __init__(self, ledger: LocalLedger | None = None):
        self._ledger = ledger

    @property
    def ledger(self) -> LocalLedger:
        if self._ledger is None:
            self._ledger = LocalLedger(load_config().db_url)
        return self._ledger

    def record(self, *, circuit=None, backend=None, job=None, result=None, project=None,
               shots=None) -> str | None:
        try:
            probe = backend or job or result or circuit
            connector = registry().detect(probe) if probe is not None else None
            if connector is None:
                connector = registry().get("simulator")
            bundle = connector.extract(circuit=circuit, backend=backend, job=job, result=result)
            if shots and not bundle.shots:
                bundle.shots = shots
            if bundle.circuit is None:
                _log.warning("provenova: no circuit captured; skipping record")
                return None
            return self.ledger.record_bundle(bundle, project=project)
        except Exception as e:  # never break the user's workflow
            _log.warning("provenova capture failed (ignored): %s", e)
            return None


class _RunRecorder:
    def __init__(self, ctx: "CaptureContext"):
        self._ctx = ctx
        self.circuit = None
        self.backend = None
        self.job = None
        self.result = None
        self.id: str | None = None

    def record(self, *, circuit=None, backend=None, job=None, result=None):
        if circuit is not None:
            self.circuit = circuit
        if backend is not None:
            self.backend = backend
        if job is not None:
            self.job = job
        if result is not None:
            self.result = result
        return self


class CaptureContext:
    """Returned by :func:`capture`; usable as a decorator OR a context manager."""

    def __init__(self, project=None, backend=None, shots=None, agent: CaptureAgent | None = None):
        self.project = project
        self.backend = backend
        self.shots = shots
        self.agent = agent or CaptureAgent()
        self._recorder: _RunRecorder | None = None

    # -- context manager --------------------------------------------------
    def __enter__(self) -> _RunRecorder:
        self._recorder = _RunRecorder(self)
        if self.backend is not None:
            self._recorder.backend = self.backend
        return self._recorder

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            return False
        r = self._recorder
        rid = self.agent.record(
            circuit=r.circuit, backend=r.backend, job=r.job, result=r.result,
            project=self.project, shots=self.shots,
        )
        if r is not None:
            r.id = rid
        return False

    # -- decorator --------------------------------------------------------
    def __call__(self, fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            retval, sniffed = _run_and_sniff(fn, args, kwargs)
            circuit = self._find(sniffed, _is_qiskit_circuit)
            backend = self.backend or self._find(sniffed, _looks_like_backend)
            job = result = None
            if retval is not None:
                if hasattr(retval, "get_counts"):
                    result = retval
                elif hasattr(retval, "result"):
                    job = retval
            if backend is None and job is not None and hasattr(job, "backend"):
                try:
                    backend = job.backend()
                except Exception:
                    backend = None
            self.agent.record(circuit=circuit, backend=backend, job=job, result=result,
                              project=self.project, shots=self.shots)
            return retval

        return wrapper

    @staticmethod
    def _find(locals_dict, pred):
        for v in (locals_dict or {}).values():
            try:
                if pred(v):
                    return v
            except Exception:
                continue
        return None


def _run_and_sniff(fn, args, kwargs):
    """Run ``fn`` and snapshot its return-frame locals (best-effort)."""
    captured: dict = {}
    code = fn.__code__

    def _profiler(frame, event, arg):
        if event == "return" and frame.f_code is code:
            try:
                captured.update(dict(frame.f_locals))
            except Exception:
                pass
        return None

    old = sys.getprofile()
    sys.setprofile(_profiler)
    try:
        retval = fn(*args, **kwargs)
    finally:
        sys.setprofile(old)
    return retval, captured


def capture(project=None, backend=None, shots=None, agent: CaptureAgent | None = None) -> CaptureContext:
    """One-line capture. Use as ``@ql.capture(project=...)`` or
    ``with ql.capture(project=...) as run: run.record(circuit=, backend=, job=)``."""
    return CaptureContext(project=project, backend=backend, shots=shots, agent=agent)
