"""DOI minting: local PID provider, provider resolution, quota + failure paths.

Everything here is offline — the DataCite provider is exercised only through
fakes; no test may touch the network.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import cards as cards_svc
from app.services import doi as doi_svc


def _card(**over):
    base = dict(doi=None, pid=None, slug="test-card-abc123",
                title="Test card", license=None, workspace_id="ws1",
                summary={"run_hash": "a" * 64}, published_at=None)
    base.update(over)
    return SimpleNamespace(**base)


def _settings(**over):
    base = dict(doi_provider="", enable_doi=False,
                datacite_endpoint="https://api.test.datacite.org",
                datacite_repository_id="", datacite_password="",
                datacite_prefix="")
    base.update(over)
    return SimpleNamespace(**base)


# -- provider resolution -----------------------------------------------------

def test_default_is_local():
    assert isinstance(doi_svc.provider_for(_settings()), doi_svc.LocalPidProvider)


def test_datacite_without_creds_degrades_to_local():
    p = doi_svc.provider_for(_settings(doi_provider="datacite"))
    assert isinstance(p, doi_svc.LocalPidProvider)


def test_datacite_with_creds_selected():
    p = doi_svc.provider_for(_settings(doi_provider="datacite",
                                       datacite_repository_id="AB.CD",
                                       datacite_password="pw",
                                       datacite_prefix="10.1234"))
    assert isinstance(p, doi_svc.DataCiteProvider)


def test_legacy_enable_doi_means_datacite():
    # legacy boolean without creds still degrades safely to local
    p = doi_svc.provider_for(_settings(enable_doi=True))
    assert isinstance(p, doi_svc.LocalPidProvider)


def test_off_provider():
    p = doi_svc.provider_for(_settings(doi_provider="off"))
    assert isinstance(p, doi_svc.OffProvider)
    r = p.mint(_card(), "http://testserver")
    assert r.scheme == "pid" and r.identifier.startswith("ql:card:")


# -- local PID ---------------------------------------------------------------

def test_local_pid_stable_and_deterministic():
    card = _card()
    p = doi_svc.LocalPidProvider()
    a = p.mint(card, "http://testserver")
    b = p.mint(card, "http://testserver")
    assert a.identifier == b.identifier == f"ql:card:{'a' * 16}"
    assert a.scheme == "pid" and a.provider == "local"


def test_local_pid_falls_back_to_slug_without_hash():
    r = doi_svc.LocalPidProvider().mint(_card(summary={}), "http://testserver")
    assert r.identifier == "ql:card:test-card-abc123"


# -- mint-on-publish orchestration -------------------------------------------

class _FakeDataCite(doi_svc.DoiProvider):
    scheme, provider = "doi", "datacite"

    def __init__(self, fail=False):
        self.fail = fail
        self.minted = []

    def mint(self, card, base_url):
        if self.fail:
            raise doi_svc.DoiMintError("boom")
        self.minted.append(card.slug)
        return doi_svc.MintResult("10.1234/fake1", "doi", "datacite")


def _usage(at_cap):
    def fn(session, plan, workspace_id):
        return {"used": 5 if at_cap else 0, "cap": 5, "unlimited": False,
                "at_cap": at_cap, "pct": 100 if at_cap else 0}
    return fn


def test_mint_is_idempotent():
    card = _card(doi="10.1/exists")
    info = cards_svc._maybe_mint_doi(None, card, plan="free",
                                     provider=_FakeDataCite(), base_url="")
    assert info == {"status": "exists", "doi": "10.1/exists"}


def test_local_provider_yields_pid_only():
    card = _card()
    info = cards_svc._maybe_mint_doi(None, card, plan="free",
                                     provider=doi_svc.LocalPidProvider(), base_url="")
    assert info["status"] == "pid_only"
    assert card.pid.startswith("ql:card:") and card.doi is None


def test_quota_exceeded_skips_and_flags(monkeypatch):
    from app.services import limits
    monkeypatch.setattr(limits, "doi_usage", _usage(at_cap=True))
    card = _card()
    fake = _FakeDataCite()
    info = cards_svc._maybe_mint_doi(None, card, plan="free", provider=fake, base_url="")
    assert info["status"] == "quota_exceeded" and info["cap"] == 5
    assert card.doi is None and not fake.minted
    assert card.pid.startswith("ql:card:")  # publish still gets an identifier


def test_datacite_success_stores_doi(monkeypatch):
    from app.services import limits
    monkeypatch.setattr(limits, "doi_usage", _usage(at_cap=False))
    card = _card()
    info = cards_svc._maybe_mint_doi(None, card, plan="pro",
                                     provider=_FakeDataCite(), base_url="http://t")
    assert info["status"] == "minted" and card.doi == "10.1234/fake1"


def test_datacite_failure_degrades_to_pid(monkeypatch):
    from app.services import limits
    monkeypatch.setattr(limits, "doi_usage", _usage(at_cap=False))
    card = _card()
    info = cards_svc._maybe_mint_doi(None, card, plan="pro",
                                     provider=_FakeDataCite(fail=True), base_url="")
    assert info["status"] == "mint_failed" and "boom" in info["error"]
    assert card.doi is None and card.pid.startswith("ql:card:")


def test_datacite_payload_shape():
    p = doi_svc.DataCiteProvider(_settings(datacite_repository_id="AB.CD",
                                           datacite_password="pw",
                                           datacite_prefix="10.5555"))
    card = _card(license="CC-BY-4.0")
    body = p._payload(card, "http://testserver")
    attrs = body["data"]["attributes"]
    assert attrs["prefix"] == "10.5555"
    assert attrs["event"] == "publish"
    assert attrs["titles"] == [{"title": "Test card"}]
    assert attrs["url"] == "http://testserver/cards/test-card-abc123"
    assert attrs["rightsList"] == [{"rights": "CC-BY-4.0"}]
    assert ("a" * 64) in attrs["descriptions"][0]["description"]
