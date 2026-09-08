"""Pydantic models. These are the OpenAPI source of truth — the
TypeScript types in the web app are generated from them, never
hand-written twice.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Severity = Literal["critical", "high", "medium", "low", "info"]
# SIH26127 §1 defines the full incident lifecycle. `new` is the spec's
# DETECTED and `acknowledged` its UNDER REVIEW; the response states are
# added so a confirmed incident can be driven through dispatch.
AlertStatus = Literal[
    "new", "acknowledged", "investigating",
    "confirmed", "dispatched", "responding",
    "resolved", "false_positive",
]


class PlateReadIn(BaseModel):
    """One raw per-frame OCR result. Kept as the audit trail behind a vote."""
    raw_text: str
    confidence: float = Field(ge=0.0, le=1.0)
    quality: float = Field(default=1.0, ge=0.0, le=1.0)
    frame_ts: str


class ObservationMeta(BaseModel):
    """Provenance every AI observation must carry (SIH26127 §8).

    The spec is explicit that a result without a model name and version is
    not auditable: when accuracy changes you must be able to say which
    model produced which record. `event_id` makes retries idempotent at
    the event level, above the row-level dedupe_key.
    """
    event_id: str | None = None
    model_name: str = "netra-anpr"
    model_version: str = "1.0.0"
    # When the SOURCE frame was captured, which is not when we processed
    # it. Confusing the two silently corrupts every speed calculation.
    source_ts: str | None = None


class SightingIn(BaseModel):
    """One consolidated vehicle pass, published by the edge worker after
    temporal voting has settled on a plate."""
    plate: str
    camera_id: str
    seen_at: str
    confidence: float = Field(ge=0.0, le=1.0)
    track_id: str | None = None
    frame_count: int = 1
    color: str | None = None
    vehicle_type: str | None = None
    crop_path: str | None = None
    source: Literal["edge", "sim", "manual"] = "edge"
    reads: list[PlateReadIn] = []
    # Idempotency: the worker generates this, so a retry after a network
    # blip cannot insert the same pass twice.
    dedupe_key: str | None = None
    meta: ObservationMeta = ObservationMeta()

    @field_validator("plate")
    @classmethod
    def _normalise(cls, v: str) -> str:
        return v.upper().replace(" ", "").replace("-", "")


class IngestBatch(BaseModel):
    sightings: list[SightingIn] = []


class IngestResult(BaseModel):
    accepted: int
    duplicates: int
    queued_for_review: int
    discarded: int
    alerts: list[dict[str, Any]] = []


class AnomalyIn(BaseModel):
    meta: ObservationMeta = ObservationMeta(model_name="netra-vad")
    camera_id: str
    occurred_at: str
    kind: str
    score: float = Field(ge=0.0, le=1.0)
    clip_path: str | None = None
    detail: str = ""
    source: Literal["heuristic", "model", "vlm"] = "heuristic"


class ReviewDecision(BaseModel):
    """An operator's verdict on a low-confidence read. `corrected_plate`
    is required when correcting, and the correction is what feeds the
    accuracy metric and future training data."""
    decision: Literal["confirmed", "corrected", "rejected"]
    corrected_plate: str | None = None
    reviewed_by: str = "operator"


class WatchlistIn(BaseModel):
    plate: str
    reason: str
    severity: Literal["critical", "high", "medium", "low"] = "high"
    case_ref: str | None = None
    added_by: str = "operator"
    expires_at: str | None = None


class AlertUpdate(BaseModel):
    status: AlertStatus
    actor: str = "operator"
    note: str | None = None


class NLQueryIn(BaseModel):
    question: str
    actor: str = "operator"


class PersonIn(BaseModel):
    """Enrolment. `image` is a data URL or base64 JPEG/PNG."""
    name: str
    image: str
    category: Literal["wanted", "missing", "person_of_interest"] = "wanted"
    case_ref: str | None = None
    added_by: str = "operator"
    # Enrolment is time-bounded by design; see the persons table comment.
    expires_at: str | None = None
    source: str = "manual_enrolment"
    # Supply to add another template to an existing person.
    person_id: str | None = None


class FaceScanIn(BaseModel):
    image: str
    camera_id: str | None = None
    track_id: str | None = None
    frame_count: int = 1
    actor: str = "operator"


class PersonMatchDecision(BaseModel):
    decision: Literal["confirmed", "rejected"]
    reviewed_by: str = "operator"


class DeviceFrameIn(BaseModel):
    """One frame from a phone acting as a camera."""
    camera_id: str
    image: str
    name: str | None = None
    lat: float | None = None
    lon: float | None = None
    # Analysis is opt-in per frame: the phone knows its own battery and
    # network budget, so it decides how often to ask for it.
    # A comma list drawn from: face, plate, threat. "none" disables it.
    analyse: str = "none"
    actor: str = "operator"

    @field_validator("analyse")
    @classmethod
    def _known_analyses(cls, v: str) -> str:
        allowed = {"none", "face", "plate", "threat", "objects"}
        bad = {w.strip() for w in v.split(",") if w.strip()} - allowed
        if bad:
            raise ValueError(f"unknown analysis {sorted(bad)}; "
                             f"choose from {sorted(allowed - {'none'})}")
        return v
