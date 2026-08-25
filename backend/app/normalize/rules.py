"""Every "is this safe to show as current clinical fact?" decision, in one file.

Keeping these sets together rather than scattering ``if status == ...`` checks
through the section builders means the safety policy is reviewable in one screen
and testable without constructing a whole snapshot.

The policy, stated plainly:

* **Erroneous data is never displayed as clinical content.** ``entered-in-error``
  on any resource, and ``entered-in-error``/``refuted`` verification on a
  Condition or AllergyIntolerance, means the source system has retracted the
  statement. It goes to the suppressed list with a reason.
* **Not-current is not the same as erroneous.** A ``stopped`` prescription and a
  ``resolved`` problem are true history. They are shown, in their own section,
  never in the active one.
* **Unknown statuses fail closed.** A status we do not recognise is treated as
  not-current and flagged, because inventing "probably fine" is how a
  discontinued drug ends up on an active medication list.
* **Uncertain is shown, labelled.** ``unconfirmed``/``provisional`` findings stay
  visible with a badge. Hiding a possible penicillin allergy is more dangerous
  than showing an unconfirmed one.
"""

from __future__ import annotations

from typing import Optional

from app.fhir.primitives import CodeableConcept
from app.models.summary import StatusBadge

# --------------------------------------------------------------------------- #
# Retraction: the source system says this statement should not exist.
# --------------------------------------------------------------------------- #
ERRONEOUS_STATUSES = {"entered-in-error"}
ERRONEOUS_VERIFICATION = {"entered-in-error", "refuted"}

# --------------------------------------------------------------------------- #
# Condition.clinicalStatus
# --------------------------------------------------------------------------- #
CONDITION_ACTIVE = {"active", "recurrence", "relapse"}
CONDITION_RESOLVED = {"inactive", "remission", "resolved"}

# --------------------------------------------------------------------------- #
# Condition/AllergyIntolerance.verificationStatus
# --------------------------------------------------------------------------- #
VERIFICATION_CONFIRMED = {"confirmed", "presumed"}
VERIFICATION_UNCERTAIN = {"unconfirmed", "provisional", "differential"}

# --------------------------------------------------------------------------- #
# MedicationRequest.status
# --------------------------------------------------------------------------- #
MEDICATION_CURRENT = {"active", "on-hold"}
MEDICATION_PAST = {"completed", "stopped", "cancelled", "ended", "draft"}

# --------------------------------------------------------------------------- #
# AllergyIntolerance.clinicalStatus
# --------------------------------------------------------------------------- #
ALLERGY_ACTIVE = {"active"}
ALLERGY_INACTIVE = {"inactive", "resolved"}

# --------------------------------------------------------------------------- #
# Observation.status -- only these are results a clinician may act on.
# --------------------------------------------------------------------------- #
OBSERVATION_RELIABLE = {"final", "amended", "corrected"}
OBSERVATION_PROVISIONAL = {"preliminary", "registered"}

# --------------------------------------------------------------------------- #
# Encounter.status
# --------------------------------------------------------------------------- #
ENCOUNTER_REAL = {
    "planned", "arrived", "triaged", "in-progress",
    "onleave", "finished",
}
ENCOUNTER_ABANDONED = {"cancelled"}

ALLERGY_CRITICALITY_RANK = {"high": 3, "low": 2, "unable-to-assess": 1}
ALLERGY_CRITICALITY_LABEL = {
    "high": "High risk",
    "low": "Low risk",
    "unable-to-assess": "Risk not assessed",
}

#: SNOMED CT concepts that assert an *absence* of allergies. Only these justify
#: printing "no known allergies"; an empty list never does.
NO_KNOWN_ALLERGY_CODES = {"716186003", "409137002"}


def status_code(concept: Optional[CodeableConcept]) -> Optional[str]:
    """The code of a status CodeableConcept, lowercased."""
    if concept is None:
        return None
    code = concept.code_value()
    if code:
        return code.strip().lower()
    if concept.text:
        return concept.text.strip().lower()
    return None


