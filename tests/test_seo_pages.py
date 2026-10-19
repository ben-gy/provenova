"""SEO surface tests: tracker, hardware/compare pages, sitemap, robots, feed.

Seeds a handful of corpus snapshots + a (retired) report directly, then
asserts rendering, canonical/OG hygiene, thin-page gating and sitemap content.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def seeded(client):
    """Corpus devices with known metric overlaps + one published, one draft report."""
    from app.db import SessionLocal
    from provenova_core.models import CorpusSnapshot, Report

    now = _dt.datetime.now(_dt.timezone.utc)

    def snap(provider, backend, dm, source, lic):
        return CorpusSnapshot(
            provider=provider, backend_id=backend, captured_at=now,
            content_hash=f"seo:{provider}:{backend}", snapshot_json={},
            derived_metrics={**dm, "source": source}, license_ref=lic,
            redistributable_raw=True)

    s = SessionLocal()
    s.add_all([
        # alpha & beta share two metrics -> comparable pair
        snap("acme", "alpha-1", {"two_q_fidelity": 0.99, "eplg": 0.002, "n_qubits": 12},
             "metriq", "CC-BY-4.0 · metriq.info"),
        snap("bcorp", "beta", {"two_q_fidelity": 0.98, "eplg": 0.004, "n_qubits": 20},
             "metriq", "CC-BY-4.0 · metriq.info"),
        # gamma shares only ONE metric with the others -> below compare threshold
        snap("ccorp", "gamma", {"two_q_fidelity": 0.97, "n_qubits": 5},
             "vendor-reported", "vendor-reported · ccorp.example"),
    ])
    s.add(Report(slug="state-of-quantum-2026-w27", title="State of Quantum — Week 27",
                 kind="weekly_fleet", body_md="# Fleet\n\nAll numbers from the corpus.",
                 body_html=None, meta_description="Weekly fleet report for week 27, 2026.",
                 published=True, published_at=now))
    s.add(Report(slug="draft-report-unpub", title="Draft", kind="weekly_fleet",
                 body_md="draft body", meta_description="Draft report.",
                 published=False))
    s.commit(); s.close()
    return True


# --- hardware pages ---------------------------------------------------------

def test_hardware_index_lists_devices(client, seeded):
    r = client.get("/hardware")
    assert r.status_code == 200
    for frag in ("alpha-1", "beta", "gamma", "acme", "bcorp"):
        assert frag in r.text, frag


def test_device_page_seo_hygiene(client, seeded):
    r = client.get("/hardware/acme/alpha-1")
    assert r.status_code == 200
    # exactly one canonical, one og:title, one meta description
    assert r.text.count('rel="canonical"') == 1
    assert r.text.count('property="og:title"') == 1
    assert r.text.count('name="description"') == 1
    assert 'http://testserver/hardware/acme/alpha-1' in r.text
    # JSON-LD parses and is a Dataset
    blobs = re.findall(r'<script type="application/ld\+json">(.*?)</script>', r.text, re.S)
    assert blobs, "expected JSON-LD"
    parsed = [json.loads(b) for b in blobs]
    assert any(p.get("@type") == "Dataset" for p in parsed)
    assert any(p.get("@type") == "BreadcrumbList" for p in parsed)


def test_device_meta_descriptions_are_unique(client, seeded):
    a = client.get("/hardware/acme/alpha-1").text
    b = client.get("/hardware/bcorp/beta").text
    da = re.search(r'name="description" content="([^"]+)"', a).group(1)
    db_ = re.search(r'name="description" content="([^"]+)"', b).group(1)
    assert da != db_


def test_device_page_mixed_case_redirects(client, seeded):
    r = client.get("/hardware/ACME/Alpha-1", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/hardware/acme/alpha-1"


def test_unknown_device_404(client, seeded):
    assert client.get("/hardware/acme/nope").status_code == 404


# --- compare pages ------------------------------------------------------------

def test_compare_canonical_pair_renders_shared_metrics_only(client, seeded):
    r = client.get("/hardware/acme/alpha-1/vs/bcorp/beta")
    assert r.status_code == 200
    assert "2Q gate fidelity" in r.text
    assert "EPLG" in r.text


def test_compare_reverse_order_redirects(client, seeded):
    r = client.get("/hardware/bcorp/beta/vs/acme/alpha-1", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/hardware/acme/alpha-1/vs/bcorp/beta"


def test_compare_thin_pair_404(client, seeded):
    # gamma shares only one metric with alpha-1 -> below MIN_COMPARE_OVERLAP
    assert client.get("/hardware/acme/alpha-1/vs/ccorp/gamma").status_code == 404


def test_compare_self_404(client, seeded):
    assert client.get("/hardware/acme/alpha-1/vs/acme/alpha-1").status_code == 404


# --- retired reports + tracker ------------------------------------------------------

def test_retired_report_urls_redirect_to_tracker(client, seeded):
    for path in ("/reports", "/reports/state-of-quantum-2026-w27", "/reports/draft-report-unpub"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 301, path
        assert r.headers["location"] == "/tracker"


def test_tracker_page_renders_sourced_headlines(client, seeded):
    r = client.get("/tracker")
    assert r.status_code == 200
    assert r.text.count('rel="canonical"') == 1
    assert 'http://testserver/tracker' in r.text
    assert "Is quantum computing actually getting better?" in r.text
    for label in ("Published experiments", "Resource estimate", "Programme announcement"):
        assert label in r.text, label
    blobs = re.findall(r'<script type="application/ld\+json">(.*?)</script>', r.text, re.S)
    assert any(json.loads(b).get("@type") == "Dataset" for b in blobs)


def test_tracker_json_cites_every_headline(client, seeded):
    data = client.get("/api/v1/tracker").json()
    assert data["headlines"], "expected headlines from the bundled datasets"
    for h in data["headlines"]:
        assert h["source_url"] and h["evidence"], h
    assert data["charts"]["gate_error"]["points"]
    assert data["experiments_source"]["commit"]


def test_feed_parses_and_has_absolute_urls(client, seeded):
    r = client.get("/reports/feed.xml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/atom+xml")
    root = ET.fromstring(r.text)
    ns = "{http://www.w3.org/2005/Atom}"
    # feed-level links (self + /tracker) plus any card entries are all absolute
    hrefs = [li.get("href") for li in root.findall(f"{ns}link")]
    hrefs += [li.get("href") for e in root.findall(f"{ns}entry") for li in e.findall(f"{ns}link")]
    assert "http://testserver/tracker" in hrefs
    assert all(h.startswith("http://testserver/") for h in hrefs)
    assert not any("/reports/" in h and not h.endswith("feed.xml") for h in hrefs)


# --- sitemap / robots -----------------------------------------------------------

def test_robots(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert "Disallow: /app/" in r.text
    assert "Sitemap: http://testserver/sitemap.xml" in r.text


def test_sitemap_content(client, seeded):
    # bust the TTL cache to see seeded content
    from app.web import seo as seo_mod

    seo_mod._cache.clear()
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    root = ET.fromstring(r.text)
    ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    locs = [u.find(f"{ns}loc").text for u in root.findall(f"{ns}url")]
    assert "http://testserver/hardware/acme/alpha-1" in locs
    assert "http://testserver/tracker" in locs
    assert not any("/reports/" in l for l in locs)
    assert "http://testserver/hardware/acme/alpha-1/vs/bcorp/beta" in locs
    # no reversed pair, no app pages, no drafts
    assert "http://testserver/hardware/bcorp/beta/vs/acme/alpha-1" not in locs
    assert not any("/app/" in l for l in locs)
    assert not any("draft-report-unpub" in l for l in locs)


def test_existing_pages_have_single_og_title(client):
    for path in ("/", "/tracker", "/leaderboard", "/hardware", "/docs"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.text.count('property="og:title"') == 1, path
