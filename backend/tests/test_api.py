from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_reports_the_bundle_loaded():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["bundle_loaded"] is True


def test_patient_summary_shape():
    response = client.get("/api/patient-summary")
    assert response.status_code == 200
    body = response.json()

    for key in (
        "demographics",
        "identity",
        "allergies",
        "problems",
        "medications",
        "encounters",
        "observations",
        "suppressed",
        "data_quality",
        "coverage",
        "source",
    ):
        assert key in body, f"missing top-level key {key}"

    assert body["demographics"]["full_name"] == "Dorothy M Whitfield"
    assert body["identity"]["confidence"] == "probable"
    assert body["source"]["entry_count"] == 17
    assert body["source"]["declared_total"] == 17


def test_data_quality_can_be_filtered_by_severity():
    response = client.get("/api/data-quality", params={"severity": "critical"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] > 0
    assert all(issue["severity"] == "critical" for issue in body["issues"])
    assert body["by_severity"]["critical"] >= body["total"]


def test_withheld_endpoint_lists_the_voided_creatinine():
    response = client.get("/api/withheld")
    assert response.status_code == 200
    resources = {item["resource"] for item in response.json()["items"]}
    assert "Observation/observation-004" in resources


def test_computed_display_fields_are_serialised():
    """The UI relies on these; they must survive JSON serialisation."""
    body = client.get("/api/patient-summary").json()
    onset = next(
        problem["onset"]
        for problem in body["problems"]["active"]
        if problem["provenance"]["resource"] == "Condition/condition-003"
    )
    assert onset["display"] == "2019"
    assert onset["is_imprecise"] is True
    assert onset["precision_note"].startswith("Year only")
