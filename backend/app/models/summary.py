"""The shape of ``GET /api/patient-summary``.

Design intent: the frontend should need **zero clinical knowledge**. Every
decision -- is this current? is this label trustworthy? is this date precise? --
is already made here and carried as explicit fields. The UI's job is to render
flags, not to re-derive them. That keeps the safety logic in one tested place
instead of duplicated across two languages.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, computed_field

from app.fhir.primitives import AgeEstimate, PartialDateTime
from app.models.issues import DataIssue
from app.normalize.terminology import LabelSource


class Confidence(str, Enum):
    CERTAIN = "certain"
    PROBABLE = "probable"
    POSSIBLE = "possible"
    UNRESOLVED = "unresolved"


class CodedConcept(BaseModel):
    """A code rendered for humans, carrying the provenance of its label."""

    #: What to print. Never a label we invented.
    text: str
    code: Optional[str] = None
    system_uri: Optional[str] = None
    system_name: Optional[str] = None
    label_source: LabelSource
    #: Coding-level problems (e.g. code/system format mismatch).
    warnings: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def label_is_from_source(self) -> bool:
        return self.label_source is LabelSource.SOURCE

    @computed_field  # type: ignore[prop-decorator]
    @property
    def label_is_unresolved(self) -> bool:
        return self.label_source in (LabelSource.CODE_ONLY, LabelSource.ABSENT)


class Provenance(BaseModel):
    """Where a displayed item came from, so it can be traced back."""

    resource: str
    #: The Patient resource the source record pointed at.
    subject_reference: Optional[str] = None
    #: True when the record hung off a *different* Patient resource that we
    #: linked to this one probabilistically rather than with certainty.
    via_linked_identity: bool = False
    #: Other resources folded into this item during deduplication.
    merged_from: list[str] = Field(default_factory=list)


class StatusBadge(BaseModel):
    """A status as the source recorded it, plus whether it reads as current."""

    code: Optional[str] = None
    label: Optional[str] = None
    #: False when the status means "do not treat as current clinical fact".
    is_current: bool = True
    tone: str = "neutral"  # neutral | positive | caution | danger


class SuppressedItem(BaseModel):
    """Something deliberately withheld from the clinical view.

    Suppressed data is returned, not deleted. "This bundle contained an
    erroneous creatinine of 14.7 mg/dL" is itself clinically relevant --
    somebody needs to know the record is dirty -- but it must never appear
    beside real results where it could be read as a value.
    """

    resource: str
    section: str
    label: str
    reason: str
    status: Optional[str] = None
    recorded: Optional[PartialDateTime] = None
    #: Set when withholding the item is more consequential than usual, e.g. a
    #: grossly abnormal lab value voided by the source system.
    is_noteworthy: bool = False


class FieldConflict(BaseModel):
    """One demographic field two Patient records disagreed about."""

    field: str
    values: list["ConflictValue"] = Field(default_factory=list)
    chosen: Optional[str] = None
    rationale: str


class ConflictValue(BaseModel):
    value: str
    source: str


FieldConflict.model_rebuild()


class IdentityResolution(BaseModel):
    """How many Patient resources there were and what we did about it."""

    primary_resource: Optional[str] = None
    linked_resources: list[str] = Field(default_factory=list)
    unlinked_resources: list[str] = Field(default_factory=list)
    confidence: Confidence = Confidence.CERTAIN
    score: float = 1.0
    matched_on: list[str] = Field(default_factory=list)
    differed_on: list[str] = Field(default_factory=list)
    conflicts: list[FieldConflict] = Field(default_factory=list)
    #: Plain-language sentence for the banner at the top of the snapshot.
    narrative: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def requires_review(self) -> bool:
        return self.confidence is not Confidence.CERTAIN or bool(self.unlinked_resources)


class Demographics(BaseModel):
    full_name: str
    family_name: Optional[str] = None
    given_names: list[str] = Field(default_factory=list)
    name_note: Optional[str] = None
    gender: Optional[str] = None
    birth_date: Optional[PartialDateTime] = None
    age: AgeEstimate
    deceased: Optional[bool] = None
    mrn: Optional[str] = None
    other_identifiers: list[str] = Field(default_factory=list)
    #: Identifiers intentionally not returned to the client (e.g. SSN).
    withheld_identifier_systems: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    address: Optional[str] = None
    alternate_addresses: list[str] = Field(default_factory=list)
    race: Optional[CodedConcept] = None
    ethnicity: Optional[CodedConcept] = None
    us_core_profiles: list[str] = Field(default_factory=list)


class Problem(BaseModel):
    concept: CodedConcept
    clinical_status: StatusBadge
    verification_status: StatusBadge
    onset: Optional[PartialDateTime] = None
    recorded: Optional[PartialDateTime] = None
    provenance: Provenance
    notes: list[str] = Field(default_factory=list)


class Medication(BaseModel):
    concept: CodedConcept
    status: StatusBadge
    intent: Optional[str] = None
    dosage_text: Optional[str] = None
    authored_on: Optional[PartialDateTime] = None
    provenance: Provenance
    notes: list[str] = Field(default_factory=list)


class Allergy(BaseModel):
    concept: CodedConcept
    clinical_status: StatusBadge
    verification_status: StatusBadge
    criticality: Optional[str] = None
    criticality_label: str
    #: 3 = high, 2 = low, 1 = unable-to-assess, 0 = not stated. Sort key only.
    criticality_rank: int = 0
    reactions: list[str] = Field(default_factory=list)
    recorded: Optional[PartialDateTime] = None
    provenance: Provenance
    notes: list[str] = Field(default_factory=list)


class EncounterSummary(BaseModel):
    type: Optional[CodedConcept] = None
    encounter_class: Optional[str] = None
    status: StatusBadge
    start: Optional[PartialDateTime] = None
    end: Optional[PartialDateTime] = None
    duration_minutes: Optional[int] = None
    provenance: Provenance
    notes: list[str] = Field(default_factory=list)


class ObservationValue(BaseModel):
    label: Optional[str] = None
    text: str
    code: Optional[str] = None
    label_source: Optional[LabelSource] = None


class ObservationSummary(BaseModel):
    concept: CodedConcept
    value: Optional[str] = None
    components: list[ObservationValue] = Field(default_factory=list)
    unit: Optional[str] = None
    effective: Optional[PartialDateTime] = None
    status: StatusBadge
    is_vital_sign: bool = False
    #: Older than the recency window; shown but explicitly dated.
    is_historical: bool = False
    age_text: Optional[str] = None
    provenance: Provenance
    notes: list[str] = Field(default_factory=list)


class ProblemSection(BaseModel):
    active: list[Problem] = Field(default_factory=list)
    inactive: list[Problem] = Field(default_factory=list)


class MedicationSection(BaseModel):
    current: list[Medication] = Field(default_factory=list)
    past: list[Medication] = Field(default_factory=list)


class ObservationSection(BaseModel):
    vitals: list[ObservationSummary] = Field(default_factory=list)
    labs: list[ObservationSummary] = Field(default_factory=list)
    other: list[ObservationSummary] = Field(default_factory=list)


class AllergySection(BaseModel):
    active: list[Allergy] = Field(default_factory=list)
    #: Resolved/inactive but not erroneous -- history, not current risk.
    inactive: list[Allergy] = Field(default_factory=list)
    #: True only when the source *asserts* no known allergies. Absence of
    #: records is NOT such an assertion, and the UI must not conflate them.
    no_known_allergies_asserted: bool = False


class SourceInfo(BaseModel):
    bundle_id: Optional[str] = None
    bundle_type: Optional[str] = None
    bundle_timestamp: Optional[PartialDateTime] = None
    declared_total: Optional[int] = None
    entry_count: int = 0
    resource_counts: dict[str, int] = Field(default_factory=dict)
    #: Data after this point is simply not in the extract.
    currency_note: Optional[str] = None


class SectionCoverage(BaseModel):
    """Per-section counts so the UI can say "2 shown, 1 withheld"."""

    displayed: int = 0
    suppressed: int = 0
    qualified: int = 0  # displayed but carrying a caveat


class ClinicalSnapshot(BaseModel):
    generated_at: datetime
    as_of: datetime
    source: SourceInfo
    identity: IdentityResolution
    demographics: Demographics
    allergies: AllergySection
    problems: ProblemSection
    medications: MedicationSection
    encounters: list[EncounterSummary] = Field(default_factory=list)
    observations: ObservationSection
    suppressed: list[SuppressedItem] = Field(default_factory=list)
    data_quality: list[DataIssue] = Field(default_factory=list)
    coverage: dict[str, SectionCoverage] = Field(default_factory=dict)
