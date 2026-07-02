"""A single source of truth for domain-term definitions.

Registered as a Jinja global (``glossary``) so the ``term()`` macro can render a
consistent hover tooltip anywhere in the UI. Define a term once here; every page
that references it stays in sync. Keep definitions to one plain sentence.
"""

from __future__ import annotations

GLOSSARY: dict[str, str] = {
    # -- provenance & integrity --
    "run": "A single execution of a circuit on a backend, bound immutably to the "
           "circuit, backend and calibration snapshot that produced its result.",
    "run_hash": "A portable Merkle content identity for a run — recomputable offline "
                "and stable across the local and hosted stores; it is the ingest "
                "idempotency key.",
    "chain_hash": "A per-workspace hash that links each run to the previous one, "
                  "forming a tamper-evident ledger.",
    "content-addressing": "Storing an artifact under the hash of its own bytes, so "
                          "identical circuits or calibrations are stored once and "
                          "referenced many times.",
    "calibration snapshot": "A time-stamped capture of a device's error rates, "
                            "coherence times and gate fidelities — the hardware state "
                            "a run actually ran against.",
    "Merkle root": "A single hash summarizing a tree of hashed leaves; changing any "
                   "leaf changes the root, which is what makes tampering detectable.",
    "immutability": "Sealed records cannot be updated or deleted — database triggers "
                    "reject any change, so the ledger is append-only.",
    "qlprov": "QuantumLedger's open, versioned provenance schemas (e.g. "
              "qlprov/run/1.0) — portable JSON that verifies its own hash with no server.",

    # -- reproduce & drift --
    "reproduce": "Re-running a stored circuit against a (optionally drifted) device "
                 "state and scoring how closely the new result matches the original.",
    "drift profile": "A deterministic model of how a device degrades over time — "
                     "'typical' (gradual), 'bad_day' (aggressive) or 'recalibrated' "
                     "(improved).",
    "Hellinger fidelity": "The primary reproducibility score (0–1): how similar two "
                          "measurement distributions are; 1.0 means identical.",
    "TVD": "Total Variation Distance — the largest probability gap between two "
           "distributions; lower is more similar.",
    "verdict": "The reproducibility outcome: reproducible (≈identical), drifted "
               "(close), divergent (noticeably different) or irreproducible (far apart).",
    "transpilation delta": "The change in compiled-circuit metrics (depth, size, CX "
                           "count) between the original run and its reproduction.",

    # -- trust artifacts --
    "result card": "A public, citable page for a run — with a DOI/PID, summary, "
                   "provenance and embeddable badges.",
    "badge ladder": "The maturity rungs a result can earn: Recorded → Reproduced → "
                    "Benchmarked → Compliant → Audit-ready.",
    "DOI": "A Digital Object Identifier — a globally unique, persistent identifier "
           "that lets a result be cited unambiguously.",

    # -- compliance --
    "framework": "A standard expressed as data (e.g. FAIR, IEEE P7131) — a set of "
                 "controls whose evidence is auto-collected from your runs.",
    "control": "One requirement within a framework, satisfied (or not) by evidence "
               "rules evaluated against the record.",
    "evidence rule": "A machine-checkable predicate that decides whether a control "
                     "has the evidence it needs, drawn from runs already in the ledger.",
    "gap": "A control that is currently failing — one or more of its evidence rules "
           "are unsatisfied. The detail page shows exactly which, and how to fix it.",
    "drift alert": "A control whose evidence exists but has aged out of its freshness "
                   "window, so it no longer counts until refreshed.",
    "evidence root": "The Merkle root over all evidence items an attestation covers; "
                     "mutate any referenced record and it stops matching.",
    "attestation": "An Ed25519-signed, publicly verifiable and revocable claim that a "
                   "framework's controls were satisfied at a point in time.",

    # -- corpus --
    "public corpus": "A cross-vendor, longitudinal collection of calibration snapshots "
                     "powering the hardware leaderboard and trend charts.",
}


def register(templates) -> None:
    """Expose the glossary to Jinja templates as the ``glossary`` global."""
    templates.env.globals["glossary"] = GLOSSARY
