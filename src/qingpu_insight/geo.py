import math

import numpy as np
import pandas as pd
from pyproj import Transformer

from qingpu_insight.addresses import match_addresses
from qingpu_insight.config import Station

_WGS84_TO_TWD97 = Transformer.from_crs("EPSG:4326", "EPSG:3826", always_xy=True)
_TWD97_TO_WGS84 = Transformer.from_crs("EPSG:3826", "EPSG:4326", always_xy=True)

# Station platforms as (latitude, longitude). Station features of a single property
# are computed from these, so every valuation path derives them the same way.
STATION_WGS84: dict[str, tuple[float, float]] = {
    "A17": (25.0223, 121.2373),
    "A18": (25.0137, 121.2143),
    "A19": (25.0011, 121.2046),
}


def wgs84_to_twd97(longitude: float, latitude: float) -> tuple[float, float]:
    if not all(math.isfinite(value) for value in (longitude, latitude)):
        raise ValueError("coordinates must be finite")
    x, y = _WGS84_TO_TWD97.transform(longitude, latitude)
    if not all(math.isfinite(value) for value in (x, y)):
        raise ValueError("coordinates must be finite")
    return float(x), float(y)


def twd97_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """Return (longitude, latitude) for a TWD97 / TM2 point."""
    if not all(math.isfinite(value) for value in (x, y)):
        raise ValueError("coordinates must be finite")
    longitude, latitude = _TWD97_TO_WGS84.transform(x, y)
    if not all(math.isfinite(value) for value in (longitude, latitude)):
        raise ValueError("coordinates must be finite")
    return float(longitude), float(latitude)


def station_from_coords(longitude: float, latitude: float) -> tuple[str, float]:
    """Nearest MRT station and its Haversine distance in metres."""
    best_station: str | None = None
    best_distance = float("inf")
    for code, (slat, slon) in STATION_WGS84.items():
        dlat = math.radians(latitude - slat)
        dlon = math.radians(longitude - slon)
        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(math.radians(latitude)) * math.cos(math.radians(slat))
            * math.sin(dlon / 2) ** 2
        )
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        dist = 6_371_000 * c
        if dist < best_distance:
            best_distance = dist
            best_station = code
    if best_station is None:
        raise ValueError("no station within range")
    return best_station, round(best_distance, 1)


def station_points(stations: tuple[Station, ...], doorplates: pd.DataFrame) -> pd.DataFrame:
    source = pd.DataFrame(
        {
            "station_code": [station.code for station in stations],
            "station_name": [station.name for station in stations],
            "district": ["大園區", "中壢區", "中壢區"],
            "address": [station.official_address for station in stations],
        }
    )
    located = match_addresses(source, doorplates)
    station_x = pd.to_numeric(located["twd97_x"], errors="coerce")
    station_y = pd.to_numeric(located["twd97_y"], errors="coerce")
    finite_coordinates = pd.Series(
        np.isfinite(station_x.to_numpy(dtype=float))
        & np.isfinite(station_y.to_numpy(dtype=float)),
        index=located.index,
    )
    assignable = (
        located["match_quality"].eq("exact")
        & finite_coordinates
    )
    if not assignable.all():
        invalid = located.loc[~assignable, "station_code"].tolist()
        raise ValueError(
            f"station addresses require exact official doorplate matches: {invalid}"
        )
    return located[["station_code", "station_name", "twd97_x", "twd97_y"]]


def assign_life_circle(
    transactions: pd.DataFrame,
    stations: pd.DataFrame,
    radius_m: float,
) -> pd.DataFrame:
    output = transactions.copy()
    station_xy = stations[["twd97_x", "twd97_y"]].to_numpy(dtype=float)
    point_xy = output[["twd97_x", "twd97_y"]].apply(pd.to_numeric, errors="coerce").to_numpy()
    distances = np.sqrt(((point_xy[:, None, :] - station_xy[None, :, :]) ** 2).sum(axis=2))
    distances[~output["coordinate_eligible"].to_numpy(), :] = np.nan
    has_distance = ~np.isnan(distances).all(axis=1)
    nearest_index = np.zeros(len(output), dtype=int)
    nearest_index[has_distance] = np.nanargmin(distances[has_distance], axis=1)
    nearest_distance = np.full(len(output), np.nan)
    nearest_distance[has_distance] = distances[has_distance, nearest_index[has_distance]]
    within = has_distance & (nearest_distance <= radius_m)
    output["station_code"] = pd.Series(pd.NA, index=output.index, dtype="string")
    output.loc[within, "station_code"] = stations.iloc[nearest_index[within]][
        "station_code"
    ].to_numpy()
    output["station_distance_m"] = nearest_distance
    transformer = Transformer.from_crs("EPSG:3826", "EPSG:4326", always_xy=True)
    valid = has_distance
    longitude = np.full(len(output), np.nan)
    latitude = np.full(len(output), np.nan)
    longitude[valid], latitude[valid] = transformer.transform(
        point_xy[valid, 0], point_xy[valid, 1]
    )
    output["longitude"] = longitude
    output["latitude"] = latitude
    return output