def status_label(concept: Optional[CodeableConcept], fallback: str) -> str:
    """Prefer the source's own display text so we echo its vocabulary."""
    if concept is not None:
        if concept.coding and concept.coding[0].display:
            return concept.coding[0].display
        if concept.text:
            return concept.text
        code = concept.code_value()
        if code:
            return code.replace("-", " ").capitalize()
    return fallback


def is_erroneous(
    status: Optional[str], verification: Optional[str] = None
) -> bool:
    """True when the source has retracted the statement."""
    return (status in ERRONEOUS_STATUSES) or (verification in ERRONEOUS_VERIFICATION)


def badge(
    code: Optional[str],
    label: str,
    *,
    is_current: bool,
    tone: str = "neutral",
) -> StatusBadge:
    return StatusBadge(code=code, label=label, is_current=is_current, tone=tone)


def condition_clinical_badge(concept: Optional[CodeableConcept]) -> StatusBadge:
    code = status_code(concept)
    label = status_label(concept, "Status not recorded")
    if code in CONDITION_ACTIVE:
        return badge(code, label, is_current=True, tone="caution")
    if code in CONDITION_RESOLVED:
        return badge(code, label, is_current=False, tone="neutral")
    if code is None:
        return badge(None, "Clinical status not recorded", is_current=False, tone="caution")
    return badge(code, label, is_current=False, tone="caution")


def verification_badge(concept: Optional[CodeableConcept]) -> StatusBadge:
    code = status_code(concept)
    label = status_label(concept, "Not verified")
    if code in VERIFICATION_CONFIRMED:
        return badge(code, label, is_current=True, tone="positive")
    if code in VERIFICATION_UNCERTAIN:
        return badge(code, label, is_current=True, tone="caution")
    if code in ERRONEOUS_VERIFICATION:
        return badge(code, label, is_current=False, tone="danger")
    if code is None:
        return badge(None, "Verification not recorded", is_current=True, tone="caution")
    return badge(code, label, is_current=True, tone="caution")


def medication_badge(status: Optional[str]) -> StatusBadge:
    code = (status or "").strip().lower() or None
    label = (code or "status not recorded").replace("-", " ").capitalize()
    if code in MEDICATION_CURRENT:
        tone = "positive" if code == "active" else "caution"
        return badge(code, label, is_current=True, tone=tone)
    if code in ERRONEOUS_STATUSES:
        return badge(code, label, is_current=False, tone="danger")
    if code in MEDICATION_PAST or code == "unknown" or code is None:
        return badge(code, label, is_current=False, tone="neutral")
    return badge(code, label, is_current=False, tone="caution")


def allergy_clinical_badge(concept: Optional[CodeableConcept]) -> StatusBadge:
    code = status_code(concept)
    label = status_label(concept, "Status not recorded")
    if code in ALLERGY_ACTIVE:
        return badge(code, label, is_current=True, tone="danger")
    if code in ALLERGY_INACTIVE:
        return badge(code, label, is_current=False, tone="neutral")
    if code is None:
        # An allergy with no clinical status is treated as *potentially active*.
        # Failing closed here means over-warning, which is the safe direction.
        return badge(None, "Clinical status not recorded", is_current=True, tone="caution")
    return badge(code, label, is_current=True, tone="caution")


def observation_badge(status: Optional[str]) -> StatusBadge:
    code = (status or "").strip().lower() or None
    label = (code or "status not recorded").replace("-", " ").capitalize()
    if code in OBSERVATION_RELIABLE:
        return badge(code, label, is_current=True, tone="positive")
    if code in ERRONEOUS_STATUSES:
        return badge(code, label, is_current=False, tone="danger")
    if code in OBSERVATION_PROVISIONAL:
        return badge(code, label, is_current=True, tone="caution")
    return badge(code, label, is_current=False, tone="caution")


def encounter_badge(status: Optional[str]) -> StatusBadge:
    code = (status or "").strip().lower() or None
    label = (code or "status not recorded").replace("-", " ").capitalize()
    if code in ERRONEOUS_STATUSES:
        return badge(code, label, is_current=False, tone="danger")
    if code in ENCOUNTER_ABANDONED:
        return badge(code, label, is_current=False, tone="neutral")
    if code in ENCOUNTER_REAL:
        return badge(code, label, is_current=True, tone="neutral")
    return badge(code, label, is_current=False, tone="caution")
