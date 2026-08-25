"""The safety contract. If one of these fails, the snapshot is unsafe to ship.

Each test names the specific harm it prevents rather than just the mechanism,
so a future reader can tell whether a change is a fix or a regression.
"""

from __future__ import annotations

from app.models.issues import IssueSeverity
from app.models.summary import Confidence
from tests.conftest import all_displayed_resources, suppressed_resources


class TestRetractedDataIsNeverClinicalFact:
    def test_voided_critical_creatinine_is_withheld(self, snapshot):
        """observation-004 is a creatinine of 14.7 mg/dL marked entered-in-error.

        Harm prevented: a clinician acting on a dialysis-grade result that the
        source system already voided.
        """
        displayed = all_displayed_resources(snapshot)
        assert "Observation/observation-004" not in displayed

        item = next(
            i for i in snapshot.suppressed if i.resource == "Observation/observation-004"
        )
        assert item.is_noteworthy, "a voided critical result must be surfaced, not buried"
        assert "entered in error" in item.reason.lower()
        # The value must not leak into any displayed observation.
        for observation in [*snapshot.observations.vitals, *snapshot.observations.labs]:
            assert "14.7" not in (observation.value or "")

    def test_erroneous_condition_is_not_shown_even_as_history(self, snapshot):
        """condition-002 is inactive AND entered-in-error.

        Harm prevented: listing asthma under "resolved problems", which asserts
        the patient once had asthma. Retraction outranks inactivity.
        """
        displayed = all_displayed_resources(snapshot)
        assert "Condition/condition-002" not in displayed
        assert "Condition/condition-002" in suppressed_resources(snapshot)
        assert not any(
            "asthma" in problem.concept.text.lower()
            for problem in [*snapshot.problems.active, *snapshot.problems.inactive]
        )

    def test_erroneous_encounter_is_not_a_visit(self, snapshot):
        assert "Encounter/encounter-002" not in {
            encounter.provenance.resource for encounter in snapshot.encounters
        }
        assert "Encounter/encounter-002" in suppressed_resources(snapshot)

    def test_erroneous_allergy_is_withheld(self, snapshot):
        """allergyintolerance-002 (latex) is resolved + entered-in-error."""
        assert "AllergyIntolerance/allergyintolerance-002" in suppressed_resources(
            snapshot
        )
        assert not any(
            "latex" in allergy.concept.text.lower()
            for allergy in [*snapshot.allergies.active, *snapshot.allergies.inactive]
        )

    def test_nothing_is_silently_dropped(self, snapshot):
        """Every clinical resource is either displayed or explicitly withheld."""
        accounted = all_displayed_resources(snapshot) | suppressed_resources(snapshot)
        # allergyintolerance-003 is merged into -001, so it appears via merged_from.
        expected = {
            "Encounter/encounter-001",
            "Encounter/encounter-002",
            "Condition/condition-001",
            "Condition/condition-002",
            "Condition/condition-003",
            "Observation/observation-001",
            "Observation/observation-002",
            "Observation/observation-003",
            "Observation/observation-004",
            "MedicationRequest/medicationrequest-001",
            "MedicationRequest/medicationrequest-002",
            "MedicationRequest/medicationrequest-003",
            "AllergyIntolerance/allergyintolerance-001",
            "AllergyIntolerance/allergyintolerance-002",
            "AllergyIntolerance/allergyintolerance-003",
        }
        assert expected <= accounted


class TestNotCurrentIsNotErroneous:
    def test_stopped_metformin_is_past_not_current(self, snapshot):
        """Harm prevented: re-prescribing a drug that was deliberately stopped."""
        current = {m.concept.text.lower() for m in snapshot.medications.current}
        past = {m.concept.text.lower() for m in snapshot.medications.past}
        assert not any("metformin" in name for name in current)
        assert any("metformin" in name for name in past)

    def test_active_lisinopril_is_current(self, snapshot):
        assert any(
            "lisinopril" in m.concept.text.lower() for m in snapshot.medications.current
        )


