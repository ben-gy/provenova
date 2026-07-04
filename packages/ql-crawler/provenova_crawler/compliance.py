"""Per-provider Terms-of-Service redistribution policy.

Vendor calibration data is published under terms that differ on whether the
*raw* payload may be stored and/or redistributed, versus whether only *derived
aggregate* metrics may. The corpus honours this: when ``redistribute_raw`` is
False, the redistributable view of a snapshot keeps only derived/aggregate
metrics and strips per-qubit / per-gate raw detail.

These entries encode a conservative reading of each vendor's public ToS as of
2024 and are the single place to update if terms change. They are policy
metadata for Provenova's own corpus governance, not legal advice.
"""

from __future__ import annotations

from copy import deepcopy

PROVIDER_POLICY: dict[str, dict] = {
    "ibm": {
        "store_raw": True,
        "redistribute_raw": False,
        "redistribute_aggregate": True,
        "license_ref": "ibm-quantum-tos-2024",
        "tos_url": "https://quantum.ibm.com/terms",
    },
    "ionq": {
        "store_raw": True,
        "redistribute_raw": False,
        "redistribute_aggregate": True,
        "license_ref": "ionq-tos-2024",
        "tos_url": "https://ionq.com/terms-of-service",
    },
    "braket": {
        "store_raw": True,
        "redistribute_raw": True,
        "redistribute_aggregate": True,
        "license_ref": "aws-braket-tos-2024",
        "tos_url": "https://aws.amazon.com/service-terms/",
    },
    "rigetti": {
        "store_raw": True,
        "redistribute_raw": True,
        "redistribute_aggregate": True,
        "license_ref": "aws-braket-tos-2024",
        "tos_url": "https://aws.amazon.com/service-terms/",
    },
}

# Conservative default for any provider not yet in the table: keep private,
# aggregate-only redistribution.
_DEFAULT_POLICY = {
    "store_raw": True,
    "redistribute_raw": False,
    "redistribute_aggregate": True,
    "license_ref": "unknown-tos",
    "tos_url": None,
}


def policy_for(provider: str) -> dict:
    """Return the ToS policy for ``provider`` (falling back to a safe default)."""
    return PROVIDER_POLICY.get(provider, _DEFAULT_POLICY)


def aggregate_view(snapshot: dict) -> dict:
    """Return a redistributable, aggregate-only projection of ``snapshot``.

    Drops per-qubit / per-gate raw detail, keeping backend identity, the capture
    time, fleet-level aggregate metrics, provenance and gap flags.
    """
    view = {
        "schema": snapshot.get("schema"),
        "backend": deepcopy(snapshot.get("backend")),
        "captured_at": snapshot.get("captured_at"),
        "fleet_metrics": deepcopy(snapshot.get("fleet_metrics", {})),
        "provenance": deepcopy(snapshot.get("provenance", {})),
        "gaps": deepcopy(snapshot.get("gaps", [])),
        "aggregate_only": True,
    }
    return view


def gate(provider: str, snapshot: dict) -> tuple[bool, str]:
    """Apply the ToS gate for ``provider``.

    Returns ``(redistributable_raw, license_ref)``. The snapshot is inspected but
    not mutated; callers use :func:`aggregate_view` to build the stripped-down
    redistributable payload when ``redistributable_raw`` is False.
    """
    pol = policy_for(provider)
    return bool(pol["redistribute_raw"]), pol["license_ref"]
