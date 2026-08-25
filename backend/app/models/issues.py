"""The data-quality ledger.

Every time the normalization pass drops, merges, downgrades or guesses at
something, it appends an issue here. The endpoint returns the ledger alongside
the snapshot and the UI renders it, so "we silently fixed it" is never a
possible outcome: the clinician can always see what the pipeline did to the
source data and why.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class IssueSeverity(str, Enum):
    #: Worth recording, does not change what is displayed.
    INFO = "info"
    #: The display is degraded or qualified; a reader needs to know.
    WARNING = "warning"
    #: Something was withheld from, or flagged in, the clinical view because
    #: presenting it as fact could mislead care.
    CRITICAL = "critical"


class IssueCategory(str, Enum):
    PARSE_FAILURE = "parse_failure"
    UNSUPPORTED_RESOURCE = "unsupported_resource"
    BUNDLE_INTEGRITY = "bundle_integrity"
    IDENTITY = "identity"
    SUPPRESSED_STATUS = "suppressed_status"
    UNRESOLVED_CODE = "unresolved_code"
    CODE_SYSTEM_MISMATCH = "code_system_mismatch"
    DANGLING_REFERENCE = "dangling_reference"
    DATE_PRECISION = "date_precision"
    DUPLICATE = "duplicate"
    STALE_DATA = "stale_data"
    PHI_MINIMIZATION = "phi_minimization"
    MISSING_DATA = "missing_data"
    ORPHANED_RESOURCE = "orphaned_resource"


class DataIssue(BaseModel):
    """One machine-readable note about how the source data was handled."""

    severity: IssueSeverity
    category: IssueCategory
    message: str
    #: Bundle-relative reference, e.g. ``Observation/observation-004``.
    resource: Optional[str] = None
    #: FHIR element path the issue concerns, e.g. ``code.coding[0].system``.
    field: Optional[str] = None
    #: What the pipeline did about it, in the clinician's language.
    action: Optional[str] = None


class IssueLog:
    """Tiny mutable collector. Not a Pydantic model on purpose -- it is plumbing."""

    def __init__(self) -> None:
        self._issues: list[DataIssue] = []

    def add(
        self,
        severity: IssueSeverity,
        category: IssueCategory,
        message: str,
        *,
        resource: Optional[str] = None,
        field: Optional[str] = None,
        action: Optional[str] = None,
    ) -> DataIssue:
        issue = DataIssue(
            severity=severity,
            category=category,
            message=message,
            resource=resource,
            field=field,
            action=action,
        )
        self._issues.append(issue)
        return issue

    def info(self, category: IssueCategory, message: str, **kw) -> DataIssue:
        return self.add(IssueSeverity.INFO, category, message, **kw)

    def warning(self, category: IssueCategory, message: str, **kw) -> DataIssue:
        return self.add(IssueSeverity.WARNING, category, message, **kw)

    def critical(self, category: IssueCategory, message: str, **kw) -> DataIssue:
        return self.add(IssueSeverity.CRITICAL, category, message, **kw)

    def extend(self, issues: list[DataIssue]) -> None:
        self._issues.extend(issues)

    @property
    def issues(self) -> list[DataIssue]:
        """Most severe first, then grouped by category for a stable render."""
        order = {
            IssueSeverity.CRITICAL: 0,
            IssueSeverity.WARNING: 1,
            IssueSeverity.INFO: 2,
        }
        return sorted(
            self._issues, key=lambda i: (order[i.severity], i.category.value, i.message)
        )

    def counts_by_severity(self) -> dict[str, int]:
        counts = {s.value: 0 for s in IssueSeverity}
        for issue in self._issues:
            counts[issue.severity.value] += 1
        return counts


class IssueSummary(BaseModel):
    total: int
    by_severity: dict[str, int] = Field(default_factory=dict)