class TestIdentityResolution:
    def test_two_patient_records_are_linked_but_not_certain(self, snapshot):
        identity = snapshot.identity
        assert identity.primary_resource == "Patient/patient-001"
        assert identity.linked_resources == ["Patient/patient-002"]
        assert identity.confidence is Confidence.PROBABLE, (
            "differing MRNs must never produce a CERTAIN match"
        )
        assert identity.requires_review

    def test_active_med_on_the_linked_record_is_included_and_flagged(self, snapshot):
        """medicationrequest-003 is an active order on patient-002.

        Harm prevented, both directions: dropping it hides an active drug;
        merging it silently hides that its inclusion rests on an assumption.
        """
        medication = next(
            m
            for m in snapshot.medications.current
            if m.provenance.resource == "MedicationRequest/medicationrequest-003"
        )
        assert medication.provenance.via_linked_identity is True
        assert any("probable" in note.lower() for note in medication.notes)

    def test_birth_date_conflict_is_surfaced_not_averaged(self, snapshot):
        conflict = next(
            c for c in snapshot.identity.conflicts if c.field == "Birth date"
        )
        assert {v.value for v in conflict.values} == {"1958-03-12", "1958"}
        assert conflict.chosen == "1958-03-12"

    def test_mrn_conflict_is_surfaced(self, snapshot):
        assert snapshot.demographics.mrn == "MRN-48213"
        assert any("MRN-48213-A" in other for other in snapshot.demographics.other_identifiers)

    def test_both_phone_numbers_are_kept(self, snapshot):
        """Different `use` values are not a conflict -- the patient has two phones."""
        phones = " ".join(snapshot.demographics.phones)
        assert "555-014-2231" in phones and "555-014-9987" in phones


class TestPhiMinimisation:
    def test_ssn_is_not_returned(self, snapshot):
        payload = snapshot.model_dump_json()
        assert "4471" not in payload, "SSN digits must not reach the client"
        assert "US Social Security Number" in snapshot.demographics.withheld_identifier_systems


class TestUncertaintyIsVisible:
    def test_unlabelled_rxnorm_code_is_not_given_an_invented_name(self, snapshot):
        """RxNorm 849574 has no display and is not in the curated table."""
        medication = next(
            m
            for m in snapshot.medications.current
            if m.provenance.resource == "MedicationRequest/medicationrequest-003"
        )
        assert medication.concept.label_is_unresolved
        assert medication.concept.text == "RxNorm 849574"

    def test_locally_resolved_label_is_marked_as_such(self, snapshot):
        """ICD-10 E11.9 has no display; the label comes from our curated table."""
        problem = next(
            p
            for p in snapshot.problems.active
            if p.provenance.resource == "Condition/condition-003"
        )
        assert problem.concept.text == "Type 2 diabetes mellitus without complications"
        assert problem.concept.label_source.value == "local_table"
        assert not problem.concept.label_is_from_source

    def test_year_only_dates_keep_their_precision(self, snapshot):
        problem = next(
            p
            for p in snapshot.problems.active
            if p.provenance.resource == "Condition/condition-003"
        )
        assert problem.onset is not None
        assert problem.onset.display == "2019"
        assert problem.onset.is_imprecise
        assert "Year only" in (problem.onset.precision_note or "")

    def test_age_uses_the_more_precise_of_the_two_birth_dates(self, snapshot):
        """1958-03-12 beats 1958, so the age is exact.

        The year-only case is covered in test_units.py, where it correctly
        yields a range rather than a falsely confident single number.
        """
        assert snapshot.demographics.age.display == "68 y"
        assert snapshot.demographics.age.is_approximate is False

    def test_dangling_encounter_reference_is_reported(self, snapshot):
        problem = next(
            p
            for p in snapshot.problems.active
            if p.provenance.resource == "Condition/condition-003"
        )
        assert any("encounter-099" in note for note in problem.notes)

    def test_dangling_performer_reference_is_reported(self, snapshot):
        weight = next(
            o
            for o in snapshot.observations.vitals
            if o.provenance.resource == "Observation/observation-003"
        )
        assert any("practitioner-999" in note for note in weight.notes)

    def test_mis_systemed_code_is_flagged(self, snapshot):
        """SNOMED CT does not have a concept id of "7980-2"."""
        allergy = snapshot.allergies.active[0]
        assert any("mis-systemed" in warning for warning in allergy.concept.warnings)

    def test_absence_of_allergy_records_is_never_read_as_none(self, snapshot):
        assert snapshot.allergies.no_known_allergies_asserted is False


