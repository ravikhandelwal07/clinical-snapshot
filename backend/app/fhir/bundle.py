"""Tolerant bundle loader.

The loader's contract: **one bad entry must not cost us the other sixteen.**
Each entry is validated on its own; failures become data-quality issues rather
than exceptions. The result is a typed, per-resource-type view of the bundle
plus the set of reference keys that actually exist in it, which is what lets the
normalizer detect dangling references instead of rendering a broken link.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from app.fhir.primitives import PartialDateTime
from app.fhir.resources import (
    SUPPORTED_RESOURCES,
    AllergyIntolerance,
    Condition,
    DomainResource,
    Encounter,
    MedicationRequest,
    Observation,
    Patient,
)
from app.models.issues import DataIssue, IssueCategory, IssueLog


@dataclass
class LoadedBundle:
    """Typed contents of one FHIR bundle, plus what went wrong reading it."""

    bundle_id: Optional[str]
    bundle_type: Optional[str]
    timestamp: Optional[PartialDateTime]
    declared_total: Optional[int]
    entry_count: int

    patients: list[Patient] = field(default_factory=list)
    encounters: list[Encounter] = field(default_factory=list)
    conditions: list[Condition] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
    medication_requests: list[MedicationRequest] = field(default_factory=list)
    allergies: list[AllergyIntolerance] = field(default_factory=list)

    #: Every ``Type/id`` present in the bundle, for reference resolution.
    present_keys: set[str] = field(default_factory=set)
    #: ``fullUrl`` values, since senders often reference by urn:uuid.
    full_urls: set[str] = field(default_factory=set)
    issues: list[DataIssue] = field(default_factory=list)

    def clinical_resources(self) -> list[DomainResource]:
        return [
            *self.encounters,
            *self.conditions,
            *self.observations,
            *self.medication_requests,
            *self.allergies,
        ]

    def has_reference(self, key: Optional[str]) -> bool:
        if not key:
            return False
        return key in self.present_keys or key in self.full_urls


class BundleLoadError(RuntimeError):
    """Raised only when the file itself is unusable (missing / not a Bundle)."""


def load_bundle(path: Path) -> LoadedBundle:
    """Read and validate a bundle from disk."""
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BundleLoadError(f"Could not read bundle at {path}: {exc}") from exc

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise BundleLoadError(f"Bundle at {path} is not valid JSON: {exc}") from exc

    return parse_bundle(payload)


def parse_bundle(payload: Any) -> LoadedBundle:
    """Validate an already-deserialized bundle document."""
    if not isinstance(payload, dict):
        raise BundleLoadError("Bundle document must be a JSON object.")
    if payload.get("resourceType") != "Bundle":
        raise BundleLoadError(
            f"Expected resourceType 'Bundle', got {payload.get('resourceType')!r}."
        )

    log = IssueLog()
    entries = payload.get("entry") or []
    if not isinstance(entries, list):
        log.warning(
            IssueCategory.PARSE_FAILURE,
            "Bundle.entry was not a list; treated as empty.",
            field="entry",
        )
        entries = []

    bundle = LoadedBundle(
        bundle_id=payload.get("id"),
        bundle_type=payload.get("type"),
        timestamp=PartialDateTime.parse(payload.get("timestamp")),
        declared_total=_as_int(payload.get("total")),
        entry_count=len(entries),
    )

    buckets: dict[str, list] = {
        "Patient": bundle.patients,
        "Encounter": bundle.encounters,
        "Condition": bundle.conditions,
        "Observation": bundle.observations,
        "MedicationRequest": bundle.medication_requests,
        "AllergyIntolerance": bundle.allergies,
    }
    seen_keys: set[str] = set()

    for index, entry in enumerate(entries):
        location = f"entry[{index}]"
        if not isinstance(entry, dict):
            log.warning(
                IssueCategory.PARSE_FAILURE,
                "Bundle entry was not an object.",
                field=location,
                action="Entry skipped.",
            )
            continue

        full_url = entry.get("fullUrl")
        if isinstance(full_url, str):
            bundle.full_urls.add(full_url)

        resource = entry.get("resource")
        if not isinstance(resource, dict):
            log.warning(
                IssueCategory.PARSE_FAILURE,
                "Bundle entry carried no resource object.",
                field=f"{location}.resource",
                action="Entry skipped.",
            )
            continue

        resource_type = resource.get("resourceType")
        if not isinstance(resource_type, str):
            log.warning(
                IssueCategory.PARSE_FAILURE,
                "Resource had no resourceType.",
                field=f"{location}.resource.resourceType",
                action="Entry skipped.",
            )
            continue

        resource_id = resource.get("id")
        key = f"{resource_type}/{resource_id}" if resource_id else None
        if key:
            bundle.present_keys.add(key)
            if key in seen_keys:
                log.warning(
                    IssueCategory.DUPLICATE,
                    f"{key} appears more than once in the bundle.",
                    resource=key,
                    action="Both copies parsed; downstream dedup applies.",
                )
            seen_keys.add(key)
        else:
            log.warning(
                IssueCategory.PARSE_FAILURE,
                f"A {resource_type} resource has no id and cannot be referenced.",
                field=f"{location}.resource.id",
            )

        model = SUPPORTED_RESOURCES.get(resource_type)
        if model is None:
            log.info(
                IssueCategory.UNSUPPORTED_RESOURCE,
                f"{resource_type} is present in the bundle but not part of the snapshot.",
                resource=key,
                action="Retained in the source bundle only; not summarized.",
            )
            continue

        try:
            parsed = model.model_validate(resource)
        except ValidationError as exc:
            log.critical(
                IssueCategory.PARSE_FAILURE,
                f"{resource_type} failed validation and was excluded: "
                f"{_first_error(exc)}",
                resource=key,
                action="Resource excluded from the snapshot entirely.",
            )
            continue

        buckets[resource_type].append(parsed)

    _check_integrity(bundle, log)
    bundle.issues = log.issues
    return bundle


def _check_integrity(bundle: LoadedBundle, log: IssueLog) -> None:
    if bundle.declared_total is not None and bundle.declared_total != bundle.entry_count:
        log.warning(
            IssueCategory.BUNDLE_INTEGRITY,
            f"Bundle.total says {bundle.declared_total} but {bundle.entry_count} "
            "entries are present. The bundle may be truncated or paged.",
            field="total",
            action="Only the entries actually present were used.",
        )
    if not bundle.patients:
        log.critical(
            IssueCategory.MISSING_DATA,
            "Bundle contains no Patient resource.",
            action="No demographics can be shown.",
        )
    if bundle.timestamp is None:
        log.info(
            IssueCategory.MISSING_DATA,
            "Bundle has no timestamp, so the age of the extract is unknown.",
            field="timestamp",
        )


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_error(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:  # pragma: no cover - defensive
        return str(exc)
    first = errors[0]
    location = ".".join(str(p) for p in first.get("loc", ()))
    return f"{location or '<root>'}: {first.get('msg')}"
