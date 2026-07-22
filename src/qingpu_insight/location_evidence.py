"""Location provenance shared by listing normalization and geocoding."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

LocationMethod = Literal["source_coordinates", "structured_address", "manual", "unknown"]
LocationConfidence = Literal["high", "medium", "low", "unknown"]


@dataclass(frozen=True)
class LocationEvidence:
    latitude: float | None
    longitude: float | None
    method: LocationMethod
    confidence: LocationConfidence
    reason: str
    geocoded_at: datetime | None
    geocoder_version: str | None


def unknown_location(reason: str) -> LocationEvidence:
    return LocationEvidence(None, None, "unknown", "unknown", reason, None, None)