class TestAllergyMerge:
    def test_duplicate_penicillin_records_merge_without_lowering_criticality(
        self, snapshot
    ):
        """confirmed/high + unconfirmed/unable-to-assess -> one row, still high."""
        penicillin = [
            a for a in snapshot.allergies.active if "penicillin" in a.concept.text.lower()
        ]
        assert len(penicillin) == 1, "the same allergen must not appear twice"
        allergy = penicillin[0]
        assert allergy.criticality == "high"
        assert allergy.criticality_rank == 3
        assert "AllergyIntolerance/allergyintolerance-003" in allergy.provenance.merged_from
        assert any("merged" in note.lower() for note in allergy.notes)

    def test_allergies_sort_most_critical_first(self, snapshot):
        ranks = [a.criticality_rank for a in snapshot.allergies.active]
        assert ranks == sorted(ranks, reverse=True)


class TestObservations:
    def test_blood_pressure_renders_as_a_pair(self, snapshot):
        bp = next(
            o
            for o in snapshot.observations.vitals
            if o.provenance.resource == "Observation/observation-001"
        )
        assert bp.value == "138/88 mmHg"
        assert len(bp.components) == 2

    def test_no_clinical_interpretation_is_invented(self, snapshot):
        """We deliberately do not label values high/low. See README."""
        payload = snapshot.observations.model_dump_json().lower()
        for word in ("hypertensive", "abnormal", "elevated", "critical high"):
            assert word not in payload

    def test_six_year_old_hba1c_is_marked_historical(self, snapshot):
        hba1c = next(
            o
            for o in snapshot.observations.labs
            if o.provenance.resource == "Observation/observation-002"
        )
        assert hba1c.is_historical
        assert hba1c.value == "6.1 %"
        assert hba1c.age_text is not None and "years ago" in hba1c.age_text
        assert hba1c.concept.label_source.value == "local_table"

    def test_units_are_never_converted(self, snapshot):
        weight = next(
            o
            for o in snapshot.observations.vitals
            if o.provenance.resource == "Observation/observation-003"
        )
        assert weight.value == "71.2 kg"


class TestDataQualityLedger:
    def test_every_suppression_has_a_matching_issue(self, snapshot):
        reported = {issue.resource for issue in snapshot.data_quality}
        for item in snapshot.suppressed:
            assert item.resource in reported

    def test_critical_findings_exist_and_are_ordered_first(self, snapshot):
        severities = [issue.severity for issue in snapshot.data_quality]
        assert IssueSeverity.CRITICAL in severities
        assert severities == sorted(
            severities,
            key=lambda s: {"critical": 0, "warning": 1, "info": 2}[s.value],
        )

    def test_coverage_counts_match_the_sections(self, snapshot):
        assert snapshot.coverage["observations"].displayed == 3
        assert snapshot.coverage["observations"].suppressed == 1
        assert snapshot.coverage["problems"].displayed == 2
        assert snapshot.coverage["problems"].suppressed == 1
        assert snapshot.coverage["medications"].displayed == 3
        assert snapshot.coverage["allergies"].displayed == 1
        assert snapshot.coverage["allergies"].suppressed == 1

    def test_us_core_extensions_are_read(self, snapshot):
        assert snapshot.demographics.race is not None
        assert snapshot.demographics.race.text == "White"
        assert snapshot.demographics.ethnicity is not None
        assert snapshot.demographics.ethnicity.text == "Not Hispanic or Latino"
        assert any("us-core-patient" in p for p in snapshot.demographics.us_core_profiles)
