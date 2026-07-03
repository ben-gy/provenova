"""Fetch openly-licensed IQM calibration data from Zenodo into datasets/iqm/.

Zenodo exposes a stable REST API (https://developers.zenodo.org). This script:

  1. searches Zenodo for a matching record (default: IQM Garnet calibration),
  2. VERIFIES the record's licence is open (CC-BY / CC-BY-SA / CC0) — and refuses
     to write anything otherwise, so we never redistribute non-open data,
  3. downloads the calibration file(s) and normalises each into a
     ``qlprov/corpus-record/1.0`` file under ``datasets/iqm/``.

Nothing is invented: every metric is derived from the downloaded file, and each
output record cites the Zenodo record URL + licence in ``raw_ref`` / ``license_ref``.

Usage:
    python scripts/fetch_zenodo_iqm.py                         # default query
    python scripts/fetch_zenodo_iqm.py --record 1234567        # a specific record id
    python scripts/fetch_zenodo_iqm.py --query "IQM Garnet calibration"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "datasets" / "iqm"
ZENODO_API = "https://zenodo.org/api/records"

# Licences we may redistribute. Zenodo returns an id like "cc-by-4.0".
_OPEN_LICENCES = re.compile(r"^(cc-by(-sa)?-\d|cc0-\d|cc-by-4)", re.I)


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "provenova-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (trusted host)
        return json.loads(resp.read().decode())


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "provenova-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return resp.read()


def _licence_id(record: dict) -> str | None:
    meta = record.get("metadata", {})
    lic = meta.get("license")
    if isinstance(lic, dict):
        return lic.get("id") or lic.get("identifier")
    return lic


def find_record(query: str, record_id: str | None) -> dict:
    if record_id:
        return _get(f"{ZENODO_API}/{record_id}")
    hits = _get(f"{ZENODO_API}?q={urllib.parse.quote(query)}&size=10&sort=mostrecent")
    for rec in hits.get("hits", {}).get("hits", []):
        if _OPEN_LICENCES.match((_licence_id(rec) or "")):
            return rec
    raise SystemExit(f"No openly-licensed Zenodo record found for query: {query!r}")


def normalise(record: dict) -> list[dict]:
    """Turn a Zenodo record + its calibration files into corpus-record dicts.

    IQM calibration formats vary; we store the raw payload under ``calibration``
    when it parses as JSON with qubit/gate structure, else keep the file bytes'
    summary. The seed loader reduces ``calibration`` to derived metrics.
    """
    lic = _licence_id(record) or "unknown"
    if not _OPEN_LICENCES.match(lic):
        raise SystemExit(f"Record licence {lic!r} is not open — refusing to redistribute.")
    landing = record.get("links", {}).get("html") or record.get("doi_url", "")
    license_ref = f"{lic.upper()} · zenodo.org"
    out: list[dict] = []
    for f in record.get("files", []):
        name = f.get("key", "")
        if not name.endswith(".json"):
            continue
        try:
            payload = json.loads(_download(f["links"]["self"]).decode())
        except Exception as e:  # noqa: BLE001
            print(f"  skip {name}: {e}", file=sys.stderr)
            continue
        # Best-effort: if it looks like a qlprov calibration, keep it as `calibration`.
        looks_calibration = isinstance(payload, dict) and (
            "qubits" in payload or "gates" in payload or "backend" in payload)
        rec = {
            "schema": "qlprov/corpus-record/1.0",
            "provider": "iqm",
            "backend_id": payload.get("backend", {}).get("name", "IQM-Garnet")
            if isinstance(payload.get("backend"), dict) else "IQM-Garnet",
            "captured_at": record.get("metadata", {}).get("publication_date", "") + "T00:00:00+00:00",
            "source": "iqm-zenodo",
            "license_ref": license_ref,
            "raw_ref": landing,
            "redistributable_raw": True,
            "note": record.get("metadata", {}).get("title", ""),
        }
        if looks_calibration:
            rec["calibration"] = payload
        else:
            rec["derived_metrics"] = payload if isinstance(payload, dict) else {}
        out.append(rec)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", default="IQM Garnet calibration")
    ap.add_argument("--record", default=None, help="explicit Zenodo record id")
    args = ap.parse_args()

    record = find_record(args.query, args.record)
    lic = _licence_id(record)
    print(f"record: {record.get('metadata', {}).get('title')!r}  licence={lic}")
    if not _OPEN_LICENCES.match(lic or ""):
        raise SystemExit(f"licence {lic!r} not open (need CC-BY/CC-BY-SA/CC0) — aborting.")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    records = normalise(record)
    for i, rec in enumerate(records):
        path = OUT_DIR / f"iqm_{rec['backend_id'].lower().replace(' ', '_')}_{i}.json"
        path.write_text(json.dumps(rec, indent=2))
        print(f"  wrote {path.relative_to(REPO)}")
    if not records:
        print("  no JSON calibration files in record — nothing written.")


if __name__ == "__main__":
    main()
