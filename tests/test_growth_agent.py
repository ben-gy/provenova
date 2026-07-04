"""Tests for the standalone growth agent's deterministic logic.

The LLM and all HTTP are mocked — these lock down qlir validation, the ISO-week
slug, abstract reconstruction, and (critically) that a published card's citation
metadata comes from the deterministically-fetched paper, never from the model.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import growth_agent as g  # noqa: E402

BELL = {"schema": "qlir/1.0", "n_qubits": 2,
        "gates": [{"name": "h", "qubits": [0], "params": []},
                  {"name": "cx", "qubits": [0, 1], "params": []}]}


def test_validate_qlir_accepts_bell():
    assert g.validate_qlir(BELL) is None


@pytest.mark.parametrize("circ,frag", [
    ({"n_qubits": 11, "gates": [{"name": "h", "qubits": [0], "params": []}]}, "n_qubits"),
    ({"n_qubits": 1, "gates": [{"name": "measure_all", "qubits": [0], "params": []}]}, "not allowed"),
    ({"n_qubits": 1, "gates": [{"name": "rz", "qubits": [0], "params": [float("inf")]}]}, "finite"),
    ({"n_qubits": 2, "gates": [{"name": "cx", "qubits": [0, 0], "params": []}]}, "distinct"),
    ({"n_qubits": 1, "gates": []}, "gates must be"),
])
def test_validate_qlir_rejects(circ, frag):
    err = g.validate_qlir(circ)
    assert err and frag in err


def test_abstract_from_inverted():
    inv = {"Quantum": [0], "is": [1], "cool": [2]}
    assert g._abstract_from_inverted(inv) == "Quantum is cool"
    assert g._abstract_from_inverted(None) == ""


def test_week_slug_shape():
    import re
    assert re.fullmatch(r"state-of-quantum-\d{4}-w\d{2}", g._iso_week_slug())


class _FakeParse:
    def __init__(self, parsed):
        self.parsed_output = parsed
        self.stop_reason = "end_turn"


class _FakeMessages:
    def __init__(self, parsed):
        self._parsed = parsed

    def parse(self, **_kw):
        return _FakeParse(self._parsed)


class _FakeClient:
    def __init__(self, parsed):
        self.messages = _FakeMessages(parsed)


def test_research_cards_merges_real_metadata_not_model_ids(monkeypatch):
    """The published paper.arxiv_id must be the FETCHED id, not anything the model emits."""
    paper = {"title": "Native GHZ preparation in ion traps", "abstract": "We prepare GHZ states.",
             "authors": ["A. Real", "B. Author"], "year": 2026, "arxiv_id": "2604.02301",
             "doi": None, "url": "https://arxiv.org/abs/2604.02301"}
    monkeypatch.setattr(g, "fetch_recent_papers", lambda: [paper])
    monkeypatch.setattr(g, "growth_get", lambda path: {
        "research_cards": {"today": 0, "daily_cap": 3, "known_arxiv_ids": []}})

    posts = []

    def fake_post(path, body, timeout=90):
        posts.append((path, body))
        if path.endswith("/corpus/refresh"):
            return 200, {"complete": True, "by_provider": {}}
        return 200, {"created": 1, "items": [{"status": "created", "slug": "ghz-4-x",
                                              "card_url": "https://provenova.net/cards/ghz-4-x"}]}
    monkeypatch.setattr(g, "growth_post", fake_post)

    # Model returns a GHZ-4 decision for paper_index 0.
    decision = g.CardDecision(
        paper_index=0, primitive="GHZ-4",
        title="GHZ-4 state preparation — referencing arXiv:2604.02301",
        n_qubits=4,
        gates=[g.QGate(name="h", qubits=[0], params=[]),
               g.QGate(name="cx", qubits=[0, 1], params=[]),
               g.QGate(name="cx", qubits=[1, 2], params=[]),
               g.QGate(name="cx", qubits=[2, 3], params=[])],
        commentary_md="x" * 150)
    monkeypatch.setattr(g, "_anthropic", lambda: _FakeClient(g.TriageResult(decisions=[decision])))

    assert g._run_research_cards() == 0
    card_post = [b for p, b in posts if p.endswith("/research-cards")]
    assert card_post, "should have posted a research card"
    item = card_post[0]["items"][0]
    assert item["paper"]["arxiv_id"] == "2604.02301"           # from the fetched paper
    assert item["paper"]["url"] == "https://arxiv.org/abs/2604.02301"
    assert item["circuit"]["schema"] == "qlir/1.0"
    assert item["circuit"]["n_qubits"] == 4


def test_research_cards_skips_invalid_circuit(monkeypatch):
    paper = {"title": "T", "abstract": "a", "authors": ["X"], "year": 2026,
             "arxiv_id": "2601.00001", "doi": None, "url": "https://arxiv.org/abs/2601.00001"}
    monkeypatch.setattr(g, "fetch_recent_papers", lambda: [paper])
    monkeypatch.setattr(g, "growth_get", lambda path: {
        "research_cards": {"today": 0, "daily_cap": 3, "known_arxiv_ids": []}})
    posts = []
    monkeypatch.setattr(g, "growth_post", lambda p, b, timeout=90: (
        posts.append((p, b)) or (200, {"complete": True, "by_provider": {}})))
    bad = g.CardDecision(paper_index=0, primitive="bad", title="bad card title here",
                         n_qubits=1, gates=[g.QGate(name="cx", qubits=[0], params=[])],
                         commentary_md="x" * 150)  # cx with 1 qubit -> invalid
    monkeypatch.setattr(g, "_anthropic", lambda: _FakeClient(g.TriageResult(decisions=[bad])))
    assert g._run_research_cards() == 0
    assert not [p for p, _ in posts if p.endswith("/research-cards")]  # nothing published


def test_fetch_openalex_survives_null_author(monkeypatch):
    """A record with author:null must not crash the resilience fallback."""
    payload = {"results": [
        {"title": "Anon work", "abstract_inverted_index": {"x": [0]},
         "authorships": [{"author": None}, {"author": {"display_name": "Real Name"}}],
         "publication_year": 2026, "doi": "https://doi.org/10.1/abc",
         "locations": [{"landing_page_url": "https://doi.org/10.1/abc"}]},
    ]}

    class _Resp:
        def json(self):
            return payload

    monkeypatch.setattr(g.httpx, "get", lambda *a, **k: _Resp())
    out = g.fetch_openalex()
    assert len(out) == 1
    assert out[0]["authors"] == ["Real Name"]        # null author dropped, no crash
    assert out[0]["doi"] == "10.1/abc"


def test_research_cards_dedups_paper_index(monkeypatch):
    paper = {"title": "T", "abstract": "a", "authors": ["X"], "year": 2026,
             "arxiv_id": "2601.00002", "doi": None, "url": "https://arxiv.org/abs/2601.00002"}
    monkeypatch.setattr(g, "fetch_recent_papers", lambda: [paper])
    monkeypatch.setattr(g, "growth_get", lambda path: {
        "research_cards": {"today": 0, "daily_cap": 3, "known_arxiv_ids": []}})
    posts = []

    def fake_post(path, body, timeout=90):
        posts.append((path, body))
        return 200, {"complete": True, "by_provider": {}, "created": 1, "items": []}
    monkeypatch.setattr(g, "growth_post", fake_post)

    def dec(prim, extra_gate):
        gates = [g.QGate(name="h", qubits=[0], params=[])]
        if extra_gate:
            gates.append(g.QGate(name="cx", qubits=[0, 1], params=[]))
        return g.CardDecision(paper_index=0, primitive=prim, title=f"{prim} card title here",
                              n_qubits=2, gates=gates, commentary_md="x" * 150)
    # two DISTINCT circuits for the SAME paper — server idempotency wouldn't dedup these
    two = g.TriageResult(decisions=[dec("A", False), dec("B", True)])
    monkeypatch.setattr(g, "_anthropic", lambda: _FakeClient(two))

    assert g._run_research_cards() == 0
    card_posts = [b for p, b in posts if p.endswith("/research-cards")]
    assert len(card_posts) == 1
    assert len(card_posts[0]["items"]) == 1          # only one card per paper


def test_research_cards_respects_daily_cap(monkeypatch):
    monkeypatch.setattr(g, "growth_get", lambda path: {
        "research_cards": {"today": 3, "daily_cap": 3, "known_arxiv_ids": []}})
    called = []
    monkeypatch.setattr(g, "growth_post", lambda *a, **k: called.append(a) or (200, {}))
    assert g._run_research_cards() == 0
    assert not called  # cap reached -> no refresh, no publish
