"""Resolve listing addresses while retaining geocoding provenance."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from math import isfinite
from numbers import Real
from typing import Any, Protocol

import pandas as pd
import requests
from pyproj import Transformer

from qingpu_insight.addresses import normalize_address
from qingpu_insight.location_evidence import LocationEvidence, unknown_location


class GeocoderUnavailable(Exception):
    """An adapter could not contact its configured geocoding provider."""


class ListingGeocoder(Protocol):
    @property
    def version(self) -> str: ...

    def resolve(self, normalized_address: str) -> tuple[float, float] | None: ...


class GeocodeCache(Protocol):
    def get(self, normalized_address: str) -> LocationEvidence | None: ...

    def put(self, normalized_address: str, evidence: LocationEvidence) -> None: ...


class DoorplateListingGeocoder:
    """Resolve only unambiguous, exact addresses from official doorplate data."""

    _version = "taoyuan-doorplate-exact-v1"

    def __init__(self, doorplates: pd.DataFrame) -> None:
        address_column = _first_available_column(
            doorplates, ("normalized_address", "address", "地址")
        )
        coordinate_columns = _coordinate_columns(doorplates)
        self._coordinates: dict[str, set[tuple[float, float]]] = {}
        self._ambiguous_addresses: set[str] = set()

        for _, row in doorplates.iterrows():
            raw_address = row[address_column]
            if pd.isna(raw_address):
                continue
            normalized_address = _canonical_address(str(raw_address))
            if not normalized_address:
                continue
            coordinates = _row_coordinates(row, coordinate_columns)
            if coordinates is None:
                self._ambiguous_addresses.add(normalized_address)
                continue
            self._coordinates.setdefault(normalized_address, set()).add(coordinates)

    @property
    def version(self) -> str:
        return self._version

    def resolve(self, normalized_address: str) -> tuple[float, float] | None:
        key = _canonical_address(normalized_address)
        if key in self._ambiguous_addresses:
            return None
        coordinates = self._coordinates.get(key)
        if coordinates is None or len(coordinates) != 1:
            return None
        return next(iter(coordinates))


class MySQLGeocodeCache:
    """A small MySQL cache for successful structured-address resolutions."""

    _schema_sql = """
        CREATE TABLE IF NOT EXISTS geocode_cache (
            normalized_address VARCHAR(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin
                NOT NULL PRIMARY KEY,
            latitude DOUBLE NOT NULL,
            longitude DOUBLE NOT NULL,
            method VARCHAR(32) NOT NULL,
            confidence VARCHAR(16) NOT NULL,
            reason VARCHAR(128) NOT NULL,
            geocoded_at DATETIME(6) NOT NULL,
            geocoder_version VARCHAR(128) NOT NULL,
            updated_at DATETIME(6) NOT NULL
        ) CHARACTER SET utf8mb4
    """
    _select_sql = """
        SELECT latitude, longitude, method, confidence, reason, geocoded_at, geocoder_version
        FROM geocode_cache
        WHERE normalized_address = %(normalized_address)s
    """
    _upsert_sql = """
        INSERT INTO geocode_cache (
            normalized_address, latitude, longitude, method, confidence, reason,
            geocoded_at, geocoder_version, updated_at
        ) VALUES (
            %(normalized_address)s, %(latitude)s, %(longitude)s, %(method)s, %(confidence)s,
            %(reason)s, %(geocoded_at)s, %(geocoder_version)s, %(updated_at)s
        ) ON DUPLICATE KEY UPDATE
            latitude = VALUES(latitude), longitude = VALUES(longitude), method = VALUES(method),
            confidence = VALUES(confidence), reason = VALUES(reason),
            geocoded_at = VALUES(geocoded_at), geocoder_version = VALUES(geocoder_version),
            updated_at = VALUES(updated_at)
    """

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def ensure_schema(self) -> None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(self._schema_sql)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get(self, normalized_address: str) -> LocationEvidence | None:
        if not normalized_address:
            return None
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(self._select_sql, {"normalized_address": normalized_address})
                row = cursor.fetchone()
        finally:
            connection.close()
        return _evidence_from_cache_row(row)

    def put(self, normalized_address: str, evidence: LocationEvidence) -> None:
        if not normalized_address:
            raise ValueError("normalized_address is required")
        if not _is_cacheable_evidence(evidence):
            raise ValueError("only valid resolved location evidence may be cached")

        geocoded_at = _mysql_utc_datetime(evidence.geocoded_at)
        params = {
            "normalized_address": normalized_address,
            "latitude": evidence.latitude,
            "longitude": evidence.longitude,
            "method": evidence.method,
            "confidence": evidence.confidence,
            "reason": evidence.reason,
            "geocoded_at": geocoded_at,
            "geocoder_version": evidence.geocoder_version,
            "updated_at": geocoded_at,
        }
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(self._upsert_sql, params)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


class GeocodingService:
    def __init__(
        self,
        geocoder: ListingGeocoder,
        cache: GeocodeCache,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._geocoder = geocoder
        self._cache = cache
        self._clock = clock or (lambda: datetime.now(UTC))

    def resolve(self, address: str) -> LocationEvidence:
        if not isinstance(address, str) or not address.strip():
            return unknown_location("missing_structured_address")
        key = _canonical_address(address)
        if not key:
            return unknown_location("missing_structured_address")

        cached = self._cache.get(key)
        if cached is not None:
            return cached

        try:
            coordinates = self._geocoder.resolve(key)
        except (GeocoderUnavailable, requests.RequestException, TimeoutError):
            return unknown_location("geocoder_unavailable")
        if coordinates is None:
            return unknown_location("address_not_resolved")
        if not isinstance(coordinates, tuple) or len(coordinates) != 2:
            return unknown_location("invalid_geocoder_coordinates")
        latitude, longitude = coordinates
        if not _is_taiwan_coordinate(latitude, longitude):
            return unknown_location("invalid_geocoder_coordinates")

        geocoded_at = _utc_datetime(self._clock())
        evidence = LocationEvidence(
            latitude=latitude,
            longitude=longitude,
            method="structured_address",
            confidence="medium",
            reason="address_resolved",
            geocoded_at=geocoded_at,
            geocoder_version=self._geocoder.version,
        )
        self._cache.put(key, evidence)
        return evidence

    def enrich(self, record: Mapping[str, object] | object) -> LocationEvidence:
        if not isinstance(record, Mapping):
            return unknown_location("missing_structured_address")
        structured_address = record.get("structured_address")
        if not isinstance(structured_address, str) or not structured_address.strip():
            return unknown_location("missing_structured_address")
        return self.resolve(structured_address)


def _is_taiwan_coordinate(latitude: object, longitude: object) -> bool:
    if (
        not isinstance(latitude, Real)
        or isinstance(latitude, bool)
        or not isinstance(longitude, Real)
        or isinstance(longitude, bool)
    ):
        return False
    latitude_value = float(latitude)
    longitude_value = float(longitude)
    return (
        isfinite(latitude_value)
        and isfinite(longitude_value)
        and 20.0 <= latitude_value <= 27.0
        and 118.0 <= longitude_value <= 123.0
    )


def _canonical_address(address: str) -> str:
    return normalize_address("".join(address.split()))


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _first_available_column(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str:
    for column in candidates:
        if column in frame.columns:
            return column
    choices = ", ".join(candidates)
    raise ValueError(f"doorplate frame needs one of these address columns: {choices}")


def _coordinate_columns(frame: pd.DataFrame) -> tuple[str, str, bool]:
    for latitude, longitude in (("latitude", "longitude"), ("緯度", "經度"), ("lat", "lon")):
        if latitude in frame.columns and longitude in frame.columns:
            return latitude, longitude, False
    if "twd97_x" in frame.columns and "twd97_y" in frame.columns:
        return "twd97_x", "twd97_y", True
    raise ValueError(
        "doorplate frame needs latitude/longitude, 緯度/經度, lat/lon, or twd97_x/twd97_y"
    )


def _row_coordinates(row: pd.Series, columns: tuple[str, str, bool]) -> tuple[float, float] | None:
    first, second, is_twd97 = columns
    try:
        first_value = float(row[first])
        second_value = float(row[second])
    except (TypeError, ValueError):
        return None
    if not isfinite(first_value) or not isfinite(second_value):
        return None
    if is_twd97:
        transformer = Transformer.from_crs("EPSG:3826", "EPSG:4326", always_xy=True)
        longitude, latitude = transformer.transform(first_value, second_value)
    else:
        latitude, longitude = first_value, second_value
    if not _is_taiwan_coordinate(latitude, longitude):
        return None
    return float(latitude), float(longitude)


def _evidence_from_cache_row(row: object) -> LocationEvidence | None:
    if row is None:
        return None
    if isinstance(row, Mapping):
        values = row
    elif isinstance(row, tuple) and len(row) == 7:
        values = dict(
            zip(
                (
                    "latitude",
                    "longitude",
                    "method",
                    "confidence",
                    "reason",
                    "geocoded_at",
                    "geocoder_version",
                ),
                row,
                strict=True,
            )
        )
    else:
        return None

    latitude = values.get("latitude")
    longitude = values.get("longitude")
    method = values.get("method")
    confidence = values.get("confidence")
    reason = values.get("reason")
    geocoded_at = values.get("geocoded_at")
    geocoder_version = values.get("geocoder_version")
    if (
        not _is_taiwan_coordinate(latitude, longitude)
        or method not in {"source_coordinates", "structured_address", "manual"}
        or confidence not in {"high", "medium", "low"}
        or not isinstance(reason, str)
        or not reason
        or not isinstance(geocoded_at, datetime)
        or not isinstance(geocoder_version, str)
        or not geocoder_version
    ):
        return None
    return LocationEvidence(
        float(latitude),
        float(longitude),
        method,
        confidence,
        reason,
        _utc_datetime(geocoded_at),
        geocoder_version,
    )


def _is_cacheable_evidence(evidence: LocationEvidence) -> bool:
    return _evidence_from_cache_row(
        {
            "latitude": evidence.latitude,
            "longitude": evidence.longitude,
            "method": evidence.method,
            "confidence": evidence.confidence,
            "reason": evidence.reason,
            "geocoded_at": evidence.geocoded_at,
            "geocoder_version": evidence.geocoder_version,
        }
    ) is not None


def _mysql_utc_datetime(value: datetime | None) -> datetime:
    if value is None:
        raise ValueError("geocoded_at is required")
    return _utc_datetime(value).replace(tzinfo=None)
