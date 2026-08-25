"""Pydantic models for the FHIR R4 resources this snapshot consumes.

Field-level notes worth reading before changing anything:

* Every ``status``-like element is typed ``str``, not an ``Enum``. A resource
  carrying an unrecognised status must still parse -- and must then be treated
  as *not safe to display*, which is stricter than dropping it on a validation
  error and losing the audit trail entirely.
* ``subject``/``patient`` stay as ``Reference``. Resolution against the bundle
  happens in the normalization pass, which can then report dangling links.
* Choice elements (``onset[x]``, ``effective[x]``, ``medication[x]``) are
  modelled as the individual named variants FHIR actually uses, because the
  variant chosen by the sender is itself information.
"""

from __future__ import annotations

from typing import Optional

from pydantic import ConfigDict, Field

from app.fhir.primitives import (
    Address,
    CodeableConcept,
    ContactPoint,
    Extension,
    FhirDateTime,
    FhirElement,
    HumanName,
    Identifier,
    Meta,
    Period,
    Quantity,
    Reference,
)

US_CORE_RACE_URL = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-race"
US_CORE_ETHNICITY_URL = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-ethnicity"
US_CORE_BIRTHSEX_URL = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-birthsex"


class DomainResource(FhirElement):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    resource_type: str = Field(alias="resourceType")
    id: Optional[str] = None
    meta: Optional[Meta] = None
    extension: list[Extension] = Field(default_factory=list)

    @property
    def key(self) -> str:
        """Bundle-relative reference key, e.g. ``Condition/condition-001``."""
        return f"{self.resource_type}/{self.id or '(no id)'}"

    def extension_by_url(self, url: str) -> Optional[Extension]:
        return next((e for e in self.extension if e.url == url), None)


class Patient(DomainResource):
    identifier: list[Identifier] = Field(default_factory=list)
    active: Optional[bool] = None
    name: list[HumanName] = Field(default_factory=list)
    telecom: list[ContactPoint] = Field(default_factory=list)
    gender: Optional[str] = None
    birth_date: FhirDateTime = Field(default=None, alias="birthDate")
    deceased_boolean: Optional[bool] = Field(default=None, alias="deceasedBoolean")
    deceased_date_time: FhirDateTime = Field(default=None, alias="deceasedDateTime")
    address: list[Address] = Field(default_factory=list)
    managing_organization: Optional[Reference] = Field(
        default=None, alias="managingOrganization"
    )


class Encounter(DomainResource):
    status: Optional[str] = None
    class_: Optional[CodeableConcept] = Field(default=None, alias="class")
    type: list[CodeableConcept] = Field(default_factory=list)
    subject: Optional[Reference] = None
    participant: list[dict] = Field(default_factory=list)
    period: Optional[Period] = None
    reason_code: list[CodeableConcept] = Field(default_factory=list, alias="reasonCode")
    service_provider: Optional[Reference] = Field(default=None, alias="serviceProvider")


class Condition(DomainResource):
    clinical_status: Optional[CodeableConcept] = Field(
        default=None, alias="clinicalStatus"
    )
    verification_status: Optional[CodeableConcept] = Field(
        default=None, alias="verificationStatus"
    )
    category: list[CodeableConcept] = Field(default_factory=list)
    severity: Optional[CodeableConcept] = None
    code: Optional[CodeableConcept] = None
    subject: Optional[Reference] = None
    encounter: Optional[Reference] = None
    onset_date_time: FhirDateTime = Field(default=None, alias="onsetDateTime")
    onset_string: Optional[str] = Field(default=None, alias="onsetString")
    onset_period: Optional[Period] = Field(default=None, alias="onsetPeriod")
    abatement_date_time: FhirDateTime = Field(default=None, alias="abatementDateTime")
    recorded_date: FhirDateTime = Field(default=None, alias="recordedDate")


class ObservationComponent(FhirElement):
    code: Optional[CodeableConcept] = None
    value_quantity: Optional[Quantity] = Field(default=None, alias="valueQuantity")
    value_string: Optional[str] = Field(default=None, alias="valueString")
    value_codeable_concept: Optional[CodeableConcept] = Field(
        default=None, alias="valueCodeableConcept"
    )
    data_absent_reason: Optional[CodeableConcept] = Field(
        default=None, alias="dataAbsentReason"
    )
    interpretation: list[CodeableConcept] = Field(default_factory=list)


