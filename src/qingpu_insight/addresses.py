import re
from pathlib import Path

import pandas as pd

FULL_WIDTH = str.maketrans("０１２３４５６７８９", "0123456789")
SECTION_DIGITS = str.maketrans("123456789", "一二三四五六七八九")


def normalize_address(value: str) -> str:
    text = str(value).strip().translate(FULL_WIDTH).replace("臺", "台")
    text = re.sub(r"^桃園市(?:中壢區|大園區)", "", text)
    text = re.sub(
        r"(?P<road>[^巷弄號]+[路街])(?P<section>[1-9])段",
        lambda match: match.group("road") + match.group("section").translate(SECTION_DIGITS) + "段",
        text,
    )
    return re.sub(r"\s+", "", text)


def _road_key(address: str) -> str:
    match = re.search(r"^(.+?(?:路|道|街)(?:[一二三四五六七八九]段)?)", address)
    return match.group(1) if match else ""


def _house_number(address: str) -> int | None:
    match = re.search(r"(\d+)號", address)
    return int(match.group(1)) if match else None


def build_doorplate_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    frame = frame.rename(
        columns={
            "鄉鎮市區代碼": "district_code",
            "街路段": "street",
            "村里": "area",
            "鄰": "lane",
            "巷": "alley",
            "號": "number",
            "橫座標": "twd97_x",
            "縱座標": "twd97_y",
        }
    )
    district_map = {"6800200": "中壢區", "6800600": "大園區"}
    frame["district"] = frame["district_code"].map(district_map)
    frame = frame[frame["district"].notna()].copy()
    frame["number"] = frame["number"].fillna("").astype(str)
    has_number = frame["number"].ne("")
    needs_suffix = has_number & ~frame["number"].str.endswith("號")
    frame.loc[needs_suffix, "number"] = frame.loc[needs_suffix, "number"] + "號"
    parts = frame[["street", "area", "lane", "alley", "number"]].fillna("")
    frame["normalized_address"] = parts.agg("".join, axis=1).map(normalize_address)
    frame["road_key"] = frame["normalized_address"].map(_road_key)
    frame["house_number"] = frame["normalized_address"].map(_house_number)
    frame["twd97_x"] = pd.to_numeric(frame["twd97_x"], errors="coerce")
    frame["twd97_y"] = pd.to_numeric(frame["twd97_y"], errors="coerce")
    return frame.dropna(subset=["twd97_x", "twd97_y"])[
        ["district", "normalized_address", "road_key", "house_number", "twd97_x", "twd97_y"]
    ]


def match_addresses(transactions: pd.DataFrame, doorplates: pd.DataFrame) -> pd.DataFrame:
    output = transactions.copy()
    output["normalized_address"] = output["address"].map(normalize_address)
    output["road_key"] = output["normalized_address"].map(_road_key)
    output["house_number"] = output["normalized_address"].map(_house_number)
    exact_lookup = doorplates.drop_duplicates(["district", "normalized_address"]).set_index(
        ["district", "normalized_address"]
    )
    rows: list[dict[str, object]] = []
    for row in output.itertuples(index=False):
        key = (row.district, row.normalized_address)
        if key in exact_lookup.index:
            match = exact_lookup.loc[key]
            rows.append(
                {"twd97_x": match.twd97_x, "twd97_y": match.twd97_y, "match_quality": "exact"}
            )
            continue
        candidates = doorplates[
            (doorplates["district"] == row.district)
            & (doorplates["road_key"] == row.road_key)
            & doorplates["house_number"].notna()
        ].copy()
        if row.house_number is not None and not candidates.empty:
            candidates["number_gap"] = (candidates["house_number"] - row.house_number).abs()
            match = candidates.sort_values("number_gap").iloc[0]
            if match["number_gap"] <= 10:
                rows.append(
                    {
                        "twd97_x": match.twd97_x,
                        "twd97_y": match.twd97_y,
                        "match_quality": "nearest_number",
                    }
                )
                continue
        if not candidates.empty:
            rows.append(
                {
                    "twd97_x": candidates["twd97_x"].median(),
                    "twd97_y": candidates["twd97_y"].median(),
                    "match_quality": "road_only",
                }
            )
        else:
            rows.append({"twd97_x": pd.NA, "twd97_y": pd.NA, "match_quality": "unmatched"})
    matches = pd.DataFrame(rows, index=output.index)
    output[["twd97_x", "twd97_y", "match_quality"]] = matches
    output["coordinate_eligible"] = output["match_quality"].isin(("exact", "nearest_number"))
    return output
