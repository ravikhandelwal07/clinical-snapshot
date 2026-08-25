"""Application settings."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

#: backend/app/config.py -> backend/app -> backend -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BUNDLE = REPO_ROOT / "data" / "scenario1_fhir_bundle.json"


class Settings(BaseModel):
    bundle_path: Path = DEFAULT_BUNDLE
    #: Origins allowed to call the API. The Next.js dev server by default.
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    app_name: str = "Centauri Clinical Snapshot API"
    version: str = "1.0.0"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    bundle_env = os.getenv("FHIR_BUNDLE_PATH")
    origins_env = os.getenv("CORS_ORIGINS")
    settings = Settings()
    if bundle_env:
        settings = settings.model_copy(
            update={"bundle_path": Path(bundle_env).expanduser().resolve()}
        )
    if origins_env:
        settings = settings.model_copy(
            update={
                "cors_origins": [
                    origin.strip() for origin in origins_env.split(",") if origin.strip()
                ]
            }
        )
    return settings
