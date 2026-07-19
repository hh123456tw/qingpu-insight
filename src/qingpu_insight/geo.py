import numpy as np
import pandas as pd
from pyproj import Transformer

from qingpu_insight.addresses import match_addresses
from qingpu_insight.config import Station


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
    assignable = located["twd97_x"].notna()
    if not assignable.all():
        missing = located.loc[~assignable, "station_code"].tolist()
        raise ValueError(f"station addresses could not be located at all: {missing}")
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
