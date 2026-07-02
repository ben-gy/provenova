"""Background worker: crawl the public corpus + continuous compliance monitoring.

Run:  PYTHONPATH=server python -m app.worker
In docker-compose this is the `worker` service.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from sqlalchemy import select

from quantumledger_core.models import ComplianceFramework, Workspace

from .db import FRAMEWORKS_DIR, SessionLocal, bootstrap
from .services import compliance as comp

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("quantumledger.worker")

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures"


def crawl_once() -> int:
    try:
        from quantumledger_crawler.corpus import crawl_all
        from quantumledger_crawler.sources.fixture_source import FixtureSource
    except Exception as e:  # noqa: BLE001
        log.warning("crawler unavailable: %s", e)
        return 0
    s = SessionLocal()
    total = 0
    try:
        for prov in ("ibm", "ionq", "braket"):
            if (FIXTURES / prov).exists():
                try:
                    total += len(crawl_all(s, FixtureSource(prov, str(FIXTURES))))
                except Exception as e:  # noqa: BLE001
                    log.warning("crawl %s failed: %s", prov, e)
        s.commit()
    finally:
        s.close()
    return total


def monitor_once() -> None:
    s = SessionLocal()
    try:
        for ws in s.scalars(select(Workspace)):
            try:
                comp.evaluate_all(s, ws)
            except Exception as e:  # noqa: BLE001
                log.warning("monitor %s failed: %s", ws.id, e)
        s.commit()
    finally:
        s.close()


def main(interval_seconds: int = 1800) -> None:
    s = SessionLocal()
    bootstrap(s)
    s.close()
    log.info("worker started; interval=%ss", interval_seconds)
    try:
        from apscheduler.schedulers.background import BackgroundScheduler

        sched = BackgroundScheduler()
        sched.add_job(crawl_once, "interval", seconds=interval_seconds, next_run_time=None)
        sched.add_job(monitor_once, "interval", seconds=interval_seconds)
        sched.start()
        # initial pass
        log.info("initial crawl: %d snapshots", crawl_once())
        monitor_once()
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        log.info("worker stopping")


if __name__ == "__main__":
    main()
