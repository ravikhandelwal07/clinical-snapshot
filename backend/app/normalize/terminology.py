"""Code resolution, with provenance attached to every label.

The rule this module exists to enforce: **the UI must never show a human-readable
label without saying where that label came from.**

A code with no ``display`` is a real gap. There are three honest ways to fill it
and one dishonest one:

* use the sender's ``display``                    -> ``LabelSource.SOURCE``
* look it up in a curated, hand-verified table    -> ``LabelSource.LOCAL_TABLE``
* query a terminology server (not available here) -> future work
* let a language model write a plausible label    -> **never**

The local table below is deliberately tiny and only contains concepts I could
verify. Codes I could not verify -- notably RxNorm ``849574`` -- are left
unresolved and rendered as "RxNorm 849574 (no label in source)". An unlabelled
medication is a visible gap a clinician will chase; a *wrongly* labelled one is
a medication error. See README, "Terminology".
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional

from app.fhir.primitives import CodeableConcept, Coding

# ---------------------------------------------------------------------------
# Code systems
# ---------------------------------------------------------------------------
LOINC = "http://loinc.org"
SNOMED = "http://snomed.info/sct"
ICD10CM = "http://hl7.org/fhir/sid/icd-10-cm"
RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
UCUM = "http://unitsofmeasure.org"

SYSTEM_NAMES: dict[str, str] = {
    LOINC: "LOINC",
    SNOMED: "SNOMED CT",
    ICD10CM: "ICD-10-CM",
    RXNORM: "RxNorm",
    UCUM: "UCUM",
    "http://hl7.org/fhir/sid/icd-10": "ICD-10",
    "http://hl7.org/fhir/sid/us-ssn": "US SSN",
    "http://terminology.hl7.org/CodeSystem/v3-ActCode": "HL7 ActCode",
    "http://terminology.hl7.org/CodeSystem/condition-clinical": "Condition Clinical Status",
    "http://terminology.hl7.org/CodeSystem/condition-ver-status": "Condition Verification Status",
    "http://terminology.hl7.org/CodeSystem/observation-category": "Observation Category",
    "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical": "Allergy Clinical Status",
    "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification": "Allergy Verification Status",
    "urn:oid:2.16.840.1.113883.6.238": "CDC Race & Ethnicity",
}

#: Structural shape of a valid code in each system. Used to catch codes filed
#: under the wrong system -- a class of error that silently corrupts any
#: downstream code-based logic (dedup, decision support, quality measures).
CODE_PATTERNS: dict[str, tuple[re.Pattern[str], str]] = {
    SNOMED: (re.compile(r"^\d{6,18}$"), "6-18 digits, no punctuation"),
    LOINC: (re.compile(r"^\d{1,5}-\d$"), "digits followed by a check digit, e.g. 4548-4"),
    ICD10CM: (
        re.compile(r"^[A-TV-Z]\d[A-Z0-9](?:\.[A-Z0-9]{1,4})?$"),
        "letter, digit, then up to 5 more characters, e.g. E11.9",
    ),
    RXNORM: (re.compile(r"^\d+$"), "digits only"),
}


class LabelSource(str, Enum):
    SOURCE = "source"
    LOCAL_TABLE = "local_table"
    CODE_ONLY = "code_only"
    ABSENT = "absent"


# ---------------------------------------------------------------------------
# Curated table. Every entry here was checked by hand against the published
# code system. Adding a row is a clinical-safety change, not a convenience one.
# ---------------------------------------------------------------------------
LOCAL_CODE_TABLE: dict[tuple[str, str], str] = {
    (ICD10CM, "E11.9"): "Type 2 diabetes mellitus without complications",
    (ICD10CM, "I10"): "Essential (primary) hypertension",
    (ICD10CM, "J45.909"): "Unspecified asthma, uncomplicated",
    (LOINC, "4548-4"): "Hemoglobin A1c (total haemoglobin in blood)",
    (LOINC, "85354-9"): "Blood pressure panel",
    (LOINC, "8480-6"): "Systolic blood pressure",
    (LOINC, "8462-4"): "Diastolic blood pressure",
    (LOINC, "29463-7"): "Body weight",
    (LOINC, "2160-0"): "Creatinine [mass/volume] in serum or plasma",
    (SNOMED, "91936005"): "Allergy to penicillin",
    (SNOMED, "300916003"): "Latex allergy",
    (SNOMED, "185349003"): "Encounter for check up",
}

#: Canonical concept keys used for clinical deduplication. Two source codings
#: that map to the same key describe the same thing even though their codes
#: differ. Kept separate from the label table because "these are the same
#: concept" is a stronger claim than "this code is called X".
CONCEPT_ALIASES: dict[tuple[str, str], str] = {
    (SNOMED, "91936005"): "allergen:penicillin",
    (SNOMED, "7980-2"): "allergen:penicillin",  # mis-systemed penicillin code
    (SNOMED, "300916003"): "allergen:latex",
    (ICD10CM, "E11.9"): "problem:type-2-diabetes",
    (ICD10CM, "I10"): "problem:essential-hypertension",
    (ICD10CM, "J45.909"): "problem:asthma",
}

#: Codes that identify a vital sign even when the resource omits ``category``.
VITAL_SIGN_CODES: set[str] = {
    "85354-9",  # BP panel
    "8480-6",   # systolic
    "8462-4",   # diastolic
    "8867-4",   # heart rate
    "9279-1",   # respiratory rate
    "8310-5",   # body temperature
    "29463-7",  # body weight
    "8302-2",   # body height
    "39156-5",  # BMI
    "2708-6",   # SpO2
    "59408-5",  # SpO2 by pulse oximetry
}

_ALLERGY_NOISE = re.compile(
    r"\b(allergy|allergies|allergic|intolerance|hypersensitivity|to|reaction|of)\b",
    re.IGNORECASE,
)
_NON_WORD = re.compile(r"[^a-z0-9]+")


def system_name(system: Optional[str]) -> Optional[str]:
    """Short display name for a code system URI, or the URI itself."""
    if not system:
        return None
    return SYSTEM_NAMES.get(system, system)


def system_mismatch_warning(coding: Coding) -> Optional[str]:
    """Flag a code whose shape does not match its declared system.

    ``AllergyIntolerance/allergyintolerance-001`` in the sample bundle declares
    SNOMED CT but carries ``7980-2``, which is not a SNOMED concept id -- it has
    the shape of a LOINC code (and 7980 is the RxNorm ingredient code for
    penicillin G). Something upstream crossed wires. We flag it and keep the
    sender's own display text rather than pretending the coding is trustworthy.
    """
    if not coding.system or not coding.code:
        return None
    pattern = CODE_PATTERNS.get(coding.system)
    if pattern is None or pattern[0].match(coding.code):
        return None

    name = system_name(coding.system)
    hint = ""
    for other_system, (other_pattern, _) in CODE_PATTERNS.items():
        if other_system != coding.system and other_pattern.match(coding.code):
            hint = f" It matches the {system_name(other_system)} code format instead."
            break
    return (
        f"Code {coding.code!r} is declared as {name} but does not match that "
        f"system's format ({pattern[1]}).{hint} The coding may be mis-systemed."
    )


def resolve_label(
    coding: Optional[Coding],
) -> tuple[Optional[str], LabelSource]:
    """Best available label for one coding, plus where the label came from."""
    if coding is None:
        return None, LabelSource.ABSENT
    if coding.display and coding.display.strip():
        return coding.display.strip(), LabelSource.SOURCE
    if coding.system and coding.code:
        local = LOCAL_CODE_TABLE.get((coding.system, coding.code))
        if local:
            return local, LabelSource.LOCAL_TABLE
    return None, LabelSource.CODE_ONLY


def pick_coding(concept: Optional[CodeableConcept]) -> Optional[Coding]:
    """Choose the most useful coding from a CodeableConcept.

    Preference order: a coding we can label from the source, then one we can
    label from the curated table, then the first coding present. This keeps the
    UI as informative as the data allows without changing the underlying code.
    """
    if concept is None or not concept.coding:
        return None
    scored: list[tuple[int, int, Coding]] = []
    for index, coding in enumerate(concept.coding):
        _, source = resolve_label(coding)
        rank = {
            LabelSource.SOURCE: 0,
            LabelSource.LOCAL_TABLE: 1,
            LabelSource.CODE_ONLY: 2,
            LabelSource.ABSENT: 3,
        }[source]
        scored.append((rank, index, coding))
    scored.sort(key=lambda item: (item[0], item[1]))
    return scored[0][2]


def concept_key(coding: Optional[Coding], label: Optional[str]) -> Optional[str]:
    """Stable key for deduplication: alias table first, then code, then text."""
    if coding is not None and coding.system and coding.code:
        alias = CONCEPT_ALIASES.get((coding.system, coding.code))
        if alias:
            return alias
        return f"{coding.system}|{coding.code}"
    if label:
        return f"text|{_NON_WORD.sub('-', label.lower()).strip('-')}"
    return None


def allergen_key(coding: Optional[Coding], label: Optional[str]) -> Optional[str]:
    """Dedup key for allergies, tolerant of "Penicillin" vs "Allergy to penicillin".

    Heuristic and documented as such: strip allergy-boilerplate words from the
    label and slugify what remains. Used only to *group* records for review --
    grouping never discards a source record and never lowers a criticality.
    """
    if coding is not None and coding.system and coding.code:
        alias = CONCEPT_ALIASES.get((coding.system, coding.code))
        if alias:
            return alias
    if label:
        stripped = _ALLERGY_NOISE.sub(" ", label.lower())
        slug = _NON_WORD.sub("-", stripped).strip("-")
        if slug:
            return f"allergen:{slug}"
    if coding is not None and coding.system and coding.code:
        return f"{coding.system}|{coding.code}"
    return None


def is_vital_sign(categories: list[CodeableConcept], coding: Optional[Coding]) -> bool:
    for category in categories:
        for cat_coding in category.coding:
            if (cat_coding.code or "").lower() == "vital-signs":
                return True
    return bool(coding and coding.code in VITAL_SIGN_CODES)
