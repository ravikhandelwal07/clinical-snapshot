"""Observation -> vitals and results.

The single most dangerous item in the sample bundle lives here:
``observation-004`` is a creatinine of **14.7 mg/dL** -- a value that, if real,
means dialysis today -- and its status is ``entered-in-error``. The source system
has voided it. Rendering it next to the real results, even greyed out, risks a
clinician acting on a number that was never true.

So it is withheld from the results list and moved to the withheld panel, flagged
``noteworthy`` because "this record contains a voided critical result" is itself
something a reader should know.

Two further decisions worth stating:

* **No units are converted and no reference ranges are applied.** Values are
  shown in the unit the source sent. Deciding that 138/88 is "high" is clinical
  decision support: it needs validated thresholds, patient context and its own
  review. Guessing at it in a summary view would be out of scope and unsafe.
* **Old results are shown, dated.** An HbA1c from 2020 is real data and worth
  seeing; it is labelled with its age so it cannot be read as current.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.fhir.primitives import format_quantity
from app.fhir.resources import Observation, ObservationComponent
from app.models.issues import IssueCategory
from app.models.summary import (
    ObservationSection,
    ObservationSummary,
    ObservationValue,
)
from app.normalize import rules
from app.normalize.common import (
    build_concept,
    build_provenance,
    check_reference,
    describe_age,
    note_imprecise_date,
    squash,
)
from app.normalize.context import HISTORICAL_AFTER_DAYS, NormalizationContext
from app.normalize.terminology import (
    LabelSource,
    is_vital_sign,
    pick_coding,
    resolve_label,
    system_name,
)

SECTION = "Observations"

SYSTOLIC = "8480-6"
DIASTOLIC = "8462-4"


def build_observations(ctx: NormalizationContext) -> ObservationSection:
    vitals: list[ObservationSummary] = []
    labs: list[ObservationSummary] = []
    other: list[ObservationSummary] = []

    for observation in ctx.bundle.observations:
        subject_kind, subject_note = ctx.subject_status(observation.subject)
        concept = build_concept(
            observation.code,
            log=ctx.log,
            resource_key=observation.key,
            field="code",
            what="observation",
        )
        status = rules.observation_badge(observation.status)
        value_text, components = _render_value(ctx, observation)

        if rules.is_erroneous(status.code):
            ctx.suppress(
                observation,
                section=SECTION,
                label=(
                    f"{concept.text}"
                    + (f" — recorded value {value_text}" if value_text else "")
                ),
                reason=(
                    "Observation status is “entered in error”. The source system has "
                    "voided this result, so it is not a measurement of this patient."
                ),
                status=status.label,
                recorded=observation.effective,
                severity="critical",
                noteworthy=True,
            )
            continue

        if subject_kind == "foreign":
            ctx.suppress(
                observation,
                section=SECTION,
                label=f"{concept.text}" + (f" — {value_text}" if value_text else ""),
                reason=subject_note or "Subject could not be linked to this patient.",
                status=status.label,
                recorded=observation.effective,
                category=IssueCategory.IDENTITY,
                severity="critical",
            )
            continue

        effective = observation.effective
        notes: list[str] = []
        if subject_note:
            notes.append(subject_note)
        if status.code in rules.OBSERVATION_PROVISIONAL:
            notes.append(
                f"Result is {status.label.lower()} — not a finalised value."
            )
        elif not status.is_current:
            notes.append(
                f"Status “{status.label}” is not a recognised final state; the value "
                "may not be reliable."
            )
        if precision_note := note_imprecise_date(
            effective,
            log=ctx.log,
            resource_key=observation.key,
            field="effectiveDateTime",
            what="effective date",
        ):
            notes.append(f"Timing: {precision_note}")
        if effective is None:
            notes.append("No date recorded — this result cannot be placed in time.")
            ctx.log.warning(
                IssueCategory.MISSING_DATA,
                f"{observation.key} has no effective date or issued time.",
                resource=observation.key,
                field="effective[x]",
                action="Displayed without a date and sorted last.",
            )
        for reference, field_name, what in (
            (observation.encounter, "encounter", "encounter"),
            *[
                (performer, f"performer[{index}]", "performer")
                for index, performer in enumerate(observation.performer)
            ],
        ):
            if dangling := check_reference(
                reference,
                bundle=ctx.bundle,
                log=ctx.log,
                resource_key=observation.key,
                field=field_name,
                what=what,
            ):
                notes.append(dangling)
        if observation.data_absent_reason is not None:
            reason = build_concept(
                observation.data_absent_reason,
                log=ctx.log,
                resource_key=observation.key,
                field="dataAbsentReason",
                what="data absent reason",
            )
            notes.append(f"No value recorded. Reason given: {reason.text}.")
        elif value_text is None and not components:
            notes.append("No value recorded and no reason given for its absence.")
            ctx.log.warning(
                IssueCategory.MISSING_DATA,
                f"{observation.key} carries no value and no dataAbsentReason.",
                resource=observation.key,
                field="value[x]",
                action="Shown as 'no value recorded'.",
            )

        age_text = describe_age(effective, ctx.as_of)
        is_historical = bool(
            effective is not None
            and (ctx.as_of - effective.sort_key).days > HISTORICAL_AFTER_DAYS
        )
        if is_historical:
            notes.append(
                f"Historical result ({age_text}) — not a current measurement."
            )
            ctx.log.info(
                IssueCategory.STALE_DATA,
                f"{observation.key} ({concept.text}) is dated "
                f"{effective.display if effective else 'unknown'}, {age_text}.",
                resource=observation.key,
                action="Shown in the results list with its age stated.",
            )

        coding = pick_coding(observation.code)
        summary = ObservationSummary(
            concept=concept,
            value=value_text,
            components=components,
            unit=(
                observation.value_quantity.unit
                if observation.value_quantity is not None
                else None
            ),
            effective=effective,
            status=status,
            is_vital_sign=is_vital_sign(observation.category, coding),
            is_historical=is_historical,
            age_text=age_text,
            provenance=build_provenance(
                observation,
                subject=observation.subject,
                via_linked_identity=subject_kind == "linked",
            ),
            notes=notes,
        )

        if summary.is_vital_sign:
            vitals.append(summary)
        elif observation.value_quantity is not None or observation.component:
            labs.append(summary)
        else:
            other.append(summary)

    for bucket in (vitals, labs, other):
        bucket.sort(key=_recency, reverse=True)
    return ObservationSection(vitals=vitals, labs=labs, other=other)


def _render_value(
    ctx: NormalizationContext, observation: Observation
) -> tuple[Optional[str], list[ObservationValue]]:
    """Format ``value[x]`` and any components, without converting anything."""
    if observation.value_quantity is not None:
        return format_quantity(observation.value_quantity), []
    if observation.value_string:
        return squash(observation.value_string), []
    if observation.value_boolean is not None:
        return "Yes" if observation.value_boolean else "No", []
    if observation.value_codeable_concept is not None:
        concept = build_concept(
            observation.value_codeable_concept,
            log=ctx.log,
            resource_key=observation.key,
            field="valueCodeableConcept",
            what="observation value",
        )
        return concept.text, []

    if observation.component:
        components = [
            _component_value(ctx, observation, component, index)
            for index, component in enumerate(observation.component)
        ]
        return _combine_blood_pressure(observation) or None, components

    return None, []


def _component_value(
    ctx: NormalizationContext,
    observation: Observation,
    component: ObservationComponent,
    index: int,
) -> ObservationValue:
    coding = pick_coding(component.code)
    label, source = resolve_label(coding)
    if label is None and coding is not None and coding.code:
        label = f"{system_name(coding.system) or 'Code'} {coding.code}"
        source = LabelSource.CODE_ONLY
    text = format_quantity(component.value_quantity)
    if text is None and component.value_string:
        text = squash(component.value_string)
    if text is None and component.value_codeable_concept is not None:
        text = build_concept(
            component.value_codeable_concept,
            log=ctx.log,
            resource_key=observation.key,
            field=f"component[{index}].valueCodeableConcept",
            what="component value",
        ).text
    if text is None:
        text = "No value recorded"
    return ObservationValue(
        label=label,
        text=text,
        code=coding.code if coding else None,
        label_source=source,
    )


def _combine_blood_pressure(observation: Observation) -> Optional[str]:
    """``138/88 mmHg`` -- the form a clinician actually reads.

    Only applied when both components are present with the same unit. A single
    limb of a BP pair is never rendered as a fraction.
    """
    values: dict[str, tuple[float, Optional[str]]] = {}
    for component in observation.component:
        coding = pick_coding(component.code)
        if coding is None or coding.code not in (SYSTOLIC, DIASTOLIC):
            continue
        quantity = component.value_quantity
        if quantity is None or quantity.value is None:
            continue
        values[coding.code] = (quantity.value, quantity.unit or quantity.code)

    if SYSTOLIC not in values or DIASTOLIC not in values:
        return None
    systolic, systolic_unit = values[SYSTOLIC]
    diastolic, diastolic_unit = values[DIASTOLIC]
    if systolic_unit != diastolic_unit:
        return None

    def fmt(number: float) -> str:
        return f"{int(number)}" if float(number).is_integer() else f"{number:g}"

    return f"{fmt(systolic)}/{fmt(diastolic)} {systolic_unit or ''}".strip()


def _recency(summary: ObservationSummary):
    if summary.effective is not None:
        return summary.effective.sort_key
    return datetime(1, 1, 1, tzinfo=timezone.utc)
