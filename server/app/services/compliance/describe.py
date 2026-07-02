"""Human-readable rendering of the compliance evidence-rule DSL.

The rule engine interprets ``control.evidence_rules`` (a predicate DSL) but the
raw rule dicts are opaque to a user staring at a failing control. These helpers
turn a rule (and its predicate) into a plain-English sentence so the UI can
explain *what a check requires* — and therefore what is missing when it fails.

Nothing here mutates state or re-implements evaluation; it is pure formatting of
data that already lives on the ``Control`` rows loaded from the framework YAML.
See :mod:`.rule_engine` for the authoritative predicate semantics.
"""

from __future__ import annotations

from typing import Any

# Sources map to a friendly noun for the sentence subject.
_SOURCE_NOUN = {
    "run": "run",
    "result": "measurement result",
    "calibration": "calibration snapshot",
    "circuit": "circuit",
    "card": "published result card",
    "reproduction": "reproduction event",
    "workspace": "workspace",
}


def _fmt_path(path: Any) -> str:
    """Render a dotted path, expanding ``a | b`` alternations to 'a or b'."""
    if path is None:
        return "the value"
    return " or ".join(p.strip() for p in str(path).split("|") if p.strip())


def describe_predicate(predicate: dict | None) -> str:
    """Return a plain-English clause describing a single predicate."""
    if not predicate:
        return "a condition is met"
    (op, arg), = predicate.items()

    if op == "exists":
        return f"{_fmt_path(arg)} is present"
    if op == "non_empty":
        return f"{_fmt_path(arg)} is present and not empty"
    if op == "equals":
        field = _fmt_path(arg.get("field")) if isinstance(arg, dict) else _fmt_path(arg)
        value = arg.get("value") if isinstance(arg, dict) else None
        return f"{field} equals {value!r}"
    if op == "in":
        field = _fmt_path(arg.get("field")) if isinstance(arg, dict) else _fmt_path(arg)
        allowed = ", ".join(str(v) for v in (arg.get("set") or [])) if isinstance(arg, dict) else ""
        return f"{field} is one of [{allowed}]"
    if op == "all_present":
        fields = ", ".join(_fmt_path(p) for p in (arg or []))
        return f"all of {fields} are present"
    if op == "count_where":
        arg = arg or {}
        relation = _fmt_path(arg.get("relation"))
        inner = describe_predicate(arg.get("where"))
        cmp = arg.get("op", "")
        value = arg.get("value")
        return f"the number of {relation} where {inner} is {cmp} {value}"
    # Unknown operator — fall back to the raw key so nothing silently vanishes.
    return f"{op}: {arg}"


def describe_rule(rule: dict | None) -> str:
    """Return a full sentence describing one evidence rule."""
    if not rule:
        return "An unspecified check."
    source = rule.get("source", "record")
    noun = _SOURCE_NOUN.get(source, source)
    clause = describe_predicate(rule.get("predicate"))

    card = rule.get("cardinality") or {}
    min_count = int(card.get("min_count", 1) or 1)
    if min_count > 1:
        subject = f"At least {min_count} {noun}s must satisfy"
    else:
        subject = f"Each {noun} must satisfy"
    parts = [f"{subject}: {clause}."]

    fresh = rule.get("freshness")
    if isinstance(fresh, dict) and fresh.get("max_age_hours"):
        hours = int(fresh["max_age_hours"])
        rel = fresh.get("relative_to")
        window = f"~{round(hours / 24)} day(s)" if hours >= 48 else f"{hours} hour(s)"
        rel_txt = f" (relative to {_fmt_path(rel)})" if rel else ""
        parts.append(f"Evidence must be no older than {window}{rel_txt}.")

    return " ".join(parts)


def rules_view(control) -> list[dict]:
    """Structured per-rule view for a control: ``[{id, description}]``.

    ``control`` is a :class:`~quantumledger_core.models.Control`; its
    ``evidence_rules`` is the list of rule dicts loaded from the framework YAML.
    """
    out: list[dict] = []
    for rule in (getattr(control, "evidence_rules", None) or []):
        out.append({"id": rule.get("id", "?"), "description": describe_rule(rule)})
    return out
