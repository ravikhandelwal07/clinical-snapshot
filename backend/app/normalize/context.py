"""Shared state threaded through the section builders."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.fhir.bundle import LoadedBundle
from app.fhir.primitives import PartialDateTime, Reference
from app.fhir.resources import DomainResource
from app.models.issues import IssueCategory, IssueLog
from app.models.summary import SuppressedItem
from app.normalize.identity import ResolvedIdentity

#: Observations older than this are still shown, but explicitly dated as history
#: so a six-year-old HbA1c is never mistaken for a current one.
HISTORICAL_AFTER_DAYS = 365


@dataclass
class NormalizationContext:
    bundle: LoadedBundle
    identity: Optional[ResolvedIdentity]
    log: IssueLog
    as_of: datetime
    suppressed: list[SuppressedItem] = field(default_factory=list)

    # ------------------------------------------------------------- subject gate
    def subject_status(self, subject: Optional[Reference]) -> tuple[str, Optional[str]]:
        """Classify a resource's subject: ``primary``, ``linked``, ``foreign``, ``unknown``.

        The second element is a UI note when the classification matters.
        """
        if self.identity is None:
            return "unknown", "No Patient record to attribute this to."
        key = subject.key if subject else None
        if key is None:
            return "unknown", "Source record does not say which patient it belongs to."
        if key == self.identity.primary.key:
            return "primary", None
        if key in self.identity.linked_keys:
            return (
                "linked",
                f"Sourced from {key}, a separate Patient record linked to this "
                "patient by a probable — not certain — identity match.",
            )
        if key in {p.key for p in self.identity.unlinked}:
            return "foreign", f"Belongs to {key}, which could not be linked to this patient."
        return "unknown", f"Subject {key} is not a Patient record in this bundle."

    # ------------------------------------------------------------- suppression
    def suppress(
        self,
        resource: DomainResource,
        *,
        section: str,
        label: str,
        reason: str,
        status: Optional[str] = None,
        recorded: Optional[PartialDateTime] = None,
        noteworthy: bool = False,
        severity: str = "warning",
        category: IssueCategory = IssueCategory.SUPPRESSED_STATUS,
    ) -> None:
        """Withhold an item from the clinical view, on the record."""
        self.suppressed.append(
            SuppressedItem(
                resource=resource.key,
                section=section,
                label=label,
                reason=reason,
                status=status,
                recorded=recorded,
                is_noteworthy=noteworthy,
            )
        )
        emit = self.log.critical if severity == "critical" else self.log.warning
        emit(
            category,
            f"{resource.key} ({label}) was not presented as current clinical fact: "
            f"{reason}",
            resource=resource.key,
            action=f"Listed under withheld records in the {section} section.",
        )
