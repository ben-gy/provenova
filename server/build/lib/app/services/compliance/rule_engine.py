"""The compliance rule engine.

Interprets a control's ``evidence_rules`` (the predicate DSL) against the
immutable core record, auto-collects :class:`EvidenceItem`\\s that point back into
that record, rolls per-rule / per-control / per-framework status up, and emits
continuous-monitoring alerts (``gap`` for a failing control, ``drift`` for
evidence that exists but has aged out of its freshness window).

The framework is pure data: this module is the only place that knows how to turn
those data rules into passes, evidence and alerts.

Predicate operators (each a single-key dict):

* ``exists: "<path>"`` — path may be ``a | b``; true if any alternative is non-null.
* ``non_empty: "<path>"`` — true if the resolved value is non-null and non-empty.
* ``equals: {field: "<path>", value: <v>}``
* ``in: {field: "<path>", set: [..]}``
* ``all_present: ["<path>", ...]`` — every path is non-null/non-empty.
* ``count_where: {relation: "<path-to-list>", where: <predicate>, op: <cmp>, value: <n>}``

Sources: ``run | result | calibration | circuit | card | reproduction | workspace``.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from provenova_core.models import (
    CalibrationSnapshot,
    Circuit,
    ComplianceAlert,
    ComplianceFramework,
    Control,
    EvidenceItem,
    ReproductionEvent,
    Result,
    ResultCard,
    Run,
    Workspace,
    WorkspaceFramework,
)

# --------------------------------------------------------------------------- #
# Path resolution
# --------------------------------------------------------------------------- #

# ORM-relationship / attribute names are resolved dynamically via getattr, so the
# same dotted path (e.g. "run.calibration.payload") walks the relationship graph.


def _get(obj: Any, attr: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(attr)
    return getattr(obj, attr, None)


def resolve_path(obj_map: dict[str, Any], path: str) -> Any:
    """Resolve a dotted ``path`` against ``obj_map`` (source-type name -> object).

    The leading token names the source (e.g. ``run``); remaining tokens are walked
    with ``getattr`` (or dict lookup). Returns ``None`` if any hop is missing.
    """
    parts = path.strip().split(".")
    root = obj_map.get(parts[0])
    value: Any = root
    for part in parts[1:]:
        value = _get(value, part)
        if value is None:
            return None
    return value


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (str, list, dict, tuple, set)):
        return len(value) == 0
    return False


def _resolve_alternation(obj_map: dict[str, Any], path: str) -> Any:
    """Resolve ``a | b`` alternation, returning the first non-empty value."""
    for alt in path.split("|"):
        value = resolve_path(obj_map, alt.strip())
        if not _is_empty(value):
            return value
    return None


# --------------------------------------------------------------------------- #
# Predicate evaluation
# --------------------------------------------------------------------------- #

_CMP = {
    ">=": lambda a, b: a >= b,
    ">": lambda a, b: a > b,
    "<=": lambda a, b: a <= b,
    "<": lambda a, b: a < b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def evaluate_predicate(predicate: dict, obj_map: dict[str, Any]) -> bool:
    """Evaluate a single-key predicate dict against the resolved object map."""
    if not predicate:
        return True
    op, spec = next(iter(predicate.items()))

    if op == "exists":
        return not _is_empty(_resolve_alternation(obj_map, spec))

    if op == "non_empty":
        return not _is_empty(_resolve_alternation(obj_map, spec))

    if op == "equals":
        return resolve_path(obj_map, spec["field"]) == spec["value"]

    if op == "in":
        return resolve_path(obj_map, spec["field"]) in spec["set"]

    if op == "all_present":
        return all(not _is_empty(resolve_path(obj_map, p)) for p in spec)

    if op == "count_where":
        relation = resolve_path(obj_map, spec["relation"]) or []
        where = spec.get("where")
        matched = 0
        for item in relation:
            child_map = dict(obj_map)
            child_map["reproduction"] = item  # relations are currently reproductions
            child_map["item"] = item
            if where is None or evaluate_predicate(where, child_map):
                matched += 1
        cmp = _CMP.get(spec.get("op", ">="))
        return bool(cmp(matched, spec.get("value", 1)))

    raise ValueError(f"Unknown predicate operator: {op!r}")


# --------------------------------------------------------------------------- #
# Source fetching + content-hash / ref helpers
# --------------------------------------------------------------------------- #

# Maps a rule `source` token to (ref_type stored on EvidenceItem).
_REF_TYPE = {
    "run": "run",
    "result": "result",
    "calibration": "calibration_snapshot",
    "circuit": "circuit",
    "card": "result_card",
    "reproduction": "reproduction_event",
    "workspace": "workspace",
}


def _workspace_runs(session: Session, workspace: Workspace) -> list[Run]:
    return list(session.scalars(select(Run).where(Run.workspace_id == workspace.id)))


def _fetch_candidates(session: Session, workspace: Workspace, source: str) -> list[dict[str, Any]]:
    """Return candidate rows for a source as a list of resolved object maps.

    Each object map is keyed by every source-type name reachable from the row so
    a rule targeting ``run`` can also reference ``run.circuit`` etc. For the
    ``workspace`` source there is exactly one row carrying the eagerly-loaded
    ``reproduction_events`` relation used by ``count_where``.
    """
    if source == "workspace":
        events = list(
            session.scalars(
                select(ReproductionEvent)
                .join(Run, ReproductionEvent.original_run_id == Run.id)
                .where(Run.workspace_id == workspace.id)
            )
        )
        # Attach as an in-memory relation the DSL can resolve via the path map.
        return [{"workspace": _WorkspaceView(workspace, events), "target": workspace}]

    if source == "run":
        return [
            {"run": r, "circuit": r.circuit, "calibration": r.calibration, "target": r}
            for r in _workspace_runs(session, workspace)
        ]

    if source == "result":
        runs = _workspace_runs(session, workspace)
        maps: list[dict[str, Any]] = []
        for r in runs:
            for res in r.results:
                maps.append({"result": res, "run": r, "target": res})
        return maps

    if source == "circuit":
        # Distinct circuits referenced by this workspace's runs.
        seen: dict[str, dict[str, Any]] = {}
        for r in _workspace_runs(session, workspace):
            if r.circuit is not None and r.circuit.id not in seen:
                seen[r.circuit.id] = {"circuit": r.circuit, "run": r, "target": r.circuit}
        return list(seen.values())

    if source == "calibration":
        seen: dict[str, dict[str, Any]] = {}
        for r in _workspace_runs(session, workspace):
            cal = r.calibration
            if cal is not None and cal.id not in seen:
                seen[cal.id] = {"calibration": cal, "run": r, "target": cal}
        return list(seen.values())

    if source == "card":
        cards = list(
            session.scalars(
                select(ResultCard).where(ResultCard.workspace_id == workspace.id)
            )
        )
        return [{"card": c, "run": c and _card_run(session, c), "target": c} for c in cards]

    if source == "reproduction":
        events = list(
            session.scalars(
                select(ReproductionEvent)
                .join(Run, ReproductionEvent.original_run_id == Run.id)
                .where(Run.workspace_id == workspace.id)
            )
        )
        return [{"reproduction": e, "target": e} for e in events]

    raise ValueError(f"Unknown evidence source: {source!r}")


def _card_run(session: Session, card: ResultCard) -> Run | None:
    return session.get(Run, card.run_id)


class _WorkspaceView:
    """Thin wrapper exposing the workspace + its verified-able reproductions.

    ``count_where`` resolves ``workspace.reproduction_events`` against this.
    """

    def __init__(self, workspace: Workspace, reproduction_events: list[ReproductionEvent]):
        self._ws = workspace
        self.reproduction_events = reproduction_events

    def __getattr__(self, name: str) -> Any:  # delegate everything else to the row
        return getattr(self._ws, name)


def _content_hash(source: str, target: Any) -> str | None:
    """The immutable content hash of the evidence target, for tamper-evidence."""
    if source == "run":
        return getattr(target, "run_hash", None)
    if source == "calibration":
        return getattr(target, "content_sha256", None)
    if source == "circuit":
        return getattr(target, "content_sha256", None)
    if source == "card":
        return getattr(target, "card_sha256", None)
    if source == "result":
        return getattr(target, "counts_sha256", None)
    if source == "reproduction":
        return getattr(target, "reproduced_run_id", None)
    if source == "workspace":
        return getattr(target, "chain_head", None)
    return None


def _ref_id(source: str, target: Any, workspace: Workspace) -> str:
    if source == "workspace":
        return workspace.id
    return getattr(target, "id", workspace.id)


# --------------------------------------------------------------------------- #
# Freshness
# --------------------------------------------------------------------------- #


def _as_aware(value: Any) -> _dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=_dt.timezone.utc)
        return value
    return None


def _calibration_captured_at(obj_map: dict[str, Any]) -> _dt.datetime | None:
    cal = obj_map.get("calibration")
    if cal is not None:
        return _as_aware(getattr(cal, "captured_at", None))
    return None


def _check_freshness(freshness: dict, obj_map: dict[str, Any]) -> bool:
    """True if the evidence is within its freshness window.

    ``relative_to`` names the reference instant (e.g. ``run.finished_at``); the
    evidence's own timestamp is the bound calibration's ``captured_at`` when the
    source involves a calibration, else the reference instant itself.
    """
    max_age = freshness.get("max_age_hours")
    if not max_age:
        return True
    reference = _as_aware(resolve_path(obj_map, freshness.get("relative_to", "run.finished_at")))
    captured = _calibration_captured_at(obj_map) or reference
    if reference is None or captured is None:
        return True  # cannot judge -> don't penalise
    age = reference - captured
    return age <= _dt.timedelta(hours=float(max_age))


# --------------------------------------------------------------------------- #
# Rule evaluation
# --------------------------------------------------------------------------- #


def _collect_value(rule: dict, obj_map: dict[str, Any]) -> dict:
    collect = rule.get("collect")
    if not collect:
        return {}
    field = collect.get("field")
    value = _resolve_alternation(obj_map, field) if field else None
    return {"label": collect.get("label", field), "field": field, "value": value}


def _upsert_evidence(
    session: Session,
    *,
    control: Control,
    workspace: Workspace,
    rule_id: str,
    source: str,
    target: Any,
    value: dict,
) -> EvidenceItem:
    ref_id = _ref_id(source, target, workspace)
    existing = session.scalar(
        select(EvidenceItem).where(
            EvidenceItem.control_id == control.id,
            EvidenceItem.source_ref_id == ref_id,
            EvidenceItem.rule_id == rule_id,
        )
    )
    content_hash = _content_hash(source, target)
    if existing is not None:
        existing.source_content_hash = content_hash
        existing.value = value
        session.flush()
        return existing
    item = EvidenceItem(
        control_id=control.id,
        workspace_id=workspace.id,
        rule_id=rule_id,
        source_ref_type=_REF_TYPE.get(source, source),
        source_ref_id=ref_id,
        source_content_hash=content_hash,
        value=value,
    )
    session.add(item)
    session.flush()
    return item


def _evaluate_rule(
    session: Session, workspace: Workspace, control: Control, rule: dict
) -> dict:
    """Evaluate one evidence rule; upsert evidence for passing rows.

    Returns ``{rule_id, passed, evidence_count, drift}`` where ``drift`` lists
    ref-ids whose predicate passed but whose freshness window has expired.
    """
    rule_id = rule["id"]
    source = rule["source"]
    predicate = rule.get("predicate", {})
    freshness = rule.get("freshness")
    cardinality = rule.get("cardinality") or {}
    min_count = int(cardinality.get("min_count", 1))

    candidates = _fetch_candidates(session, workspace, source)
    passing = 0
    drift_refs: list[str] = []
    for obj_map in candidates:
        target = obj_map.get("target")
        if not evaluate_predicate(predicate, obj_map):
            continue
        if freshness is not None and not _check_freshness(freshness, obj_map):
            # Predicate holds but evidence has aged out -> drift.
            drift_refs.append(_ref_id(source, target, workspace))
            continue
        value = _collect_value(rule, obj_map)
        _upsert_evidence(
            session,
            control=control,
            workspace=workspace,
            rule_id=rule_id,
            source=source,
            target=target,
            value=value,
        )
        passing += 1

    return {
        "rule_id": rule_id,
        "passed": passing >= min_count,
        "evidence_count": passing,
        "drift": drift_refs,
    }


# --------------------------------------------------------------------------- #
# Alerts (continuous monitoring)
# --------------------------------------------------------------------------- #


def _open_alert(
    session: Session,
    *,
    workspace: Workspace,
    framework: ComplianceFramework,
    control: Control | None,
    kind: str,
    message: str,
) -> None:
    """Create an alert unless an identical unresolved one already exists."""
    control_id = control.id if control is not None else None
    existing = session.scalar(
        select(ComplianceAlert).where(
            ComplianceAlert.workspace_id == workspace.id,
            ComplianceAlert.framework_id == framework.id,
            ComplianceAlert.control_id == control_id,
            ComplianceAlert.kind == kind,
            ComplianceAlert.resolved.is_(False),
        )
    )
    if existing is not None:
        existing.message = message
        session.flush()
        return
    session.add(
        ComplianceAlert(
            workspace_id=workspace.id,
            framework_id=framework.id,
            control_id=control_id,
            kind=kind,
            message=message,
            resolved=False,
        )
    )
    session.flush()


def _resolve_alerts(
    session: Session,
    *,
    workspace: Workspace,
    framework: ComplianceFramework,
    control: Control,
    kind: str,
) -> None:
    """Mark any open alerts of ``kind`` for this control as resolved."""
    for alert in session.scalars(
        select(ComplianceAlert).where(
            ComplianceAlert.workspace_id == workspace.id,
            ComplianceAlert.framework_id == framework.id,
            ComplianceAlert.control_id == control.id,
            ComplianceAlert.kind == kind,
            ComplianceAlert.resolved.is_(False),
        )
    ):
        alert.resolved = True
    session.flush()


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def enable_framework(
    session: Session, workspace: Workspace, framework: ComplianceFramework
) -> WorkspaceFramework:
    """Enable a framework on a workspace (idempotent)."""
    wf = session.scalar(
        select(WorkspaceFramework).where(
            WorkspaceFramework.workspace_id == workspace.id,
            WorkspaceFramework.framework_id == framework.id,
        )
    )
    if wf is None:
        wf = WorkspaceFramework(
            workspace_id=workspace.id, framework_id=framework.id, status="unknown"
        )
        session.add(wf)
        session.flush()
    return wf


def evaluate_framework(
    session: Session, workspace: Workspace, framework: ComplianceFramework
) -> dict:
    """Evaluate every control of ``framework`` for ``workspace``.

    Auto-collects evidence, rolls status up, writes the ``WorkspaceFramework``
    row, and emits gap/drift alerts. Returns a summary dict.
    """
    wf = enable_framework(session, workspace, framework)

    controls_summary: list[dict] = []
    status_detail: dict[str, dict] = {}
    total_evidence = 0
    all_pass = True

    controls = list(
        session.scalars(select(Control).where(Control.framework_id == framework.id))
    )
    for control in controls:
        rules = control.evidence_rules or []
        rule_results = [_evaluate_rule(session, workspace, control, r) for r in rules]
        failing_rule_ids = [r["rule_id"] for r in rule_results if not r["passed"]]
        control_evidence = sum(r["evidence_count"] for r in rule_results)
        control_pass = len(failing_rule_ids) == 0
        total_evidence += control_evidence
        if not control_pass:
            all_pass = False

        # Gap alerts: failing control. Resolve when it passes again.
        if not control_pass:
            _open_alert(
                session,
                workspace=workspace,
                framework=framework,
                control=control,
                kind="gap",
                message=(
                    f"Control {control.key} is failing: "
                    f"rules {', '.join(failing_rule_ids)} unsatisfied."
                ),
            )
        else:
            _resolve_alerts(
                session, workspace=workspace, framework=framework, control=control, kind="gap"
            )

        # Drift alerts: predicate passed but evidence expired freshness.
        drift_refs = [ref for r in rule_results for ref in r["drift"]]
        if drift_refs:
            _open_alert(
                session,
                workspace=workspace,
                framework=framework,
                control=control,
                kind="drift",
                message=(
                    f"Control {control.key}: {len(drift_refs)} evidence item(s) "
                    f"aged out of the freshness window."
                ),
            )
        else:
            _resolve_alerts(
                session, workspace=workspace, framework=framework, control=control, kind="drift"
            )

        detail = {
            "status": "pass" if control_pass else "gap",
            "failing_rule_ids": failing_rule_ids,
            "evidence_count": control_evidence,
        }
        status_detail[control.key] = detail
        controls_summary.append({"key": control.key, "title": control.title, **detail})

    status = "pass" if all_pass else "gap"
    wf.status = status
    wf.last_evaluated_at = _dt.datetime.now(_dt.timezone.utc)
    wf.status_detail = status_detail
    session.flush()

    alerts = [
        {"kind": a.kind, "control_id": a.control_id, "message": a.message}
        for a in session.scalars(
            select(ComplianceAlert).where(
                ComplianceAlert.workspace_id == workspace.id,
                ComplianceAlert.framework_id == framework.id,
                ComplianceAlert.resolved.is_(False),
            )
        )
    ]

    return {
        "framework_key": framework.key,
        "status": status,
        "controls": controls_summary,
        "evidence_count": total_evidence,
        "alerts": alerts,
    }


def evaluate_all(session: Session, workspace: Workspace) -> list[dict]:
    """Evaluate every framework enabled on ``workspace``."""
    enabled = session.scalars(
        select(WorkspaceFramework).where(WorkspaceFramework.workspace_id == workspace.id)
    )
    out: list[dict] = []
    for wf in enabled:
        framework = session.get(ComplianceFramework, wf.framework_id)
        if framework is not None:
            out.append(evaluate_framework(session, workspace, framework))
    return out
