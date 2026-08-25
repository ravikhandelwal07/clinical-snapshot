"""AllergyIntolerance -> allergy list.

Allergies get the most conservative handling in the pipeline, because the cost
matrix is lopsided: a spurious warning wastes a clinician's attention, a missing
one can kill someone.

Concretely:

* **Never de-escalate on merge.** The sample bundle records penicillin twice --
  once as ``confirmed`` / ``high`` criticality with display text "Penicillin",
  and once as ``unconfirmed`` / ``unable-to-assess`` with SNOMED ``91936005``
  and no display. Those are the same allergen, so they collapse into one row --
  but the row keeps the *highest* criticality and notes that a second, weaker
  record exists. Merging down to "unable to assess" would be a safety defect.
* **Show unconfirmed allergies.** ``unconfirmed`` and ``provisional`` records
  stay visible with a badge. Only retraction (``entered-in-error`` / ``refuted``)
  removes an allergy from the clinical view.
* **Missing clinicalStatus means possibly active.** Failing closed over-warns,
  which is the correct direction of error here.
* **An empty list is not "no known allergies".** Only an explicit negation
  concept licenses that sentence; the API keeps them as separate fields so the
  UI cannot conflate them.
"""

from __future__ import annotations

from typing import Optional

from app.fhir.resources import AllergyIntolerance
from app.models.issues import IssueCategory
from app.models.summary import Allergy, AllergySection
from app.normalize import rules
from app.normalize.common import (
    build_concept,
    build_provenance,
    note_imprecise_date,
    squash,
)
from app.normalize.context import NormalizationContext
from app.normalize.terminology import allergen_key, pick_coding, resolve_label

SECTION = "Allergies"


def build_allergies(ctx: NormalizationContext) -> AllergySection:
    active: dict[str, Allergy] = {}
    inactive: dict[str, Allergy] = {}
    asserted_none = False

    for record in ctx.bundle.allergies:
        subject_kind, subject_note = ctx.subject_status(record.subject)
        concept = build_concept(
            record.code,
            log=ctx.log,
            resource_key=record.key,
            field="code",
            what="allergen",
        )
        clinical = rules.allergy_clinical_badge(record.clinical_status)
        verification = rules.verification_badge(record.verification_status)

        if rules.is_erroneous(None, verification.code):
            ctx.suppress(
                record,
                section=SECTION,
                label=concept.text,
                reason=(
                    f"Verification status is “{verification.label}” — the source "
                    "system has retracted this allergy record."
                ),
                status=f"{clinical.label} / {verification.label}",
                recorded=record.recorded_date,
                severity="critical",
                noteworthy=True,
            )
            continue

        if subject_kind == "foreign":
            ctx.suppress(
                record,
                section=SECTION,
                label=concept.text,
                reason=subject_note or "Subject could not be linked to this patient.",
                status=f"{clinical.label} / {verification.label}",
                category=IssueCategory.IDENTITY,
                severity="critical",
                noteworthy=True,
            )
            continue

        code = pick_coding(record.code)
        code_value = code.code if code else None
        if code_value in rules.NO_KNOWN_ALLERGY_CODES:
            asserted_none = True
            ctx.log.info(
                IssueCategory.MISSING_DATA,
                f"{record.key} explicitly asserts no known allergies.",
                resource=record.key,
                action="Rendered as a positive 'no known allergies' statement.",
            )
            continue

        criticality = (record.criticality or "").strip().lower() or None
        notes: list[str] = []
        if subject_note:
            notes.append(subject_note)
        if verification.code in rules.VERIFICATION_UNCERTAIN:
            notes.append(
                f"This allergy is {verification.label.lower()} — treat as a possible "
                "allergy pending confirmation."
            )
        if record.clinical_status is None:
            notes.append(
                "No clinical status recorded; treated as possibly active rather than "
                "assumed resolved."
            )
            ctx.log.warning(
                IssueCategory.MISSING_DATA,
                f"{record.key} has no clinicalStatus.",
                resource=record.key,
                field="clinicalStatus",
                action="Treated as possibly active (fails closed) and flagged.",
            )
        if criticality is None:
            notes.append("Criticality not recorded — severity of reaction is unknown.")
        elif criticality == "unable-to-assess":
            notes.append("Criticality could not be assessed by the recorder.")
        if precision_note := note_imprecise_date(
            record.recorded_date,
            log=ctx.log,
            resource_key=record.key,
            field="recordedDate",
            what="recorded date",
        ):
            notes.append(f"Recorded: {precision_note}")

        allergy = Allergy(
            concept=concept,
            clinical_status=clinical,
            verification_status=verification,
            criticality=criticality,
            criticality_label=rules.ALLERGY_CRITICALITY_LABEL.get(
                criticality or "", "Risk not recorded"
            ),
            criticality_rank=rules.ALLERGY_CRITICALITY_RANK.get(criticality or "", 0),
            reactions=_reactions(record),
            recorded=record.recorded_date,
            provenance=build_provenance(
                record,
                subject=record.subject,
                via_linked_identity=subject_kind == "linked",
            ),
            notes=notes,
        )

        bucket = active if clinical.is_current else inactive
        key = _dedup_key(record) or record.key
        if key in bucket:
            _merge(ctx, bucket[key], allergy, record)
        else:
            bucket[key] = allergy

    ordered_active = sorted(
        active.values(),
        key=lambda a: (-a.criticality_rank, a.concept.text.lower()),
    )
    ordered_inactive = sorted(
        inactive.values(), key=lambda a: (-a.criticality_rank, a.concept.text.lower())
    )
    return AllergySection(
        active=ordered_active,
        inactive=ordered_inactive,
        no_known_allergies_asserted=asserted_none,
    )


