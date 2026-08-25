"""Shared helpers used by every section builder."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from app.fhir.bundle import LoadedBundle
from app.fhir.primitives import CodeableConcept, PartialDateTime, Reference
from app.fhir.resources import DomainResource
from app.models.issues import IssueCategory, IssueLog
from app.models.summary import CodedConcept, Provenance
from app.normalize import terminology as tx
from app.normalize.terminology import LabelSource

_WS = re.compile(r"\s+")

#: Street-suffix abbreviations, expanded before comparing two addresses.
_STREET_ABBREVIATIONS = {
    "st": "street", "str": "street",
    "rd": "road",
    "ln": "lane",
    "ave": "avenue", "av": "avenue",
    "dr": "drive",
    "blvd": "boulevard",
    "ct": "court",
    "pl": "place",
    "cir": "circle",
    "hwy": "highway",
    "pkwy": "parkway",
    "ter": "terrace",
    "apt": "apartment",
    "ste": "suite",
    "n": "north", "s": "south", "e": "east", "w": "west",
    "ne": "northeast", "nw": "northwest", "se": "southeast", "sw": "southwest",
}


def squash(text: Optional[str]) -> str:
    return _WS.sub(" ", (text or "").strip())


def normalize_token_text(text: Optional[str]) -> str:
    """Lowercase, punctuation-stripped form used for comparisons only."""
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())
    return squash(cleaned)


def normalize_street(text: Optional[str]) -> str:
    """``482 Larkspur Ln`` and ``482 Larkspur Lane`` compare equal."""
    tokens = normalize_token_text(text).split()
    return " ".join(_STREET_ABBREVIATIONS.get(token, token) for token in tokens)


def build_concept(
    concept: Optional[CodeableConcept],
    *,
    log: IssueLog,
    resource_key: str,
    field: str,
    what: str,
) -> CodedConcept:
    """Turn a CodeableConcept into a display-ready concept with provenance.

    ``what`` is the human noun used in issue messages ("problem", "medication")
    so the data-quality panel reads as prose rather than field paths.
    """
    coding = tx.pick_coding(concept)
    label, source = tx.resolve_label(coding)
    warnings: list[str] = []

    if concept is not None and concept.text and not label:
        label, source = squash(concept.text), LabelSource.SOURCE

    if coding is not None:
        if mismatch := tx.system_mismatch_warning(coding):
            warnings.append(mismatch)
            log.warning(
                IssueCategory.CODE_SYSTEM_MISMATCH,
                mismatch,
                resource=resource_key,
                field=f"{field}.coding[0]",
                action="Coding shown as recorded; treat the code system as unverified.",
            )

    system_label = tx.system_name(coding.system if coding else None)
    code_value = coding.code if coding else None

    if source is LabelSource.LOCAL_TABLE:
        log.info(
            IssueCategory.UNRESOLVED_CODE,
            f"{what.capitalize()} coding {system_label} {code_value} had no display "
            f"text; label supplied from the application's curated code table.",
            resource=resource_key,
            field=f"{field}.coding[0].display",
            action=f"Displayed as “{label}” and marked as a locally resolved label.",
        )
    elif source in (LabelSource.CODE_ONLY, LabelSource.ABSENT):
        if code_value:
            log.warning(
                IssueCategory.UNRESOLVED_CODE,
                f"{what.capitalize()} coding {system_label} {code_value} has no "
                "display text and is not in the curated code table.",
                resource=resource_key,
                field=f"{field}.coding[0].display",
                action="Shown as an unlabelled code. No label was inferred.",
            )
        else:
            log.warning(
                IssueCategory.MISSING_DATA,
                f"{what.capitalize()} has no code and no text.",
                resource=resource_key,
                field=field,
                action="Shown as “Not recorded”.",
            )

    if label:
        text = label
    elif code_value:
        text = f"{system_label or 'Unknown system'} {code_value}"
    else:
        text = "Not recorded"

    return CodedConcept(
        text=text,
        code=code_value,
        system_uri=coding.system if coding else None,
        system_name=system_label,
        label_source=source,
        warnings=warnings,
    )


def build_provenance(
    resource: DomainResource,
    *,
    subject: Optional[Reference],
    via_linked_identity: bool,
) -> Provenance:
    return Provenance(
        resource=resource.key,
        subject_reference=subject.key if subject else None,
        via_linked_identity=via_linked_identity,
    )


def check_reference(
    reference: Optional[Reference],
    *,
    bundle: LoadedBundle,
    log: IssueLog,
    resource_key: str,
    field: str,
    what: str,
) -> Optional[str]:
    """Report a reference that points outside the bundle. Returns a UI note."""
    if reference is None or not reference.key:
        return None
    if bundle.has_reference(reference.key):
        return None
    message = (
        f"{resource_key} references {reference.key} ({what}), which is not present "
        "in the bundle."
    )
    log.warning(
        IssueCategory.DANGLING_REFERENCE,
        message,
        resource=resource_key,
        field=field,
        action="Link shown as unresolved; no detail could be retrieved.",
    )
    return f"Linked {what} {reference.key} is not in this extract."


def note_imprecise_date(
    value: Optional[PartialDateTime],
    *,
    log: IssueLog,
    resource_key: str,
    field: str,
    what: str,
) -> Optional[str]:
    """Log and describe a date whose precision is below day level."""
    if value is None or not value.is_imprecise:
        return None
    log.info(
        IssueCategory.DATE_PRECISION,
        f"{what.capitalize()} on {resource_key} is recorded as {value.raw!r} "
        f"({value.precision.value} precision).",
        resource=resource_key,
        field=field,
        action="Displayed at the precision recorded; no day or month was assumed.",
    )
    return value.precision_note


def describe_age(value: Optional[PartialDateTime], as_of: datetime) -> Optional[str]:
    """"about 6 years ago" style caption for an aged result."""
    if value is None or value.sort_key.year < 1900:
        return None
    days = (as_of - value.sort_key).days
    if days < 0:
        return "dated in the future relative to this snapshot"
    if days < 45:
        return f"{days} days ago"
    months = round(days / 30.44)
    if days < 365:
        return f"about {months} months ago"
    years = days / 365.25
    if years < 1.75:
        return "about a year ago"
    return f"about {round(years)} years ago"
