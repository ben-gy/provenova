"""Source abstraction for the calibration crawler.

A :class:`CalibrationSource` knows how to enumerate a provider's backends and
hand back *vendor-native* raw payloads plus provenance metadata. Everything
downstream (normalization, ToS gating, corpus dedup) is source-agnostic, so a
fixture replay and a live API poll are fully interchangeable.
"""

from __future__ import annotations

import abc


class CalibrationSource(abc.ABC):
    """Abstract calibration data source for one provider.

    Attributes
    ----------
    provider:
        Short vendor key, e.g. ``"ibm"``, ``"ionq"``, ``"braket"``.
    mode:
        ``"fixture"`` for the offline default, ``"live"`` for real API polls.
        Recorded in provenance so a corpus row's origin is auditable.
    """

    provider: str
    mode: str

    @abc.abstractmethod
    def list_backends(self) -> list[str]:
        """Return the backend/device ids this source can fetch."""
        raise NotImplementedError

    @abc.abstractmethod
    def fetch_raw(self, backend_id: str) -> tuple[dict, dict]:
        """Return one ``(raw_native, meta)`` calibration reading.

        ``raw_native`` is a single vendor-native calibration snapshot (already
        selected to one timepoint — sources that carry multiple timepoints
        expose each via :meth:`iter_readings`).

        ``meta`` carries provenance and must contain::

            {
                "updated_at": <ISO8601 str | None>,  # vendor's own stamp
                "source_url": <str>,                 # where it came from
                "license_ref": <str>,                # ToS / license identifier
            }
        """
        raise NotImplementedError

    def iter_readings(self, backend_id: str):
        """Yield every ``(raw_native, meta)`` timepoint for ``backend_id``.

        Longitudinal sources (fixtures with multiple snapshots, live sources
        replaying a history window) override this. The default yields the single
        reading returned by :meth:`fetch_raw` so simple sources need not.
        """
        yield self.fetch_raw(backend_id)
