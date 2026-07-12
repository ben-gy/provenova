"""Pydantic models for the open ``qlprov/*`` provenance documents.

These validate captured/exported documents and are the portable, offline-
verifiable unit shared, cited and attested.
"""

from __future__ import annotations

import datetime as _dt

from pydantic import BaseModel, Field

CALIBRATION_SCHEMA = "qlprov/calibration/1.0"
RUN_SCHEMA = "qlprov/run/1.0"


class QubitCalibration(BaseModel):
    index: int
    T1_us: float | None = None
    T2_us: float | None = None
    frequency_ghz: float | None = None
    anharmonicity_ghz: float | None = None
    readout_error: float | None = None
    prob_meas0_prep1: float | None = None
    prob_meas1_prep0: float | None = None
    readout_length_ns: float | None = None


class GateCalibration(BaseModel):
    gate: str
    qubits: list[int]
    error: float | None = None
    length_ns: float | None = None


class GapFlag(BaseModel):
    field: str
    reason: str  # vendor_not_exposed | auth_missing | sdk_version
    detail: str | None = None


class CalibrationDoc(BaseModel):
    schema_id: str = Field(default=CALIBRATION_SCHEMA, alias="schema")
    backend: dict
    captured_at: _dt.datetime
    qubits: list[QubitCalibration] = []
    gates: list[GateCalibration] = []
    general: dict = {}
    fleet_metrics: dict = {}
    provenance: dict = {}
    gaps: list[GapFlag] = []

    model_config = {"populate_by_name": True}


class RunProvenanceDoc(BaseModel):
    schema_id: str = Field(default=RUN_SCHEMA, alias="schema")
    run_id: str
    run_hash: str
    prev_run_hash: str | None = None
    workspace: dict
    created_by: str | None = None
    created_at: _dt.datetime
    circuit: dict
    compilation: dict | None = None
    backend: dict
    calibration: dict
    execution: dict
    results: list[dict]
    merkle: dict

    model_config = {"populate_by_name": True}
