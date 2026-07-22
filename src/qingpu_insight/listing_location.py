"""Assign life circle to listings using WGS84 Haversine distance."""

import numpy as np
import pandas as pd
from pyproj import Transformer


def _haversine_distance(
    lats: np.ndarray,
    lons: np.ndarray,
    station_lats: np.ndarray,
    station_lons: np.ndarray,
) -> np.ndarray:
    lat1 = np.radians(lats[:, np.newaxis])
    lon1 = np.radians(lons[:, np.newaxis])
    lat2 = np.radians(station_lats[np.newaxis, :])
    lon2 = np.radians(station_lons[np.newaxis, :])

    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    c = 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    return 6_371_000 * c


def assign_listing_life_circle(
    listings: pd.DataFrame,
    stations: pd.DataFrame,
    radius_m: float,
) -> pd.DataFrame:
    output = listings.copy()

    required = {"station_code", "twd97_x", "twd97_y"}
    missing = required.difference(stations.columns)
    if missing:
        raise ValueError(f"stations missing required columns: {sorted(missing)}")
    valid_stations = stations.copy()
    valid_stations["twd97_x"] = pd.to_numeric(valid_stations["twd97_x"], errors="coerce")
    valid_stations["twd97_y"] = pd.to_numeric(valid_stations["twd97_y"], errors="coerce")
    valid_stations = valid_stations.loc[
        valid_stations["station_code"].notna()
        & valid_stations["station_code"].astype(str).str.strip().ne("")
        & np.isfinite(valid_stations["twd97_x"])
        & np.isfinite(valid_stations["twd97_y"])
    ].copy()
    if valid_stations.empty:
        raise ValueError("stations must contain at least one valid station")

    transformer = Transformer.from_crs("EPSG:3826", "EPSG:4326", always_xy=True)
    station_lons, station_lats = transformer.transform(
        valid_stations["twd97_x"].to_numpy(dtype=float),
        valid_stations["twd97_y"].to_numpy(dtype=float),
    )
    transformed_valid = (
        np.isfinite(station_lats)
        & np.isfinite(station_lons)
        & (station_lats > 20.0)
        & (station_lats < 30.0)
        & (station_lons > 115.0)
        & (station_lons < 125.0)
    )
    valid_stations = valid_stations.iloc[np.where(transformed_valid)[0]].copy()
    station_lats = station_lats[transformed_valid]
    station_lons = station_lons[transformed_valid]
    if valid_stations.empty:
        raise ValueError("stations must contain at least one valid station")

    lats = pd.to_numeric(output["latitude"], errors="coerce").to_numpy(dtype=float)
    lons = pd.to_numeric(output["longitude"], errors="coerce").to_numpy(dtype=float)
    # Listing evidence is only useful for this service when it is a finite
    # Taiwan coordinate.  This also keeps malformed coordinates from gaining a
    # misleading nearest station.
    has_coords = (
        np.isfinite(lats)
        & np.isfinite(lons)
        & (lats > 20.0)
        & (lats < 30.0)
        & (lons > 115.0)
        & (lons < 125.0)
    )

    station_code_col = pd.Series(pd.NA, index=output.index, dtype="string")
    station_dist_col = pd.Series(np.nan, index=output.index, dtype=float)
    location_eligible = np.zeros(len(output), dtype=bool)
    methods = (
        output["location_method"].fillna("unknown").astype(str).to_numpy()
        if "location_method" in output
        else np.full(len(output), "source_coordinates", dtype=object)
    )
    preserved_reasons = {
        "geocoder_unavailable",
        "address_not_resolved",
        "invalid_geocoder_coordinates",
        "missing_structured_address",
        "detail_address_missing",
    }
    prior_reasons = (
        output["location_reason"].fillna("").astype(str).to_numpy()
        if "location_reason" in output
        else np.full(len(output), "", dtype=object)
    )
    location_reason = np.where(
        (methods == "unknown") & np.isin(prior_reasons, list(preserved_reasons)),
        prior_reasons,
        "missing_coordinates",
    ).astype(object)
    if has_coords.any():
        distances = _haversine_distance(
            lats[has_coords], lons[has_coords],
            station_lats, station_lons,
        )
        nearest_idx = np.argmin(distances, axis=1)
        nearest_dist = distances[np.arange(len(nearest_idx)), nearest_idx]
        within = nearest_dist <= radius_m

        valid_indices = np.where(has_coords)[0]
        # Retain nearest-station evidence even if the listing is outside the
        # service radius; the radius only controls eligibility.
        station_code_col.iloc[valid_indices] = valid_stations.iloc[nearest_idx][
            "station_code"
        ].values
        station_dist_col.iloc[valid_indices] = nearest_dist

        location_reason[valid_indices[~within]] = "outside_service_radius"
        for method, reason in (
            ("source_coordinates", "eligible_source_coordinates"),
            ("structured_address", "eligible_structured_address"),
            ("manual", "eligible_manual"),
        ):
            eligible_indices = valid_indices[within & (methods[valid_indices] == method)]
            location_eligible[eligible_indices] = True
            location_reason[eligible_indices] = reason

        unknown_indices = valid_indices[within & ~np.isin(
            methods[valid_indices], ["source_coordinates", "structured_address", "manual"]
        )]
        location_reason[unknown_indices] = "unknown_location_method"

    output["station_code"] = station_code_col
    output["station_distance_m"] = station_dist_col
    output["location_eligible"] = location_eligible
    output["location_reason"] = location_reason
    return output