class Observation(DomainResource):
    status: Optional[str] = None
    category: list[CodeableConcept] = Field(default_factory=list)
    code: Optional[CodeableConcept] = None
    subject: Optional[Reference] = None
    encounter: Optional[Reference] = None
    performer: list[Reference] = Field(default_factory=list)
    effective_date_time: FhirDateTime = Field(default=None, alias="effectiveDateTime")
    effective_period: Optional[Period] = Field(default=None, alias="effectivePeriod")
    issued: FhirDateTime = None
    value_quantity: Optional[Quantity] = Field(default=None, alias="valueQuantity")
    value_string: Optional[str] = Field(default=None, alias="valueString")
    value_codeable_concept: Optional[CodeableConcept] = Field(
        default=None, alias="valueCodeableConcept"
    )
    value_boolean: Optional[bool] = Field(default=None, alias="valueBoolean")
    data_absent_reason: Optional[CodeableConcept] = Field(
        default=None, alias="dataAbsentReason"
    )
    interpretation: list[CodeableConcept] = Field(default_factory=list)
    reference_range: list[dict] = Field(default_factory=list, alias="referenceRange")
    component: list[ObservationComponent] = Field(default_factory=list)

    @property
    def effective(self):
        if self.effective_date_time is not None:
            return self.effective_date_time
        if self.effective_period is not None:
            return self.effective_period.start or self.effective_period.end
        return self.issued


class Dosage(FhirElement):
    text: Optional[str] = None
    timing: Optional[dict] = None
    route: Optional[CodeableConcept] = None
    as_needed_boolean: Optional[bool] = Field(default=None, alias="asNeededBoolean")
    dose_and_rate: list[dict] = Field(default_factory=list, alias="doseAndRate")


class MedicationRequest(DomainResource):
    status: Optional[str] = None
    status_reason: Optional[CodeableConcept] = Field(default=None, alias="statusReason")
    intent: Optional[str] = None
    medication_codeable_concept: Optional[CodeableConcept] = Field(
        default=None, alias="medicationCodeableConcept"
    )
    medication_reference: Optional[Reference] = Field(
        default=None, alias="medicationReference"
    )
    subject: Optional[Reference] = None
    encounter: Optional[Reference] = None
    authored_on: FhirDateTime = Field(default=None, alias="authoredOn")
    requester: Optional[Reference] = None
    reason_code: list[CodeableConcept] = Field(default_factory=list, alias="reasonCode")
    dosage_instruction: list[Dosage] = Field(
        default_factory=list, alias="dosageInstruction"
    )


class AllergyReaction(FhirElement):
    manifestation: list[CodeableConcept] = Field(default_factory=list)
    description: Optional[str] = None
    severity: Optional[str] = None
    onset: FhirDateTime = None


class AllergyIntolerance(DomainResource):
    clinical_status: Optional[CodeableConcept] = Field(
        default=None, alias="clinicalStatus"
    )
    verification_status: Optional[CodeableConcept] = Field(
        default=None, alias="verificationStatus"
    )
    type: Optional[str] = None
    category: list[str] = Field(default_factory=list)
    criticality: Optional[str] = None
    code: Optional[CodeableConcept] = None
    patient: Optional[Reference] = None
    encounter: Optional[Reference] = None
    onset_date_time: FhirDateTime = Field(default=None, alias="onsetDateTime")
    recorded_date: FhirDateTime = Field(default=None, alias="recordedDate")
    last_occurrence: FhirDateTime = Field(default=None, alias="lastOccurrence")
    reaction: list[AllergyReaction] = Field(default_factory=list)

    @property
    def subject(self) -> Optional[Reference]:
        """``AllergyIntolerance`` names its subject ``patient``; unify the API."""
        return self.patient


#: Resource types the normalization pass knows how to reason about.
SUPPORTED_RESOURCES: dict[str, type[DomainResource]] = {
    "Patient": Patient,
    "Encounter": Encounter,
    "Condition": Condition,
    "Observation": Observation,
    "MedicationRequest": MedicationRequest,
    "AllergyIntolerance": AllergyIntolerance,
}
