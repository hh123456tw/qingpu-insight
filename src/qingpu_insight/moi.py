import csv
from pathlib import Path
from typing import Literal

import pandas as pd

COLUMN_MAP = {
    "鄉鎮市區": "district",
    "土地位置建物門牌": "address",
    "土地區段位置建物區段門牌": "address",
    "交易年月日": "transaction_date",
    "建物移轉總面積平方公尺": "building_area_sqm",
    "總價元": "total_price_twd",
    "單價元平方公尺": "unit_price_sqm_twd",
    "建物型態": "building_type",
    "建築型態": "building_type",
    "移轉層次": "floor",
    "總樓層數": "total_floors",
    "車位類別": "parking_type",
    "車位移轉總面積(平方公尺)": "parking_area_sqm",
    "車位移轉總面積平方公尺": "parking_area_sqm",
    "車位總價元": "parking_price_twd",
    "編號": "record_id",
}

CANONICAL_COLUMNS = [
    "transaction_type",
    "record_id",
    "district",
    "address",
    "transaction_date",
    "building_area_sqm",
    "total_price_twd",
    "unit_price_sqm_twd",
    "building_type",
    "floor",
    "total_floors",
    "parking_type",
    "parking_area_sqm",
    "parking_price_twd",
    "source_file",
]

NUMERIC_COLUMNS = [
    "building_area_sqm",
    "total_price_twd",
    "unit_price_sqm_twd",
    "parking_area_sqm",
    "parking_price_twd",
]

PROJECT_DISTRICTS = frozenset(("中壢區", "大園區"))


def roc_date_to_timestamp(value: object) -> pd.Timestamp:
    text = str(value).strip().split(".")[0]
    if not text or text.lower() == "nan" or not text.isdigit() or len(text) < 5:
        return pd.NaT
    text = text.zfill(7)
    year = int(text[:-4]) + 1911
    month = int(text[-4:-2])
    day = int(text[-2:])
    try:
        return pd.Timestamp(year=year, month=month, day=day)
    except ValueError:
        return pd.NaT


def read_moi_csv(
    path: Path,
    transaction_type: Literal["resale", "presale"],
) -> pd.DataFrame:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source)
        try:
            header = next(reader)
        except StopIteration as error:
            raise ValueError("empty MOI CSV") from error
        rows: list[list[str]] = []
        for line_number, row in enumerate(reader, start=2):
            if not row or row[0] not in PROJECT_DISTRICTS:
                continue
            if len(row) != len(header):
                raise ValueError(
                    f"malformed in-scope MOI row {line_number}: "
                    f"expected {len(header)} fields, saw {len(row)}"
                )
            rows.append(row)
    frame = pd.DataFrame(rows, columns=header, dtype=str)
    known = {key for key in COLUMN_MAP if key in frame.columns}
    frame = frame[list(known)]
    frame = frame.rename(columns={key: value for key, value in COLUMN_MAP.items() if key in frame})
    required = {"district", "address", "transaction_date", "total_price_twd"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing MOI columns: {sorted(missing)}")
    frame = frame[frame["district"].isin(PROJECT_DISTRICTS)].copy()
    for column in NUMERIC_COLUMNS:
        if column not in frame:
            frame[column] = pd.NA
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    for column in ("record_id", "building_type", "floor", "total_floors", "parking_type"):
        if column not in frame:
            frame[column] = pd.NA
    frame["transaction_date"] = frame["transaction_date"].map(roc_date_to_timestamp)
    frame.insert(0, "transaction_type", transaction_type)
    frame["source_file"] = path.name
    return frame[CANONICAL_COLUMNS].reset_index(drop=True)
