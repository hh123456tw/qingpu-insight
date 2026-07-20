from typing import Any

import pandas as pd

INSERT_COLUMNS: tuple[str, ...] = (
    "transaction_key",
    "transaction_type",
    "record_id",
    "station_code",
    "transaction_date",
    "building_area_sqm",
    "building_area_ping",
    "unit_price_sqm_twd",
    "unit_price_per_ping_twd",
    "total_price_twd",
    "building_type",
    "bedrooms",
    "living_rooms",
    "bathrooms",
    "building_age_years",
    "station_distance_m",
    "longitude",
    "latitude",
    "match_quality",
    "source_file",
)

_placeholders = ", ".join("%s" for _ in INSERT_COLUMNS)
_columns_sql = ", ".join(INSERT_COLUMNS)

_updates_sql = ",\n  ".join(
    f"{column}=VALUES({column})" for column in INSERT_COLUMNS[1:]
)

_UPSERT_SQL = f"""INSERT INTO market_transactions ({_columns_sql})
VALUES ({_placeholders})
ON DUPLICATE KEY UPDATE
  {_updates_sql},
  updated_at=CURRENT_TIMESTAMP"""


def _mysql_value(value: Any) -> Any:
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        return value.item()
    return value


def load_market_rows(connection: Any, frame: pd.DataFrame, batch_size: int = 1000) -> int:
    total = 0
    try:
        for start in range(0, len(frame), batch_size):
            batch = frame.iloc[start : start + batch_size]
            rows = [
                tuple(_mysql_value(value) for value in row)
                for row in batch[list(INSERT_COLUMNS)].itertuples(index=False, name=None)
            ]
            with connection.cursor() as cursor:
                cursor.executemany(_UPSERT_SQL, rows)
            total += len(rows)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return total
