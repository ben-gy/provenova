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
                datacite_prefix="",
                zenodo_endpoint="https://sandbox.zenodo.org", zenodo_token="")
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


# -- Zenodo provider (fake httpx, no network) --------------------------------

class _Resp:
    def __init__(self, payload=None, status=200):
        self._payload = payload or {}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeZenodoClient:
    """Scriptable stand-in for httpx.Client covering the 4-step deposit."""

    calls = []          # (method, url) across the last mint, for assertions
    uploaded = {}       # filename -> bytes
    fail_at = None      # "create" | "upload" | "metadata" | "publish" | None

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, json=None):
        _FakeZenodoClient.calls.append(("POST", url))
        if url.endswith("/api/deposit/depositions"):
            if _FakeZenodoClient.fail_at == "create":
                return _Resp(status=500)
            return _Resp({"id": 4242, "links": {"bucket": "https://zen/bucket/xyz"}}, 201)
        if url.endswith("/actions/publish"):
            if _FakeZenodoClient.fail_at == "publish":
                return _Resp(status=500)
            return _Resp({"doi": "10.5072/zenodo.4242",
                          "links": {"record_html": "https://sandbox.zenodo.org/record/4242"}}, 202)
        return _Resp(status=404)

    def put(self, url, json=None, content=None):
        _FakeZenodoClient.calls.append(("PUT", url))
        if "/bucket/" in url:
            if _FakeZenodoClient.fail_at == "upload":
                return _Resp(status=500)
            _FakeZenodoClient.uploaded[url.rsplit("/", 1)[-1]] = content
            return _Resp({}, 201)
        # metadata PUT
        if _FakeZenodoClient.fail_at == "metadata":
            return _Resp(status=400)
        return _Resp({}, 200)

    def delete(self, url):
        _FakeZenodoClient.calls.append(("DELETE", url))
        return _Resp({}, 204)


@pytest.fixture
def fake_zenodo(monkeypatch):
    import httpx
    _FakeZenodoClient.calls = []
    _FakeZenodoClient.uploaded = {}
    _FakeZenodoClient.fail_at = None
    monkeypatch.setattr(httpx, "Client", _FakeZenodoClient)
    return _FakeZenodoClient


def _zenodo_settings():
    return _settings(zenodo_token="tok", zenodo_endpoint="https://sandbox.zenodo.org")


def test_zenodo_provider_resolution():
    assert doi_svc.zenodo_provider(_settings()) is None                       # no token
    assert isinstance(doi_svc.zenodo_provider(_zenodo_settings()), doi_svc.ZenodoProvider)


def test_provider_for_still_local_with_zenodo_token():
    # a Zenodo token must NOT change the auto-publish provider (opt-in boundary)
    assert isinstance(doi_svc.provider_for(_zenodo_settings()), doi_svc.LocalPidProvider)


def test_zenodo_metadata_shape():
    p = doi_svc.ZenodoProvider(_zenodo_settings())
    card = _card(license="CC-BY-4.0", summary={"run_hash": "b" * 64,
                                               "backend": {"vendor": "ibm", "name": "kyiv"},
                                               "verdict": "drifted"})
    meta = p._metadata(card, "http://testserver")["metadata"]
    assert meta["upload_type"] == "dataset"
    assert meta["title"] == "Test card"
    assert meta["access_right"] == "open"
    assert meta["license"] == "cc-by-4.0"                    # lowercased for Zenodo
    assert meta["prereserve_doi"] is True
    assert meta["related_identifiers"] == [
        {"relation": "isIdenticalTo",
         "identifier": "http://testserver/cards/test-card-abc123", "scheme": "url"}]
    assert ("b" * 64) in meta["description"] and "drifted" in meta["description"]


def test_zenodo_mint_happy_path(fake_zenodo):
    p = doi_svc.ZenodoProvider(_zenodo_settings())
    card = _card(summary={"run_hash": "c" * 64})
    card.__dict__["_provenance_json"] = b'{"schema": "qlprov/run/1.0", "run_hash": "ccc"}'
    result = p.mint(card, "http://testserver")
    assert result.identifier == "10.5072/zenodo.4242"
    assert result.scheme == "doi" and result.provider == "zenodo"
    assert result.url == "https://sandbox.zenodo.org/record/4242"
    methods = [m for m, _ in fake_zenodo.calls]
    assert methods == ["POST", "PUT", "PUT", "POST"]          # create, upload, metadata, publish
    assert b"qlprov/run/1.0" in list(fake_zenodo.uploaded.values())[0]
    assert "DELETE" not in methods                            # no rollback on success


def test_zenodo_mint_deletes_draft_on_failure(fake_zenodo):
    fake_zenodo.fail_at = "metadata"                          # fail at step 3
    p = doi_svc.ZenodoProvider(_zenodo_settings())
    with pytest.raises(doi_svc.DoiMintError):
        p.mint(_card(), "http://testserver")
    assert any(m == "DELETE" for m, _ in fake_zenodo.calls)   # draft cleaned up


def test_zenodo_hide_is_noop():
    import httpx

    called = {"n": 0}
    orig = httpx.Client
    try:
        httpx.Client = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no HTTP"))  # noqa: E731
        doi_svc.ZenodoProvider(_zenodo_settings()).hide("10.5072/zenodo.1")  # must not touch network
    finally:
        httpx.Client = orig
