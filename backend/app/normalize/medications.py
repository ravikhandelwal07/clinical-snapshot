"""MedicationRequest -> medication list.

The two failure modes that matter here are opposite in direction and both bad:

* A discontinued drug shown as current invites a duplicate prescription.
  So ``stopped``/``completed``/``cancelled`` go to a clearly-labelled past
  section, and any status we do not recognise fails closed into it as well.
* A current drug *missing* from the list is worse. ``medicationrequest-003``
  in the sample bundle is an ``active`` order that hangs off the second Patient
  resource. Dropping it because the MRN did not match exactly would hand a
  clinician an incomplete med list with no indication anything was missing.
  It is included, and tagged with the identity caveat.

We also refuse to name a drug we cannot identify. RxNorm ``849574`` arrives with
no ``display`` and is not in the curated code table, so it renders as
"RxNorm 849574" with an explicit unresolved flag. An unlabelled row prompts a
lookup; a guessed label causes a medication error.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.fhir.resources import MedicationRequest
from app.models.issues import IssueCategory
from app.models.summary import CodedConcept, Medication, MedicationSection
from app.normalize.terminology import LabelSource
from app.normalize import rules
from app.normalize.common import (
    build_concept,
    build_provenance,
    check_reference,
    note_imprecise_date,
    squash,
)
from app.normalize.context import NormalizationContext

SECTION = "Medications"


def build_medications(ctx: NormalizationContext) -> MedicationSection:
    current: list[Medication] = []
    past: list[Medication] = []

    for request in ctx.bundle.medication_requests:
        subject_kind, subject_note = ctx.subject_status(request.subject)
        concept = _medication_concept(ctx, request)
        status = rules.medication_badge(request.status)

        if rules.is_erroneous(status.code):
            ctx.suppress(
                request,
                section=SECTION,
                label=concept.text,
                reason=(
                    "Order status is “entered in error”, so the source system has "
                    "retracted this prescription."
                ),
                status=status.label,
                recorded=request.authored_on,
                severity="critical",
            )
            continue

        if subject_kind == "foreign":
            ctx.suppress(
                request,
                section=SECTION,
                label=concept.text,
                reason=subject_note or "Subject could not be linked to this patient.",
                status=status.label,
                recorded=request.authored_on,
                category=IssueCategory.IDENTITY,
                severity="critical",
                noteworthy=status.is_current,
            )
            continue

        notes: list[str] = []
        if subject_note:
            notes.append(subject_note)
            if status.is_current:
                ctx.log.critical(
                    IssueCategory.IDENTITY,
                    f"{request.key} is an active medication order attached to "
                    f"{request.subject.key if request.subject else 'an unknown subject'}, "
                    "not to the primary Patient record.",
                    resource=request.key,
                    field="subject",
                    action=(
                        "Included in the current medication list because omitting an "
                        "active order is the more dangerous error, and flagged in the "
                        "row so the identity assumption is visible."
                    ),
                )
        if request.intent and request.intent not in ("order", "original-order"):
            notes.append(
                f"Intent is “{request.intent}”, not a placed order — may be a "
                "proposal or plan rather than a live prescription."
            )
        if precision_note := note_imprecise_date(
            request.authored_on,
            log=ctx.log,
            resource_key=request.key,
            field="authoredOn",
            what="authored date",
        ):
            notes.append(f"Authored: {precision_note}")
        if dangling := check_reference(
            request.encounter,
            bundle=ctx.bundle,
            log=ctx.log,
            resource_key=request.key,
            field="encounter",
            what="encounter",
        ):
            notes.append(dangling)
        if not status.is_current and status.code not in rules.MEDICATION_PAST:
            notes.append(
                f"Status “{status.label}” is not a recognised active state; treated "
                "as not current."
            )
        if request.status_reason is not None:
            reason = build_concept(
                request.status_reason,
                log=ctx.log,
                resource_key=request.key,
                field="statusReason",
                what="medication status reason",
            )
            notes.append(f"Status reason: {reason.text}.")

        dosage = _dosage_text(request)
        if dosage is None:
            ctx.log.warning(
                IssueCategory.MISSING_DATA,
                f"{request.key} has no dosage instruction text.",
                resource=request.key,
                field="dosageInstruction",
                action="Dose shown as not recorded.",
            )
        elif dosage.lower() in ("as directed", "as needed", "per protocol"):
            notes.append(
                "Dosage instruction is non-specific — the actual regimen is not in "
                "this record."
            )

        medication = Medication(
            concept=concept,
            status=status,
            intent=request.intent,
            dosage_text=dosage,
            authored_on=request.authored_on,
            provenance=build_provenance(
                request,
                subject=request.subject,
                via_linked_identity=subject_kind == "linked",
            ),
            notes=notes,
        )
        (current if status.is_current else past).append(medication)

    current.sort(key=lambda m: _sort_key(m), reverse=True)
    past.sort(key=lambda m: _sort_key(m), reverse=True)
    return MedicationSection(current=current, past=past)


def _medication_concept(ctx: NormalizationContext, request: MedicationRequest):
    """``medication[x]`` is a choice: a codeable concept or a reference."""
    if request.medication_codeable_concept is not None:
        return build_concept(
            request.medication_codeable_concept,
            log=ctx.log,
            resource_key=request.key,
            field="medicationCodeableConcept",
            what="medication",
        )

    reference = request.medication_reference
    if reference is not None:
        check_reference(
            reference,
            bundle=ctx.bundle,
            log=ctx.log,
            resource_key=request.key,
            field="medicationReference",
            what="medication",
        )
        return CodedConcept(
            text=reference.display or f"Medication {reference.key or 'unknown'}",
            label_source=LabelSource.SOURCE if reference.display else LabelSource.CODE_ONLY,
        )

    ctx.log.critical(
        IssueCategory.MISSING_DATA,
        f"{request.key} identifies no medication at all.",
        resource=request.key,
        field="medication[x]",
        action="Shown as an unidentified medication rather than dropped.",
    )
    return CodedConcept(text="Medication not identified", label_source=LabelSource.ABSENT)


def _dosage_text(request: MedicationRequest) -> Optional[str]:
    texts = [squash(d.text) for d in request.dosage_instruction if d.text]
    return "; ".join(t for t in texts if t) or None


def _sort_key(medication: Medication):
    if medication.authored_on is not None:
        return medication.authored_on.sort_key
    return datetime(1, 1, 1, tzinfo=timezone.utc)
