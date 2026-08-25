"""FastAPI application.

Endpoints
---------
``GET /api/patient-summary``  the whole snapshot; this is what the UI consumes.
``GET /api/data-quality``     just the issue ledger, for triage.
``GET /api/withheld``         just the records kept out of the clinical view.
``GET /health``               liveness plus whether the bundle parsed.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import get_settings
from app.models.issues import DataIssue, IssueSeverity
from app.models.summary import ClinicalSnapshot, SuppressedItem
from app.services import BundleLoadError, get_snapshot

logger = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    description=(
        "Normalizes a messy FHIR R4 bundle into a clinical snapshot. Every value "
        "returned carries provenance and, where relevant, an explicit statement of "
        "what could not be resolved."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


class HealthResponse(BaseModel):
    status: str
    bundle_path: str
    bundle_loaded: bool
    detail: str | None = None


class DataQualityResponse(BaseModel):
    total: int
    by_severity: dict[str, int]
    issues: list[DataIssue]


class WithheldResponse(BaseModel):
    total: int
    items: list[SuppressedItem]


def _snapshot(refresh: bool) -> ClinicalSnapshot:
    try:
        return get_snapshot(refresh=refresh)
    except BundleLoadError as exc:
        logger.exception("Bundle could not be loaded")
        # 503, not 500: the service is fine, its input is not.
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/health", response_model=HealthResponse, tags=["ops"])
def health() -> HealthResponse:
    try:
        snapshot = get_snapshot()
    except BundleLoadError as exc:
        return HealthResponse(
            status="degraded",
            bundle_path=str(settings.bundle_path),
            bundle_loaded=False,
            detail=str(exc),
        )
    critical = sum(
        1 for issue in snapshot.data_quality if issue.severity is IssueSeverity.CRITICAL
    )
    return HealthResponse(
        status="ok",
        bundle_path=str(settings.bundle_path),
        bundle_loaded=True,
        detail=f"{len(snapshot.data_quality)} data-quality findings, {critical} critical.",
    )


@app.get("/api/patient-summary", response_model=ClinicalSnapshot, tags=["snapshot"])
def patient_summary(
    refresh: bool = Query(False, description="Re-read the bundle from disk."),
) -> ClinicalSnapshot:
    return _snapshot(refresh)


@app.get("/api/data-quality", response_model=DataQualityResponse, tags=["snapshot"])
def data_quality(
    severity: IssueSeverity | None = Query(None, description="Filter by severity."),
    refresh: bool = Query(False),
) -> DataQualityResponse:
    snapshot = _snapshot(refresh)
    issues = snapshot.data_quality
    counts = {level.value: 0 for level in IssueSeverity}
    for issue in issues:
        counts[issue.severity.value] += 1
    if severity is not None:
        issues = [issue for issue in issues if issue.severity is severity]
    return DataQualityResponse(total=len(issues), by_severity=counts, issues=issues)


@app.get("/api/withheld", response_model=WithheldResponse, tags=["snapshot"])
def withheld(refresh: bool = Query(False)) -> WithheldResponse:
    snapshot = _snapshot(refresh)
    return WithheldResponse(total=len(snapshot.suppressed), items=snapshot.suppressed)
