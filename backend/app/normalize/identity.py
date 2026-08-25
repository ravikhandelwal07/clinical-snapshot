"""Patient identity resolution across multiple Patient resources.

The sample bundle contains two Patient resources for what is almost certainly
one person, and -- critically -- an **active medication order hanging off the
second one**. That makes this the most consequential decision in the pipeline,
because both of the easy answers are unsafe:

* Show only ``patient-001`` and you silently drop an active prescription.
  A clinician reading the snapshot would believe they have the full med list.
* Blindly merge and you may attribute another person's medication to this
  patient. The MRNs are *not* the same (``MRN-48213`` vs ``MRN-48213-A``).

So we do neither. We score the match, and when it is *probable* we link the
records, show the clinical content, and mark every item that arrived via the
link so the reader can see exactly which facts depend on the identity
assumption. Demographic conflicts are surfaced rather than resolved away.

Escalation thresholds are conservative on purpose: only an identical identifier
in the same system yields ``CERTAIN``. Two records that merely look alike stay
``PROBABLE`` forever, however high the field agreement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.fhir.primitives import (
    Address,
    AgeEstimate,
    Coding,
    DatePrecision,
    PartialDateTime,
)
from app.fhir.resources import (
    US_CORE_ETHNICITY_URL,
    US_CORE_RACE_URL,
    Patient,
)
from app.models.issues import IssueCategory, IssueLog
from app.models.summary import (
    CodedConcept,
    Confidence,
    ConflictValue,
    Demographics,
    FieldConflict,
    IdentityResolution,
)
from app.normalize.common import normalize_street, normalize_token_text, squash
from app.normalize.terminology import LabelSource, system_name

MRN_SYSTEM_HINTS = ("mrn", "medical-record", "medicalrecord")

#: Identifier systems whose values are never returned to the client.
WITHHELD_IDENTIFIER_SYSTEMS = {
    "http://hl7.org/fhir/sid/us-ssn": "US Social Security Number",
}

#: Field weights. They sum to 1.0 so ``score`` reads as a fraction of agreement.
W_FAMILY = 0.25
W_GIVEN = 0.20
W_GENDER = 0.10
W_BIRTH_EXACT = 0.20
W_BIRTH_COMPATIBLE = 0.14
W_ADDRESS = 0.15
W_IDENTIFIER_EXACT = 0.10
W_IDENTIFIER_VARIANT = 0.05
GENDER_PENALTY = 0.15

LINK_THRESHOLD = 0.75  # link and display, flagged
REVIEW_THRESHOLD = 0.50  # surface, do not merge, withhold clinical content


@dataclass
class MatchAssessment:
    score: float
    matched_on: list[str] = field(default_factory=list)
    differed_on: list[str] = field(default_factory=list)
    exact_identifier: bool = False
    blocking_reason: Optional[str] = None

    @property
    def confidence(self) -> Confidence:
        if self.blocking_reason:
            return Confidence.UNRESOLVED
        if self.exact_identifier:
            return Confidence.CERTAIN
        if self.score >= LINK_THRESHOLD:
            return Confidence.PROBABLE
        if self.score >= REVIEW_THRESHOLD:
            return Confidence.POSSIBLE
        return Confidence.UNRESOLVED


@dataclass
class ResolvedIdentity:
    primary: Patient
    linked: list[Patient]
    unlinked: list[Patient]
    resolution: IdentityResolution
    demographics: Demographics
    #: Patient reference keys whose clinical resources belong in the snapshot.
    accepted_keys: set[str]
    #: Subset of the above that were only *probably* the same person.
    linked_keys: set[str]


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
def _identifiers(patient: Patient) -> list[tuple[str, str]]:
    return [
        (i.system or "", squash(i.value))
        for i in patient.identifier
        if i.value and (i.system or "") not in WITHHELD_IDENTIFIER_SYSTEMS
    ]


def _mrn(patient: Patient) -> Optional[str]:
    """The MRN if one is identifiable, else the first non-withheld identifier."""
    for identifier in patient.identifier:
        system = (identifier.system or "").lower()
        if identifier.value and any(hint in system for hint in MRN_SYSTEM_HINTS):
            return squash(identifier.value)
    remaining = _identifiers(patient)
    return remaining[0][1] if remaining else None


def _first_given(patient: Patient) -> Optional[str]:
    for name in patient.name:
        if name.given:
            return normalize_token_text(name.given[0])
    return None


def _family(patient: Patient) -> Optional[str]:
    for name in patient.name:
        if name.family:
            return normalize_token_text(name.family)
    return None


def _address_key(address: Optional[Address]) -> Optional[str]:
    if address is None:
        return None
    street = normalize_street(" ".join(address.line))
    postal = normalize_token_text(address.postal_code)
    city = normalize_token_text(address.city)
    if not street:
        return None
    return f"{street}|{postal or city}"


def _primary_address(patient: Patient) -> Optional[Address]:
    if not patient.address:
        return None
    for address in patient.address:
        if (address.use or "home") == "home":
            return address
    return patient.address[0]


def _birth_dates_compatible(
    left: Optional[PartialDateTime], right: Optional[PartialDateTime]
) -> Optional[bool]:
    """None when either side is missing; otherwise whether the ranges overlap."""
    if left is None or right is None:
        return None
    if DatePrecision.UNKNOWN in (left.precision, right.precision):
        return None
    l_start, l_end = left.bounds()
    r_start, r_end = right.bounds()
    return l_start <= r_end and r_start <= l_end


def _identifier_relationship(left: Patient, right: Patient) -> Optional[str]:
    """``exact``, ``variant`` (one is a suffixed form of the other), or None."""
    left_ids = _identifiers(left)
    right_ids = _identifiers(right)
    for l_system, l_value in left_ids:
        for r_system, r_value in right_ids:
            if l_system != r_system:
                continue
            if l_value == r_value:
                return "exact"
            longer, shorter = sorted((l_value, r_value), key=len, reverse=True)
            if shorter and longer.startswith(shorter) and len(longer) - len(shorter) <= 3:
                return "variant"
    return None


def assess_match(left: Patient, right: Patient) -> MatchAssessment:
    """Score two Patient resources as being the same person."""
    assessment = MatchAssessment(score=0.0)

    l_family, r_family = _family(left), _family(right)
    if l_family and r_family:
        if l_family == r_family:
            assessment.score += W_FAMILY
            assessment.matched_on.append("family name")
        else:
            assessment.differed_on.append("family name")

    l_given, r_given = _first_given(left), _first_given(right)
    if l_given and r_given:
        if l_given == r_given:
            assessment.score += W_GIVEN
            assessment.matched_on.append("first given name")
        else:
            assessment.differed_on.append("first given name")

    if left.gender and right.gender:
        if left.gender == right.gender:
            assessment.score += W_GENDER
            assessment.matched_on.append("administrative gender")
        else:
            assessment.score -= GENDER_PENALTY
            assessment.differed_on.append("administrative gender")

    compatible = _birth_dates_compatible(left.birth_date, right.birth_date)
    if compatible is False:
        assessment.blocking_reason = (
            f"Birth dates cannot refer to the same person "
            f"({left.birth_date.raw if left.birth_date else '?'} vs "
            f"{right.birth_date.raw if right.birth_date else '?'})."
        )
        assessment.differed_on.append("birth date")
    elif compatible is True:
        same_precision = (
            left.birth_date is not None
            and right.birth_date is not None
            and left.birth_date.precision is right.birth_date.precision
            and left.birth_date.raw == right.birth_date.raw
        )
        assessment.score += W_BIRTH_EXACT if same_precision else W_BIRTH_COMPATIBLE
        assessment.matched_on.append(
            "birth date" if same_precision else "birth date (compatible, differing precision)"
        )

    l_address = _address_key(_primary_address(left))
    r_address = _address_key(_primary_address(right))
    if l_address and r_address:
        if l_address == r_address:
            assessment.score += W_ADDRESS
            assessment.matched_on.append("home address")
        else:
            assessment.differed_on.append("home address")

    relationship = _identifier_relationship(left, right)
    if relationship == "exact":
        assessment.score += W_IDENTIFIER_EXACT
        assessment.exact_identifier = True
        assessment.matched_on.append("identical identifier")
    elif relationship == "variant":
        assessment.score += W_IDENTIFIER_VARIANT
        assessment.matched_on.append("related identifier (suffixed variant)")
        assessment.differed_on.append("medical record number")
    else:
        assessment.differed_on.append("medical record number")

    assessment.score = round(max(0.0, min(1.0, assessment.score)), 3)
    return assessment


# --------------------------------------------------------------------------- #
# Primary record selection
# --------------------------------------------------------------------------- #
def _completeness(patient: Patient) -> tuple[int, int, int]:
    """Sort key: (populated fields, date precision, US Core conformance)."""
    populated = sum(
        1
        for value in (
            patient.name,
            patient.identifier,
            patient.telecom,
            patient.address,
            patient.gender,
            patient.birth_date,
            patient.extension,
        )
        if value
    )
    precision_rank = {
        DatePrecision.INSTANT: 4,
        DatePrecision.DAY: 4,
        DatePrecision.MONTH: 2,
        DatePrecision.YEAR: 1,
        DatePrecision.UNKNOWN: 0,
    }
    precision = precision_rank.get(
        patient.birth_date.precision if patient.birth_date else DatePrecision.UNKNOWN, 0
    )
    profiles = len(patient.meta.profile) if patient.meta else 0
    extra_names = sum(len(n.given) for n in patient.name)
    return (populated + extra_names, precision, profiles)


# --------------------------------------------------------------------------- #
# US Core extensions
# --------------------------------------------------------------------------- #
def _us_core_concept(patient: Patient, url: str) -> Optional[CodedConcept]:
    extension = patient.extension_by_url(url)
    if extension is None:
        return None
    omb = extension.child("ombCategory")
    text_ext = extension.child("text")
    coding: Optional[Coding] = omb.value_coding if omb else None
    label = None
    source = LabelSource.ABSENT
    if coding and coding.display:
        label, source = coding.display, LabelSource.SOURCE
    elif text_ext and text_ext.value_string:
        label, source = text_ext.value_string, LabelSource.SOURCE
    if label is None and coding and coding.code:
        return CodedConcept(
            text=f"{system_name(coding.system) or 'Coded'} {coding.code}",
            code=coding.code,
            system_uri=coding.system,
            system_name=system_name(coding.system),
            label_source=LabelSource.CODE_ONLY,
        )
    if label is None:
        return None
    return CodedConcept(
        text=label,
        code=coding.code if coding else None,
        system_uri=coding.system if coding else None,
        system_name=system_name(coding.system) if coding else None,
        label_source=source,
    )


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def resolve_identity(
    patients: list[Patient], *, as_of: datetime, log: IssueLog
) -> Optional[ResolvedIdentity]:
    if not patients:
        return None

    ranked = sorted(patients, key=_completeness, reverse=True)
    primary = ranked[0]
    others = ranked[1:]

    linked: list[Patient] = []
    unlinked: list[Patient] = []
    matched_on: list[str] = []
    differed_on: list[str] = []
    scores: list[float] = []
    confidence = Confidence.CERTAIN

    for candidate in others:
        assessment = assess_match(primary, candidate)
        scores.append(assessment.score)
        matched_on.extend(assessment.matched_on)
        differed_on.extend(assessment.differed_on)
        candidate_confidence = assessment.confidence

        if candidate_confidence in (Confidence.CERTAIN, Confidence.PROBABLE):
            linked.append(candidate)
            severity = log.info if candidate_confidence is Confidence.CERTAIN else log.warning
            severity(
                IssueCategory.IDENTITY,
                f"{candidate.key} was linked to {primary.key} as the same person "
                f"({candidate_confidence.value} match, score {assessment.score:.2f}; "
                f"agreed on {', '.join(assessment.matched_on) or 'nothing'}; "
                f"differed on {', '.join(sorted(set(assessment.differed_on))) or 'nothing'}).",
                resource=candidate.key,
                action=(
                    "Clinical records attached to this Patient resource are included "
                    "and individually marked as arriving via a probabilistic identity "
                    "link."
                    if candidate_confidence is Confidence.PROBABLE
                    else "Records merged on an identical identifier."
                ),
            )
        else:
            unlinked.append(candidate)
            log.critical(
                IssueCategory.IDENTITY,
                f"{candidate.key} could not be confidently linked to {primary.key} "
                f"({candidate_confidence.value}, score {assessment.score:.2f})."
                + (f" {assessment.blocking_reason}" if assessment.blocking_reason else ""),
                resource=candidate.key,
                action=(
                    "Its clinical records are listed as withheld rather than merged "
                    "into this patient's snapshot."
                ),
            )

        order = [
            Confidence.CERTAIN,
            Confidence.PROBABLE,
            Confidence.POSSIBLE,
            Confidence.UNRESOLVED,
        ]
        if order.index(candidate_confidence) > order.index(confidence):
            confidence = candidate_confidence

    demographics, conflicts = _merge_demographics(
        primary, linked, as_of=as_of, log=log
    )

    resolution = IdentityResolution(
        primary_resource=primary.key,
        linked_resources=[p.key for p in linked],
        unlinked_resources=[p.key for p in unlinked],
        confidence=confidence if others else Confidence.CERTAIN,
        score=min(scores) if scores else 1.0,
        matched_on=sorted(set(matched_on)),
        differed_on=sorted(set(differed_on)),
        conflicts=conflicts,
        narrative=_narrative(primary, linked, unlinked, confidence),
    )

    return ResolvedIdentity(
        primary=primary,
        linked=linked,
        unlinked=unlinked,
        resolution=resolution,
        demographics=demographics,
        accepted_keys={primary.key, *(p.key for p in linked)},
        linked_keys={p.key for p in linked},
    )


def _narrative(
    primary: Patient,
    linked: list[Patient],
    unlinked: list[Patient],
    confidence: Confidence,
) -> str:
    if not linked and not unlinked:
        return "A single Patient record was found in this bundle."
    parts: list[str] = []
    if linked:
        ids = ", ".join(p.key for p in linked)
        if confidence is Confidence.CERTAIN:
            parts.append(
                f"{len(linked) + 1} Patient records were merged on a matching "
                f"identifier ({primary.key} + {ids})."
            )
        else:
            parts.append(
                f"{len(linked) + 1} Patient records appear to describe the same "
                f"person and have been combined ({primary.key} + {ids}). The match "
                "is probable, not certain — the medical record numbers differ. "
                "Items sourced from the linked record are marked individually."
            )
    if unlinked:
        parts.append(
            f"{len(unlinked)} further Patient record(s) ("
            + ", ".join(p.key for p in unlinked)
            + ") could not be confidently matched and were not merged; their "
            "clinical records are listed as withheld."
        )
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# Demographic merge
# --------------------------------------------------------------------------- #
def _format_address(address: Address) -> str:
    parts = [squash(" ".join(address.line))]
    locality = ", ".join(p for p in (squash(address.city), squash(address.state)) if p)
    if locality:
        parts.append(locality)
    if address.postal_code:
        parts.append(squash(address.postal_code))
    if address.country:
        parts.append(squash(address.country))
    return ", ".join(p for p in parts if p)


def _merge_demographics(
    primary: Patient,
    linked: list[Patient],
    *,
    as_of: datetime,
    log: IssueLog,
) -> tuple[Demographics, list[FieldConflict]]:
    """Take the most precise value per field; report every disagreement.

    Nothing is averaged, blended or invented. Where the records disagree, the
    primary record's value wins and the alternative is preserved -- either as an
    explicit conflict for the UI to show, or as an "also known" entry.
    """
    conflicts: list[FieldConflict] = []
    sources = [primary, *linked]

    # --- name ---------------------------------------------------------------
    best_name = max(
        (n for p in sources for n in p.name),
        key=lambda n: (n.use == "official", len(n.given), len(n.family or "")),
        default=None,
    )
    given = list(best_name.given) if best_name else []
    family = best_name.family if best_name else None
    full_name = squash(" ".join([*given, family or ""])) or "Name not recorded"
    name_note = None
    given_variants = {" ".join(n.given) for p in sources for n in p.name if n.given}
    if len(given_variants) > 1:
        name_note = (
            "Source records differ on given names ("
            + "; ".join(sorted(given_variants))
            + "); the most complete form is shown."
        )
        conflicts.append(
            FieldConflict(
                field="Given names",
                values=[
                    ConflictValue(value=" ".join(n.given), source=p.key)
                    for p in sources
                    for n in p.name
                    if n.given
                ],
                chosen=" ".join(given),
                rationale="Most complete name retained; no name component was dropped.",
            )
        )

    # --- birth date ---------------------------------------------------------
    birth_candidates = [(p, p.birth_date) for p in sources if p.birth_date]
    precision_rank = {
        DatePrecision.INSTANT: 4,
        DatePrecision.DAY: 3,
        DatePrecision.MONTH: 2,
        DatePrecision.YEAR: 1,
        DatePrecision.UNKNOWN: 0,
    }
    birth_date = None
    if birth_candidates:
        owner, birth_date = max(
            birth_candidates, key=lambda item: precision_rank[item[1].precision]
        )
        if len({b.raw for _, b in birth_candidates}) > 1:
            conflicts.append(
                FieldConflict(
                    field="Birth date",
                    values=[
                        ConflictValue(value=b.raw, source=p.key)
                        for p, b in birth_candidates
                    ],
                    chosen=birth_date.raw,
                    rationale=(
                        f"The values are consistent; {owner.key} records the highest "
                        "precision, so it is used for age. The less precise value was "
                        "not treated as a contradiction."
                    ),
                )
            )
            log.info(
                IssueCategory.DATE_PRECISION,
                "Patient birth date is recorded at different precisions across "
                "source records ("
                + ", ".join(f"{p.key}: {b.raw}" for p, b in birth_candidates)
                + ").",
                resource=owner.key,
                field="birthDate",
                action=f"Used {birth_date.raw} (highest precision) for age calculation.",
            )

    # --- gender -------------------------------------------------------------
    genders = {p.gender for p in sources if p.gender}
    gender = primary.gender or next(iter(genders), None)
    if len(genders) > 1:
        conflicts.append(
            FieldConflict(
                field="Administrative gender",
                values=[
                    ConflictValue(value=p.gender, source=p.key)
                    for p in sources
                    if p.gender
                ],
                chosen=gender,
                rationale="Records disagree. The primary record's value is shown; "
                "this conflict needs human resolution.",
            )
        )

    # --- identifiers --------------------------------------------------------
    mrn = _mrn(primary)
    other_identifiers: list[str] = []
    withheld: list[str] = []
    for patient in sources:
        for identifier in patient.identifier:
            system = identifier.system or ""
            if system in WITHHELD_IDENTIFIER_SYSTEMS:
                label = WITHHELD_IDENTIFIER_SYSTEMS[system]
                if label not in withheld:
                    withheld.append(label)
                    log.info(
                        IssueCategory.PHI_MINIMIZATION,
                        f"A {label} identifier is present in the bundle.",
                        resource=patient.key,
                        field="identifier",
                        action=(
                            "Not included in the API response or the UI; a clinical "
                            "snapshot has no need for it."
                        ),
                    )
                continue
            if not identifier.value:
                continue
            value = squash(identifier.value)
            if value == mrn:
                continue
            entry = f"{value} ({system_name(system) or 'unspecified system'}, from {patient.key})"
            if entry not in other_identifiers:
                other_identifiers.append(entry)
    if other_identifiers:
        conflicts.append(
            FieldConflict(
                field="Medical record number",
                values=[
                    ConflictValue(value=squash(i.value), source=p.key)
                    for p in sources
                    for i in p.identifier
                    if i.value and (i.system or "") not in WITHHELD_IDENTIFIER_SYSTEMS
                ],
                chosen=mrn,
                rationale=(
                    "The linked records carry different MRNs in the same system. "
                    "Both are shown; neither was overwritten."
                ),
            )
        )

    # --- contact ------------------------------------------------------------
    phones: list[str] = []
    for patient in sources:
        for contact in patient.telecom:
            if (contact.system or "phone") != "phone" or not contact.value:
                continue
            entry = f"{squash(contact.value)}" + (f" ({contact.use})" if contact.use else "")
            if entry not in phones:
                phones.append(entry)

    # --- address ------------------------------------------------------------
    primary_address = _primary_address(primary)
    address_text = _format_address(primary_address) if primary_address else None
    alternates: list[str] = []
    for patient in linked:
        candidate = _primary_address(patient)
        if candidate is None:
            continue
        formatted = _format_address(candidate)
        if formatted != address_text:
            same_place = _address_key(candidate) == _address_key(primary_address)
            note = " — same address, differently formatted" if same_place else ""
            alternates.append(f"{formatted} (from {patient.key}){note}")

    demographics = Demographics(
        full_name=full_name,
        family_name=family,
        given_names=given,
        name_note=name_note,
        gender=gender,
        birth_date=birth_date,
        age=AgeEstimate.from_birth_date(birth_date, as_of),
        deceased=primary.deceased_boolean,
        mrn=mrn,
        other_identifiers=other_identifiers,
        withheld_identifier_systems=withheld,
        phones=phones,
        address=address_text,
        alternate_addresses=alternates,
        race=_us_core_concept(primary, US_CORE_RACE_URL),
        ethnicity=_us_core_concept(primary, US_CORE_ETHNICITY_URL),
        us_core_profiles=[
            profile
            for patient in sources
            if patient.meta
            for profile in patient.meta.profile
        ],
    )
    return demographics, conflicts
