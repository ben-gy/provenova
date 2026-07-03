# provenova-crawler

Public-QPU calibration crawler for Provenova.

Collects vendor-native calibration data (IBM `BackendProperties`, IonQ
characterization, Amazon Braket device properties), normalizes it into the open
`qlprov/calibration/1.0` schema, applies each vendor's Terms-of-Service
redistribution policy, and ingests content-hash-deduplicated snapshots into the
longitudinal corpus that powers the cross-fleet leaderboard and the public
"State of Quantum Hardware" analytics.

The default `FixtureSource` runs entirely offline from the repo `fixtures/`
tree — zero credentials, zero network. `LiveSource` is a per-provider skeleton
that would call the real vendor APIs when explicitly enabled via config.
