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
            "地區": "locality",
            "巷": "alley",
            "弄": "lane",
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
    needs_suffix = has_number & ~frame["number"].str.contains("號", regex=False)
    frame.loc[needs_suffix, "number"] = frame.loc[needs_suffix, "number"] + "號"
    # 村里與鄰是行政中繼資料，不是門牌字串的一部分。把它們接進地址會讓
    # 鄰別（例如 013）被誤判為門牌號碼，並使精確比對完全失效。
    parts = frame[["street", "locality", "alley", "lane", "number"]].fillna("")
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
    output["match_quality"] = "unmatched"
    output["twd97_x"] = pd.NA
    output["twd97_y"] = pd.NA
    best = doorplates.drop_duplicates(["district", "normalized_address"])
    exact = output.merge(
        best[["district", "normalized_address", "twd97_x", "twd97_y"]].rename(
            columns={"twd97_x": "exact_x", "twd97_y": "exact_y"}
        ),
        on=["district", "normalized_address"],
        how="left",
    )
    has_exact = exact["exact_x"].notna()
    output.loc[has_exact, "twd97_x"] = exact.loc[has_exact, "exact_x"]
    output.loc[has_exact, "twd97_y"] = exact.loc[has_exact, "exact_y"]
    output.loc[has_exact, "match_quality"] = "exact"
    if not has_exact.all():
        remaining = output[~has_exact].copy()
        remaining["_orig_idx"] = remaining.index
        nn = doorplates[doorplates["house_number"].notna()][
            ["district", "road_key", "twd97_x", "twd97_y", "house_number"]
        ].rename(columns={"twd97_x": "nn_x", "twd97_y": "nn_y", "house_number": "nn_house"})
        candidates = remaining.merge(nn, on=["district", "road_key"], how="left")
        candidates["number_gap"] = (candidates["nn_house"] - candidates["house_number"]).abs()
        best_nn = (
            candidates.loc[candidates["house_number"].notna()]
            .sort_values("number_gap")
            .drop_duplicates(subset=["_orig_idx"])
        )
        close = best_nn[best_nn["number_gap"] <= 10]
        if not close.empty:
            for _, row in close.iterrows():
                orig = row["_orig_idx"]
                if orig in output.index:
                    output.at[orig, "twd97_x"] = row["nn_x"]
                    output.at[orig, "twd97_y"] = row["nn_y"]
                    output.at[orig, "match_quality"] = "nearest_number"
        road_idx = output[output["match_quality"] == "unmatched"].index
        if not road_idx.empty:
            road_med = (
                doorplates.groupby(["district", "road_key"])[["twd97_x", "twd97_y"]]
                .median()
                .rename(columns={"twd97_x": "road_x", "twd97_y": "road_y"})
            )
            for idx in road_idx:
                key = (output.at[idx, "district"], output.at[idx, "road_key"])
                if key in road_med.index and pd.notna(road_med.at[key, "road_x"]):
                    output.at[idx, "twd97_x"] = road_med.at[key, "road_x"]
                    output.at[idx, "twd97_y"] = road_med.at[key, "road_y"]
                    output.at[idx, "match_quality"] = "road_only"
    output["coordinate_eligible"] = output["match_quality"].isin(("exact", "nearest_number"))
    return output
