"""APScheduler-driven periodic crawl loop.

``build_scheduler`` wires a background scheduler that, every ``interval_minutes``,
opens a fresh session and runs :func:`crawl_all` for each configured source. Each
source is guarded independently: a failing source (bad credentials, vendor API
down, malformed payload) is logged and skipped — it never crashes the loop or
prevents the other sources from being crawled.

``run_once`` performs a single synchronous sweep with the same guarding and is
what the CLI and tests call.
"""

from __future__ import annotations

import logging

from .corpus import crawl_all

logger = logging.getLogger("quantumledger.crawler.scheduler")


def run_once(session, sources) -> dict[str, int]:
    """Run one crawl sweep over ``sources`` using ``session``.

    Returns a ``{provider: n_new_snapshots}`` summary. A source that raises is
    logged and recorded as ``-1`` rather than aborting the sweep.
    """
    summary: dict[str, int] = {}
    for source in sources:
        provider = getattr(source, "provider", repr(source))
        try:
            created = crawl_all(session, source)
            summary[provider] = len(created)
            logger.info("crawl ok provider=%s new_snapshots=%d", provider, len(created))
        except Exception:  # noqa: BLE001 - loop must survive any source failure
            logger.exception("crawl failed provider=%s (skipped)", provider)
            summary[provider] = -1
    return summary


def _tick(session_factory, sources) -> None:
    """One scheduled tick: fresh session, guarded sweep, always closes."""
    session = session_factory()
    try:
        run_once(session, sources)
    except Exception:  # noqa: BLE001 - defensive: never let a tick kill the loop
        logger.exception("crawl tick failed")
    finally:
        try:
            session.close()
        except Exception:  # noqa: BLE001
            logger.exception("failed to close crawl session")


def build_scheduler(session_factory, sources, interval_minutes: int = 30):
    """Build (but do not start) a :class:`BackgroundScheduler` running the crawl.

    Parameters
    ----------
    session_factory:
        Zero-arg callable returning a new SQLAlchemy ``Session`` per tick.
    sources:
        Iterable of :class:`~quantumledger_crawler.sources.base.CalibrationSource`.
    interval_minutes:
        Minutes between sweeps (default 30).

    Call ``.start()`` on the returned scheduler to begin; use :func:`run_once`
    for one-shot / test invocations.
    """
    from apscheduler.schedulers.background import BackgroundScheduler

    sources = list(sources)
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        _tick,
        trigger="interval",
        minutes=interval_minutes,
        args=[session_factory, sources],
        id="quantumledger-crawl",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    return scheduler
