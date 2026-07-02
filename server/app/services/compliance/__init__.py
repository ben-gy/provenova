"""Compliance subsystem: frameworks-as-data, evidence auto-collection, alerts.

Framework definitions live as YAML under ``frameworks/`` and are loaded into the
DB by :mod:`.loader`. The :mod:`.rule_engine` interprets each control's
``evidence_rules`` predicate DSL against the immutable core record, auto-collects
:class:`EvidenceItem`\\s, rolls status up, and emits gap/drift alerts.
"""

from __future__ import annotations

from .describe import describe_predicate, describe_rule, rules_view
from .loader import (
    load_all_frameworks,
    load_framework,
    load_framework_file,
    load_framework_spec,
)
from .rule_engine import (
    enable_framework,
    evaluate_all,
    evaluate_framework,
    evaluate_predicate,
    resolve_path,
)

__all__ = [
    # loader
    "load_framework",
    "load_framework_file",
    "load_framework_spec",
    "load_all_frameworks",
    # rule engine
    "enable_framework",
    "evaluate_framework",
    "evaluate_all",
    "evaluate_predicate",
    "resolve_path",
    # describe
    "describe_rule",
    "describe_predicate",
    "rules_view",
]
