from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.config import DEFAULT_BUNDLE
from app.fhir.bundle import load_bundle
from app.models.summary import ClinicalSnapshot
from app.normalize.pipeline import build_snapshot

#: Pinned so age/staleness assertions do not rot with the wall clock.
FIXED_NOW = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def bundle():
    return load_bundle(DEFAULT_BUNDLE)


@pytest.fixture(scope="session")
def snapshot(bundle) -> ClinicalSnapshot:
    return build_snapshot(bundle, now=FIXED_NOW)


def find_issue(snapshot: ClinicalSnapshot, *, resource: str, category=None):
    return [
        issue
        for issue in snapshot.data_quality
        if issue.resource == resource and (category is None or issue.category is category)
    ]


def suppressed_resources(snapshot: ClinicalSnapshot) -> set[str]:
    return {item.resource for item in snapshot.suppressed}


def all_displayed_resources(snapshot: ClinicalSnapshot) -> set[str]:
    """Every resource key that appears anywhere in the clinical view."""
    items = [
        *snapshot.problems.active,
        *snapshot.problems.inactive,
        *snapshot.medications.current,
        *snapshot.medications.past,
        *snapshot.allergies.active,
        *snapshot.allergies.inactive,
        *snapshot.encounters,
        *snapshot.observations.vitals,
        *snapshot.observations.labs,
        *snapshot.observations.other,
    ]
    keys: set[str] = set()
    for item in items:
        keys.add(item.provenance.resource)
        keys.update(item.provenance.merged_from)
    return keys
