"""Condition -> problem list.

Three-way split, because "not active" and "not real" are different claims:

* **active**    -- clinicalStatus is active/recurrence/relapse and the source has
                   not retracted the statement.
* **inactive**  -- resolved / in remission. True history; shown separately.
* **withheld**  -- verificationStatus is entered-in-error or refuted. The source
                   has retracted it, so it is not a problem at all.

``condition-002`` in the sample bundle is both ``inactive`` *and*
``entered-in-error``. Retraction wins: an erroneous record is not history, and
listing it under "resolved problems" would imply the patient once had asthma.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.fhir.resources import Condition
from app.models.issues import IssueCategory
from app.models.summary import Problem, ProblemSection
from app.normalize import rules
from app.normalize.common import (
    build_concept,
    build_provenance,
    check_reference,
    note_imprecise_date,
)
from app.normalize.context import NormalizationContext
from app.normalize.terminology import concept_key, pick_coding, resolve_label

SECTION = "Problems"


def build_problems(ctx: NormalizationContext) -> ProblemSection:
    active: list[Problem] = []
    inactive: list[Problem] = []
    seen: dict[str, Problem] = {}

    for condition in ctx.bundle.conditions:
        subject_kind, subject_note = ctx.subject_status(condition.subject)
        concept = build_concept(
            condition.code,
            log=ctx.log,
            resource_key=condition.key,
            field="code",
            what="problem",
        )

        clinical = rules.condition_clinical_badge(condition.clinical_status)
        verification = rules.verification_badge(condition.verification_status)

        # 1. Retracted by the source system -> never a clinical fact.
        if rules.is_erroneous(None, verification.code):
            ctx.suppress(
                condition,
                section=SECTION,
                label=concept.text,
                reason=(
                    f"Verification status is “{verification.label}”, so the source "
                    "system has retracted this diagnosis."
                ),
                status=f"{clinical.label} / {verification.label}",
                recorded=condition.onset_date_time,
                severity="critical",
            )
            continue

        # 2. Belongs to a patient we could not link -> withheld, not merged.
        if subject_kind == "foreign":
            ctx.suppress(
                condition,
                section=SECTION,
                label=concept.text,
                reason=subject_note or "Subject could not be linked to this patient.",
                status=f"{clinical.label} / {verification.label}",
                category=IssueCategory.IDENTITY,
                severity="critical",
            )
            continue

        notes: list[str] = []
        if subject_note:
            notes.append(subject_note)
        if precision_note := note_imprecise_date(
            condition.onset_date_time,
            log=ctx.log,
            resource_key=condition.key,
            field="onsetDateTime",
            what="onset date",
        ):
            notes.append(f"Onset: {precision_note}")
        if dangling := check_reference(
            condition.encounter,
            bundle=ctx.bundle,
            log=ctx.log,
            resource_key=condition.key,
            field="encounter",
            what="encounter",
        ):
            notes.append(dangling)
        if condition.clinical_status is None:
            ctx.log.warning(
                IssueCategory.MISSING_DATA,
                f"{condition.key} has no clinicalStatus, so it cannot be shown as an "
                "active problem.",
                resource=condition.key,
                field="clinicalStatus",
                action="Listed under other/unclear problems.",
            )
            notes.append(
                "No clinical status recorded — cannot be confirmed as an active problem."
            )
        if verification.code in rules.VERIFICATION_UNCERTAIN:
            notes.append(
                f"Diagnosis is {verification.label.lower()} — not a confirmed problem."
            )
        if condition.onset_string:
            notes.append(f"Onset recorded as free text: “{condition.onset_string}”.")

        problem = Problem(
            concept=concept,
            clinical_status=clinical,
            verification_status=verification,
            onset=condition.onset_date_time
            or (condition.onset_period.start if condition.onset_period else None),
            recorded=condition.recorded_date,
            provenance=build_provenance(
                condition,
                subject=condition.subject,
                via_linked_identity=subject_kind == "linked",
            ),
            notes=notes,
        )

        key = _dedup_key(condition)
        if key and key in seen:
            _merge_duplicate(ctx, seen[key], problem, condition)
            continue
        if key:
            seen[key] = problem

        (active if clinical.is_current else inactive).append(problem)

    active.sort(key=_recency_key, reverse=True)
    inactive.sort(key=_recency_key, reverse=True)
    return ProblemSection(active=active, inactive=inactive)


def _dedup_key(condition: Condition) -> Optional[str]:
    coding = pick_coding(condition.code)
    label, _ = resolve_label(coding)
    return concept_key(coding, label)


def _merge_duplicate(
    ctx: NormalizationContext,
    kept: Problem,
    duplicate: Problem,
    condition: Condition,
) -> None:
    """Fold a repeat of the same problem into the entry already displayed."""
    kept.provenance.merged_from.append(condition.key)
    kept.notes.append(
        f"A duplicate record for this problem ({condition.key}, "
        f"{duplicate.clinical_status.label}) was folded into this row."
    )
    ctx.log.info(
        IssueCategory.DUPLICATE,
        f"{condition.key} duplicates {kept.provenance.resource} "
        f"({kept.concept.text}).",
        resource=condition.key,
        action="Shown once, with both source records credited.",
    )


def _recency_key(problem: Problem):
    if problem.onset is not None:
        return problem.onset.sort_key
    if problem.recorded is not None:
        return problem.recorded.sort_key
    return datetime(1, 1, 1, tzinfo=timezone.utc)
