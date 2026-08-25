"""Unit tests for the pieces that must be right in isolation."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.fhir.bundle import BundleLoadError, parse_bundle
from app.fhir.primitives import (
    AgeEstimate,
    Coding,
    DatePrecision,
    PartialDateTime,
    Quantity,
    Reference,
    format_quantity,
)
from app.fhir.resources import Patient
from app.models.issues import IssueLog
from app.normalize import terminology as tx
from app.normalize.common import normalize_street
from app.normalize.identity import assess_match, resolve_identity
from app.models.summary import Confidence

NOW = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)


class TestPartialDateTime:
    @pytest.mark.parametrize(
        "raw,precision,display",
        [
            ("1958", DatePrecision.YEAR, "1958"),
            ("2019-06", DatePrecision.MONTH, "Jun 2019"),
            ("1958-03-12", DatePrecision.DAY, "12 Mar 1958"),
            ("2025-11-04T14:12:00Z", DatePrecision.INSTANT, "04 Nov 2025 14:12 UTC"),
            # Midnight UTC is a padded date, not a time of day.
            ("2021-06-02T00:00:00Z", DatePrecision.INSTANT, "02 Jun 2021"),
        ],
    )
    def test_precision_and_display(self, raw, precision, display):
        parsed = PartialDateTime.parse(raw)
        assert parsed is not None
        assert parsed.precision is precision
        assert parsed.display == display

    def test_midnight_padding_is_detected(self):
        parsed = PartialDateTime.parse("2015-04-11T00:00:00Z")
        assert parsed.midnight_utc_padded is True
        assert "treated as a date" in parsed.precision_note

    def test_real_time_of_day_is_not_flagged_as_padded(self):
        parsed = PartialDateTime.parse("2025-11-04T14:12:00Z")
        assert parsed.midnight_utc_padded is False
        assert parsed.precision_note is None

    def test_unparseable_date_is_kept_verbatim_not_dropped(self):
        parsed = PartialDateTime.parse("last Tuesday")
        assert parsed.precision is DatePrecision.UNKNOWN
        assert parsed.display == "last Tuesday"
        assert parsed.is_imprecise
        # And it must sort oldest so it can never win a "latest value" contest.
        assert parsed.sort_key < PartialDateTime.parse("1900").sort_key

    def test_offset_is_honoured(self):
        parsed = PartialDateTime.parse("2025-11-04T09:12:00-05:00")
        assert parsed.sort_key == datetime(2025, 11, 4, 14, 12, tzinfo=timezone.utc)

    def test_absent_is_none(self):
        assert PartialDateTime.parse(None) is None
        assert PartialDateTime.parse("  ") is None


class TestAgeEstimate:
    def test_exact_birth_date_gives_exact_age(self):
        age = AgeEstimate.from_birth_date(PartialDateTime.parse("1958-03-12"), NOW)
        assert age.years == 68
        assert age.is_approximate is False
        assert age.display == "68 y"

    def test_year_only_birth_date_gives_a_range_not_a_guess(self):
        """This is the whole point of PartialDateTime.

        Naively parsing "1958" as 1958-01-01 would print "68 y" with false
        confidence. The patient is 68 *or* 67 depending on the unknown month.
        """
        age = AgeEstimate.from_birth_date(PartialDateTime.parse("1958"), NOW)
        assert age.is_approximate is True
        assert (age.low, age.high) == (67, 68)
        assert age.display == "67–68 y"

    def test_missing_birth_date_is_unknown_not_zero(self):
        age = AgeEstimate.from_birth_date(None, NOW)
        assert age.years is None
        assert age.display == "Unknown"


class TestTerminology:
    def test_source_display_wins(self):
        label, source = tx.resolve_label(
            Coding(system=tx.ICD10CM, code="I10", display="Essential hypertension")
        )
        assert (label, source) == ("Essential hypertension", tx.LabelSource.SOURCE)

    def test_curated_table_is_marked_as_local(self):
        label, source = tx.resolve_label(Coding(system=tx.LOINC, code="4548-4"))
        assert source is tx.LabelSource.LOCAL_TABLE
        assert "Hemoglobin A1c" in label

    def test_unknown_code_returns_no_label(self):
        label, source = tx.resolve_label(Coding(system=tx.RXNORM, code="849574"))
        assert label is None
        assert source is tx.LabelSource.CODE_ONLY

    def test_snomed_format_violation_is_detected(self):
        warning = tx.system_mismatch_warning(
            Coding(system=tx.SNOMED, code="7980-2", display="Penicillin")
        )
        assert warning is not None
        assert "LOINC code format" in warning

    def test_valid_codes_produce_no_warning(self):
        assert tx.system_mismatch_warning(Coding(system=tx.SNOMED, code="91936005")) is None
        assert tx.system_mismatch_warning(Coding(system=tx.ICD10CM, code="E11.9")) is None
        assert tx.system_mismatch_warning(Coding(system=tx.LOINC, code="8480-6")) is None

    def test_allergen_keys_collapse_equivalent_wording(self):
        left = tx.allergen_key(None, "Penicillin")
        right = tx.allergen_key(None, "Allergy to penicillin")
        assert left == right == "allergen:penicillin"

    def test_allergen_alias_maps_the_mis_systemed_code(self):
        assert tx.allergen_key(
            Coding(system=tx.SNOMED, code="7980-2"), "Penicillin"
        ) == tx.allergen_key(Coding(system=tx.SNOMED, code="91936005"), None)

    def test_pick_coding_prefers_a_labelled_coding(self):
        from app.fhir.primitives import CodeableConcept

        concept = CodeableConcept(
            coding=[
                Coding(system=tx.RXNORM, code="849574"),
                Coding(system=tx.RXNORM, code="197361", display="Lisinopril 10 MG"),
            ]
        )
        assert tx.pick_coding(concept).code == "197361"


class TestAddressNormalisation:
    def test_street_suffix_abbreviations_are_expanded(self):
        assert normalize_street("482 Larkspur Ln") == normalize_street(
            "482 Larkspur Lane"
        )

    def test_different_streets_stay_different(self):
        assert normalize_street("482 Larkspur Ln") != normalize_street("483 Larkspur Ln")


class TestReference:
    def test_relative_reference(self):
        ref = Reference(reference="Patient/patient-001")
        assert (ref.resource_type, ref.resource_id) == ("Patient", "patient-001")

    def test_absolute_url_is_reduced_to_type_and_id(self):
        ref = Reference(reference="https://ehr.example.org/fhir/Patient/abc")
        assert ref.key == "Patient/abc"

    def test_urn_reference_is_preserved(self):
        assert Reference(reference="urn:uuid:1234").key == "urn:uuid:1234"


class TestQuantityFormatting:
    def test_integral_values_lose_the_decimal_point(self):
        assert format_quantity(Quantity(value=138, unit="mmHg")) == "138 mmHg"

    def test_decimals_are_preserved(self):
        assert format_quantity(Quantity(value=71.2, unit="kg")) == "71.2 kg"

    def test_comparator_is_kept(self):
        assert format_quantity(Quantity(value=5, unit="mg", comparator="<")) == "<5 mg"

    def test_missing_value_is_none(self):
        assert format_quantity(Quantity(unit="kg")) is None


class TestIdentityMatching:
    @staticmethod
    def _patient(**overrides) -> Patient:
        payload = {
            "resourceType": "Patient",
            "id": "p1",
            "name": [{"family": "Whitfield", "given": ["Dorothy"]}],
            "gender": "female",
            "birthDate": "1958-03-12",
            "identifier": [
                {"system": "http://example.org/mrn", "value": "MRN-1"}
            ],
            "address": [{"line": ["482 Larkspur Lane"], "postalCode": "44011"}],
        }
        payload.update(overrides)
        return Patient.model_validate(payload)

    def test_identical_mrn_is_the_only_route_to_certain(self):
        left = self._patient()
        right = self._patient(id="p2")
        assessment = assess_match(left, right)
        assert assessment.exact_identifier is True
        assert assessment.confidence is Confidence.CERTAIN

    def test_high_field_agreement_without_mrn_match_stays_probable(self):
        left = self._patient()
        right = self._patient(
            id="p2", identifier=[{"system": "http://example.org/mrn", "value": "MRN-1-A"}]
        )
        assessment = assess_match(left, right)
        assert assessment.exact_identifier is False
        assert assessment.confidence is Confidence.PROBABLE
        assert "medical record number" in assessment.differed_on

    def test_incompatible_birth_dates_block_the_match_outright(self):
        left = self._patient()
        right = self._patient(id="p2", birthDate="1971-03-12")
        assessment = assess_match(left, right)
        assert assessment.blocking_reason is not None
        assert assessment.confidence is Confidence.UNRESOLVED

    def test_year_only_birth_date_is_compatible_not_conflicting(self):
        left = self._patient()
        right = self._patient(id="p2", birthDate="1958")
        assessment = assess_match(left, right)
        assert assessment.blocking_reason is None
        assert any("compatible" in reason for reason in assessment.matched_on)

    def test_unlinkable_patient_is_reported_not_merged(self):
        log = IssueLog()
        resolved = resolve_identity(
            [self._patient(), self._patient(id="p2", birthDate="1971-03-12")],
            as_of=NOW,
            log=log,
        )
        assert resolved is not None
        assert resolved.resolution.unlinked_resources == ["Patient/p2"]
        assert "Patient/p2" not in resolved.accepted_keys

    def test_more_complete_record_becomes_primary(self):
        sparse = self._patient(id="sparse", birthDate="1958", address=[], telecom=[])
        rich = self._patient(id="rich", telecom=[{"system": "phone", "value": "1"}])
        resolved = resolve_identity([sparse, rich], as_of=NOW, log=IssueLog())
        assert resolved.primary.id == "rich"


class TestBundleLoaderTolerance:
    def test_one_bad_entry_does_not_lose_the_others(self):
        bundle = parse_bundle(
            {
                "resourceType": "Bundle",
                "type": "collection",
                "total": 2,
                "entry": [
                    {"resource": {"resourceType": "Patient", "id": "good"}},
                    {"resource": {"resourceType": "Observation", "id": "bad",
                                  "valueQuantity": {"value": "not-a-number"}}},
                ],
            }
        )
        assert [p.id for p in bundle.patients] == ["good"]
        assert bundle.observations == []
        assert any(i.category.value == "parse_failure" for i in bundle.issues)

    def test_total_mismatch_is_reported(self):
        bundle = parse_bundle(
            {
                "resourceType": "Bundle",
                "type": "collection",
                "total": 99,
                "entry": [{"resource": {"resourceType": "Patient", "id": "a"}}],
            }
        )
        assert any(i.category.value == "bundle_integrity" for i in bundle.issues)

    def test_unsupported_resource_is_noted_not_ignored(self):
        bundle = parse_bundle(
            {
                "resourceType": "Bundle",
                "entry": [
                    {"resource": {"resourceType": "Patient", "id": "a"}},
                    {"resource": {"resourceType": "Procedure", "id": "x"}},
                ],
            }
        )
        assert any(i.category.value == "unsupported_resource" for i in bundle.issues)

    def test_non_bundle_document_is_rejected_loudly(self):
        with pytest.raises(BundleLoadError):
            parse_bundle({"resourceType": "Patient"})

    def test_missing_patient_is_a_critical_finding(self):
        bundle = parse_bundle({"resourceType": "Bundle", "entry": []})
        assert any(i.severity.value == "critical" for i in bundle.issues)
