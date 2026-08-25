"""Snapshot loading and caching.

The bundle is a static file, so it is parsed once per process and the normalized
snapshot is cached. ``generated_at`` is refreshed on every request so the UI can
show when it last rendered, while ``as_of`` stays the moment the snapshot was
computed. ``refresh=True`` re-reads from disk, which is what you want while
iterating on the data file.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Optional

from app.config import get_settings
from app.fhir.bundle import BundleLoadError, LoadedBundle, load_bundle
from app.models.summary import ClinicalSnapshot
from app.normalize.pipeline import build_snapshot

_lock = threading.Lock()
_cached_snapshot: Optional[ClinicalSnapshot] = None
_cached_bundle: Optional[LoadedBundle] = None


def get_bundle(refresh: bool = False) -> LoadedBundle:
    global _cached_bundle
    with _lock:
        if _cached_bundle is None or refresh:
            settings = get_settings()
            _cached_bundle = load_bundle(settings.bundle_path)
        return _cached_bundle


def get_snapshot(refresh: bool = False) -> ClinicalSnapshot:
    global _cached_snapshot
    bundle = get_bundle(refresh=refresh)
    with _lock:
        if _cached_snapshot is None or refresh:
            _cached_snapshot = build_snapshot(bundle)
        snapshot = _cached_snapshot.model_copy(
            update={"generated_at": datetime.now(timezone.utc)}
        )
    return snapshot


def reset_cache() -> None:
    """Test helper."""
    global _cached_snapshot, _cached_bundle
    with _lock:
        _cached_snapshot = None
        _cached_bundle = None


__all__ = ["BundleLoadError", "get_bundle", "get_snapshot", "reset_cache"]
