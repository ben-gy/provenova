"""Provenova public-QPU calibration crawler.

Pipeline (see the PRD §E7 "public corpus" epic)::

    source.fetch_raw()  ->  normalize.to_snapshot()  ->  hashing.calibration_hash()
                        ->  compliance.gate()        ->  corpus.ingest_snapshot()

Sources are pluggable (:class:`~provenova_crawler.sources.base.CalibrationSource`).
The default :class:`~provenova_crawler.sources.fixture_source.FixtureSource`
replays committed vendor-native JSON fixtures — no credentials, no network — and
iterates every longitudinal timepoint so the corpus is a real time-series.
"""

from __future__ import annotations

from .compliance import PROVIDER_POLICY, gate
from .corpus import (
    crawl_all,
    device_timeseries,
    fleet_leaderboard,
    ingest_snapshot,
)
from .normalize import CALIBRATION_SCHEMA_ID, to_snapshot
from .scheduler import build_scheduler, run_once
from .sources.base import CalibrationSource
from .sources.fixture_source import FixtureSource
from .sources.live_source import LiveSource

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "CalibrationSource",
    "FixtureSource",
    "LiveSource",
    "to_snapshot",
    "CALIBRATION_SCHEMA_ID",
    "PROVIDER_POLICY",
    "gate",
    "ingest_snapshot",
    "crawl_all",
    "fleet_leaderboard",
    "device_timeseries",
    "build_scheduler",
    "run_once",
]
