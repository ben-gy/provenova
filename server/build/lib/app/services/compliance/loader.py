"""Load framework definitions (YAML data) into the database.

A framework is authored as a YAML file under ``frameworks/`` (see
``fair-1.0.yaml`` etc.). Loading is idempotent and upserts by
``(framework.key, framework.version)`` and, within a framework, by
``control.key``. Controls carry their ``evidence_rules`` verbatim — the
:mod:`rule_engine` interprets them at evaluation time, so the framework is pure
data with no code.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from provenova_core.models import ComplianceFramework, Control

# Repo-root ``frameworks/`` directory: this file lives at
# <repo>/server/app/services/compliance/loader.py -> up 5 parents to <repo>.
FRAMEWORKS_DIR = Path(__file__).resolve().parents[4] / "frameworks"


def load_framework_spec(spec: dict, session: Session) -> ComplianceFramework:
    """Upsert a single framework (+ its controls) from a parsed spec dict."""
    key = spec["key"]
    version = str(spec.get("version", "1.0"))

    framework = session.scalar(
        select(ComplianceFramework).where(
            ComplianceFramework.key == key,
            ComplianceFramework.version == version,
        )
    )
    if framework is None:
        framework = ComplianceFramework(key=key, version=version, name=spec.get("name", key))
        session.add(framework)
        session.flush()

    framework.name = spec.get("name", key)
    framework.jurisdiction = spec.get("jurisdiction")
    framework.description = spec.get("description")
    framework.spec = spec.get("spec")

    # Index existing controls by key so re-loading updates in place.
    existing = {c.key: c for c in framework.controls}
    for ctrl_spec in spec.get("controls", []) or []:
        ckey = ctrl_spec["key"]
        control = existing.get(ckey)
        if control is None:
            control = Control(framework_id=framework.id, key=ckey, title=ctrl_spec.get("title", ckey))
            session.add(control)
        control.title = ctrl_spec.get("title", ckey)
        control.requirement_text = ctrl_spec.get("requirement_text")
        control.severity = ctrl_spec.get("severity", "medium")
        control.evidence_rules = ctrl_spec.get("evidence_rules") or []
        control.remediation = ctrl_spec.get("remediation")

    session.flush()
    return framework


def load_framework_file(path: str | Path, session: Session) -> ComplianceFramework:
    """Load a framework from a YAML file path."""
    spec = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return load_framework_spec(spec, session)


def load_framework(key: str, session: Session, *, version: str | None = None) -> ComplianceFramework:
    """Load a bundled framework by ``key`` (e.g. ``"fair"``) from ``frameworks/``.

    Matches ``<key>-<version>.yaml`` if ``version`` is given, else the first file
    whose parsed ``key`` matches.
    """
    if version is not None:
        path = FRAMEWORKS_DIR / f"{key}-{version}.yaml"
        if path.exists():
            return load_framework_file(path, session)
    for path in sorted(FRAMEWORKS_DIR.glob("*.yaml")):
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        if spec.get("key") == key and (version is None or str(spec.get("version")) == version):
            return load_framework_spec(spec, session)
    raise FileNotFoundError(f"No framework with key={key!r} version={version!r} in {FRAMEWORKS_DIR}")


def load_all_frameworks(session: Session, *, directory: str | Path | None = None) -> list[ComplianceFramework]:
    """Load every ``*.yaml`` framework in ``frameworks/`` (or ``directory``)."""
    base = Path(directory) if directory is not None else FRAMEWORKS_DIR
    out: list[ComplianceFramework] = []
    for path in sorted(base.glob("*.yaml")):
        out.append(load_framework_file(path, session))
    return out
