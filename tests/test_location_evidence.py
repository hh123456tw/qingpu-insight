"""Tests for the listing location evidence contract."""

from qingpu_insight.location_evidence import LocationEvidence, unknown_location


def test_unknown_location_has_explicit_reason() -> None:
    value = unknown_location("missing_coordinates_and_address")

    assert value == LocationEvidence(
        latitude=None,
        longitude=None,
        method="unknown",
        confidence="unknown",
        reason="missing_coordinates_and_address",
        geocoded_at=None,
        geocoder_version=None,
    )
