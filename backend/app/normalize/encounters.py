"""Encounter -> recent visit list.

``encounter-002`` is ``entered-in-error``. It is withheld rather than shown as a
September visit, because "the patient was seen on 17 Sep 2025" is a factual
claim about the care record, and the source has retracted it. It also happens to
be the encounter that *other* resources would have hung off, which is why the
withheld panel names it explicitly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.fhir.resources import Encounter
from app.models.issues import IssueCategory
from app.models.summary import CodedConcept, EncounterSummary
from app.normalize import rules
from app.normalize.common import (
    build_concept,
    build_provenance,
    describe_age,
    note_imprecise_date,
    squash,
)
from app.normalize.context import NormalizationContext

SECTION = "Encounters"


def build_encounters(
    ctx: NormalizationContext, *, limit: int = 10
) -> list[EncounterSummary]:
    encounters: list[EncounterSummary] = []

    for encounter in ctx.bundle.encounters:
        subject_kind, subject_note = ctx.subject_status(encounter.subject)
        type_concept = _encounter_type(ctx, encounter)
        status = rules.encounter_badge(encounter.status)
        label = type_concept.text if type_concept else "Encounter"
        start = encounter.period.start if encounter.period else None
        end = encounter.period.end if encounter.period else None

        if rules.is_erroneous(status.code):
            ctx.suppress(
                encounter,
                section=SECTION,
                label=label + (f" on {start.display}" if start else ""),
                reason=(
                    "Encounter status is “entered in error”, so this visit did not "
                    "happen as recorded."
                ),
                status=status.label,
                recorded=start,
                severity="critical",
            )
            continue

        if subject_kind == "foreign":
            ctx.suppress(
                encounter,
                section=SECTION,
                label=label,
                reason=subject_note or "Subject could not be linked to this patient.",
                status=status.label,
                recorded=start,
                category=IssueCategory.IDENTITY,
                severity="critical",
            )
            continue

        notes: list[str] = []
        if subject_note:
            notes.append(subject_note)
        if type_concept is None:
            notes.append("Visit type not recorded.")
            ctx.log.info(
                IssueCategory.MISSING_DATA,
                f"{encounter.key} has no type coding.",
                resource=encounter.key,
                field="type",
                action="Shown with the encounter class only.",
            )
        if start is None:
            notes.append("No start date recorded.")
            ctx.log.warning(
                IssueCategory.MISSING_DATA,
                f"{encounter.key} has no period.start.",
                resource=encounter.key,
                field="period.start",
                action="Displayed without a date and sorted last.",
            )
        elif end is None and status.code == "finished":
            notes.append("Marked finished but no end time was recorded.")
        for value, field_name, what in (
            (start, "period.start", "start date"),
            (end, "period.end", "end date"),
        ):
            if precision_note := note_imprecise_date(
                value,
                log=ctx.log,
                resource_key=encounter.key,
                field=field_name,
                what=what,
            ):
                notes.append(f"{what.capitalize()}: {precision_note}")

        if age_text := describe_age(start, ctx.as_of):
            notes.append(f"Visit was {age_text}.")

        encounters.append(
            EncounterSummary(
                type=type_concept,
                encounter_class=_class_label(encounter),
                status=status,
                start=start,
                end=end,
                duration_minutes=_duration_minutes(start, end),
                provenance=build_provenance(
                    encounter,
                    subject=encounter.subject,
                    via_linked_identity=subject_kind == "linked",
                ),
                notes=notes,
            )
        )

    encounters.sort(key=_recency, reverse=True)
    if len(encounters) > limit:
        ctx.log.info(
            IssueCategory.BUNDLE_INTEGRITY,
            f"{len(encounters)} encounters were available; the {limit} most recent "
            "are shown.",
            action="Older visits are omitted from the snapshot view.",
        )
    return encounters[:limit]


def _encounter_type(
    ctx: NormalizationContext, encounter: Encounter
) -> Optional[CodedConcept]:
    if not encounter.type:
        return None
    return build_concept(
        encounter.type[0],
        log=ctx.log,
        resource_key=encounter.key,
        field="type[0]",
        what="encounter type",
    )


def _class_label(encounter: Encounter) -> Optional[str]:
    concept = encounter.class_
    if concept is None:
        return None
    if concept.coding and concept.coding[0].display:
        return squash(concept.coding[0].display).capitalize()
    if concept.text:
        return squash(concept.text)
    code = concept.code_value()
    return code.upper() if code else None


def _duration_minutes(start, end) -> Optional[int]:
    """Only computed when both ends are true instants; never from a padded date."""
    if start is None or end is None:
        return None
    from app.fhir.primitives import DatePrecision

    if DatePrecision.INSTANT not in (start.precision, end.precision):
        return None
    if start.midnight_utc_padded or end.midnight_utc_padded:
        return None
    delta = end.sort_key - start.sort_key
    minutes = int(delta.total_seconds() // 60)
    return minutes if minutes > 0 else None


def _recency(summary: EncounterSummary):
    if summary.start is not None:
        return summary.start.sort_key
    return datetime(1, 1, 1, tzinfo=timezone.utc)
