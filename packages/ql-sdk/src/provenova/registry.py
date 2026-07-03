"""Connector plugin discovery via importlib.metadata entry points (E1.5)."""

from __future__ import annotations

import logging
from importlib.metadata import entry_points

from .connectors.base import Connector

_log = logging.getLogger("provenova.registry")
GROUP = "provenova.connectors"

# connector entry-point name -> the pip extra that provides its vendor SDK
CONNECTOR_EXTRAS = {
    "simulator": "aer",
    "qiskit_runtime": "qiskit_runtime",
    "braket": "braket",
    "azure_quantum": "azure",
}


def extra_for(name: str) -> str | None:
    return CONNECTOR_EXTRAS.get(name)


class ConnectorRegistry:
    def __init__(self) -> None:
        self._connectors: dict[str, Connector] = {}
        self._unavailable: dict[str, str] = {}  # name -> reason (e.g. missing SDK)
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        try:
            eps = entry_points(group=GROUP)
        except TypeError:  # pragma: no cover - older API
            eps = entry_points().get(GROUP, [])
        for ep in eps:
            try:
                cls = ep.load()
                self._connectors[ep.name] = cls()
            except Exception as e:  # missing vendor SDK -> record so we can guide the user
                self._unavailable[ep.name] = str(e)
                _log.debug("connector %s unavailable: %s", ep.name, e)
        self._loaded = True

    def available(self) -> list[str]:
        self.load()
        return sorted(self._connectors)

    def unavailable(self) -> dict[str, str]:
        """Connectors that failed to load, name -> reason (usually a missing extra)."""
        self.load()
        return dict(self._unavailable)

    def get(self, name: str) -> Connector:
        self.load()
        return self._connectors[name]

    def detect(self, obj: object) -> Connector | None:
        """Find the connector that claims ``obj``. Simulator is tried last so a
        real backend always wins over the always-matching simulator."""
        self.load()
        ordered = sorted(self._connectors.values(), key=lambda c: (c.name == "simulator", c.name))
        for c in ordered:
            try:
                if c.claims(obj):
                    return c
            except Exception:
                continue
        return None


_REGISTRY = ConnectorRegistry()


def registry() -> ConnectorRegistry:
    return _REGISTRY
