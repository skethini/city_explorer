"""Runtime configuration sourced from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None
    openai_model: str
    foursquare_api_key: str | None
    google_maps_api_key: str | None
    overpass_url: str
    osrm_url: str
    host: str
    port: int


def get_settings() -> Settings:
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        foursquare_api_key=os.getenv("FOURSQUARE_API_KEY"),
        google_maps_api_key=os.getenv("GOOGLE_MAPS_API_KEY"),
        overpass_url=os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter"),
        osrm_url=os.getenv("OSRM_URL", "https://router.project-osrm.org"),
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
    )


settings = get_settings()
