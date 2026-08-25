"""Orchestrates the normalization pass: LoadedBundle -> ClinicalSnapshot.

Order matters. Identity is resolved first because every downstream section needs
to know which Patient resources count as "this patient" before it can decide
whether a record belongs in the snapshot at all.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.fhir.bundle import LoadedBundle
from app.fhir.primitives import AgeEstimate
from app.models.issues import IssueCategory, IssueLog
from app.models.summary import (
    ClinicalSnapshot,
    Confidence,
    Demographics,
    IdentityResolution,
    SectionCoverage,
    SourceInfo,
)
from app.normalize.allergies import build_allergies
from app.normalize.context import NormalizationContext
from app.normalize.encounters import build_encounters
from app.normalize.identity import resolve_identity
from app.normalize.medications import build_medications
from app.normalize.observations import build_observations
from app.normalize.problems import build_problems


def build_snapshot(
    bundle: LoadedBundle, *, now: datetime | None = None
) -> ClinicalSnapshot:
    as_of = now or datetime.now(timezone.utc)

    log = IssueLog()
    log.extend(bundle.issues)  # carry the loader's findings forward

    identity = resolve_identity(bundle.patients, as_of=as_of, log=log)
    ctx = NormalizationContext(bundle=bundle, identity=identity, log=log, as_of=as_of)

    problems = build_problems(ctx)
    medications = build_medications(ctx)
    allergies = build_allergies(ctx)
    encounters = build_encounters(ctx)
    observations = build_observations(ctx)

    _flag_orphans(ctx)

    demographics = (
        identity.demographics if identity is not None else _placeholder_demographics(as_of)
    )
    resolution = (
        identity.resolution
        if identity is not None
        else IdentityResolution(
            narrative="No Patient resource was present in the bundle.",
            confidence=Confidence.UNRESOLVED,
            score=0.0,
        )
    )

    if not allergies.active and not allergies.no_known_allergies_asserted:
        log.warning(
            IssueCategory.MISSING_DATA,
            "No current allergy records were found. This is not the same as a "
            "documented 'no known allergies' assertion.",
            action="The allergy panel says allergy status is unknown, not 'none'.",
        )

    return ClinicalSnapshot(
        generated_at=datetime.now(timezone.utc),
        as_of=as_of,
        source=_source_info(bundle, as_of),
        identity=resolution,
        demographics=demographics,
        allergies=allergies,
        problems=problems,
        medications=medications,
        encounters=encounters,
        observations=observations,
        suppressed=sorted(
            ctx.suppressed, key=lambda item: (not item.is_noteworthy, item.section)
        ),
        data_quality=log.issues,
        coverage=_coverage(problems, medications, allergies, encounters, observations, ctx),
    )


def _placeholder_demographics(as_of: datetime) -> Demographics:
    return Demographics(
        full_name="Patient record not available",
        age=AgeEstimate.unknown("No Patient resource in the bundle."),
    )


def _source_info(bundle: LoadedBundle, as_of: datetime) -> SourceInfo:
    currency = None
    if bundle.timestamp is not None:
        days = (as_of - bundle.timestamp.sort_key).days
        currency = (
            f"Extract assembled {bundle.timestamp.display}"
            + (f" ({days} days before this view)." if days > 0 else ".")
            + " Anything recorded after that date is not in this snapshot."
        )
    return SourceInfo(
        bundle_id=bundle.bundle_id,
        bundle_type=bundle.bundle_type,
        bundle_timestamp=bundle.timestamp,
        declared_total=bundle.declared_total,
        entry_count=bundle.entry_count,
        resource_counts={
            "Patient": len(bundle.patients),
            "Encounter": len(bundle.encounters),
            "Condition": len(bundle.conditions),
            "Observation": len(bundle.observations),
            "MedicationRequest": len(bundle.medication_requests),
            "AllergyIntolerance": len(bundle.allergies),
        },
        currency_note=currency,
    )


def _flag_orphans(ctx: NormalizationContext) -> None:
    """Report resources with no usable subject reference.

    They are still rendered (with a note) because a result that exists but is
    poorly linked is information; silently dropping it is not.
    """
    for resource in ctx.bundle.clinical_resources():
        subject = getattr(resource, "subject", None)
        if subject is None or not subject.key:
            ctx.log.warning(
                IssueCategory.ORPHANED_RESOURCE,
                f"{resource.key} has no subject reference.",
                resource=resource.key,
                field="subject",
                action=(
                    "Shown with a note that the source did not say which patient it "
                    "belongs to."
                ),
            )


def _coverage(problems, medications, allergies, encounters, observations, ctx):
    def suppressed_for(section: str) -> int:
        return sum(1 for item in ctx.suppressed if item.section == section)

    def qualified(items) -> int:
        return sum(1 for item in items if item.notes)

    all_observations = [*observations.vitals, *observations.labs, *observations.other]
    return {
        "problems": SectionCoverage(
            displayed=len(problems.active) + len(problems.inactive),
            suppressed=suppressed_for("Problems"),
            qualified=qualified([*problems.active, *problems.inactive]),
        ),
        "medications": SectionCoverage(
            displayed=len(medications.current) + len(medications.past),
            suppressed=suppressed_for("Medications"),
            qualified=qualified([*medications.current, *medications.past]),
        ),
        "allergies": SectionCoverage(
            displayed=len(allergies.active) + len(allergies.inactive),
            suppressed=suppressed_for("Allergies"),
            qualified=qualified([*allergies.active, *allergies.inactive]),
        ),
        "encounters": SectionCoverage(
            displayed=len(encounters),
            suppressed=suppressed_for("Encounters"),
            qualified=qualified(encounters),
        ),
        "observations": SectionCoverage(
            displayed=len(all_observations),
            suppressed=suppressed_for("Observations"),
            qualified=qualified(all_observations),
        ),
    }