def _dedup_key(record: AllergyIntolerance) -> Optional[str]:
    coding = pick_coding(record.code)
    label, _ = resolve_label(coding)
    return allergen_key(coding, label)


def _merge(
    ctx: NormalizationContext,
    kept: Allergy,
    duplicate: Allergy,
    record: AllergyIntolerance,
) -> None:
    """Collapse two records for the same allergen, escalating never lowering."""
    kept.provenance.merged_from.append(record.key)

    if duplicate.criticality_rank > kept.criticality_rank:
        kept.criticality = duplicate.criticality
        kept.criticality_label = duplicate.criticality_label
        kept.criticality_rank = duplicate.criticality_rank

    # Prefer a label that came from the source over one we resolved locally.
    if (
        kept.concept.label_is_unresolved
        and not duplicate.concept.label_is_unresolved
    ):
        kept.concept = duplicate.concept

    stronger_verification = (
        duplicate.verification_status.code in rules.VERIFICATION_CONFIRMED
        and kept.verification_status.code not in rules.VERIFICATION_CONFIRMED
    )
    if stronger_verification:
        kept.verification_status = duplicate.verification_status

    if duplicate.clinical_status.is_current and not kept.clinical_status.is_current:
        kept.clinical_status = duplicate.clinical_status

    for reaction in duplicate.reactions:
        if reaction not in kept.reactions:
            kept.reactions.append(reaction)

    kept.notes.append(
        f"A second record for this allergen ({record.key}: "
        f"{duplicate.verification_status.label.lower()}, "
        f"{duplicate.criticality_label.lower()}) was merged into this row. "
        "The more severe criticality is shown."
    )
    ctx.log.warning(
        IssueCategory.DUPLICATE,
        f"{record.key} and {kept.provenance.resource} describe the same allergen "
        f"({kept.concept.text}) under different codes.",
        resource=record.key,
        action=(
            "Merged into one row keeping the highest criticality and the strongest "
            "verification status. Neither source record was discarded."
        ),
    )


def _reactions(record: AllergyIntolerance) -> list[str]:
    out: list[str] = []
    for reaction in record.reaction:
        labels = [
            squash(coding.display)
            for concept in reaction.manifestation
            for coding in concept.coding
            if coding.display
        ]
        text = ", ".join(labels) or squash(reaction.description)
        if reaction.severity:
            text = f"{text} ({reaction.severity})" if text else reaction.severity
        if text:
            out.append(text)
    return out
