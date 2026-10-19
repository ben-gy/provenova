"""Run the data collectors now, instead of waiting for the daily cron.

Calls ``POST /internal/collect`` with ``QL_COLLECT_TOKEN`` (on provenova.net the
token is the Worker secret of the same name, also kept in the macOS keychain
as ``provenova-collect-token``). Collectors that aren't due are skipped unless
named in ``--force``.

Usage:
    QL_COLLECT_TOKEN=... python scripts/trigger_collect.py                 # whatever is due
    QL_COLLECT_TOKEN=... python scripts/trigger_collect.py --force all     # everything now
    QL_COLLECT_TOKEN=... python scripts/trigger_collect.py --force ibm,metriq
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", default="", help="comma-separated collector names, or 'all'")
    ap.add_argument("--base-url", default=os.environ.get("QL_BASE_URL", "https://provenova.net"))
    args = ap.parse_args()
    token = os.environ.get("QL_COLLECT_TOKEN")
    if not token:
        sys.exit("set QL_COLLECT_TOKEN")
    url = f"{args.base_url.rstrip('/')}/internal/collect"
    if args.force:
        url += "?" + urllib.parse.urlencode({"force": args.force})
    req = urllib.request.Request(url, method="POST", data=b"",
                                 headers={"Authorization": f"Bearer {token}",
                                          "User-Agent": "provenova-trigger/1.0"})
    with urllib.request.urlopen(req, timeout=600) as resp:  # noqa: S310 (our own host)
        print(json.dumps(json.loads(resp.read()), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
