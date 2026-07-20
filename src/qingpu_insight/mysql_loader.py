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

_UPSERT_SQL = f"""INSERT INTO market_transactions ({_columns_sql})
VALUES ({_placeholders})
ON DUPLICATE KEY UPDATE
  station_code=VALUES(station_code),
  transaction_date=VALUES(transaction_date),
  unit_price_per_ping_twd=VALUES(unit_price_per_ping_twd),
  total_price_twd=VALUES(total_price_twd),
  updated_at=CURRENT_TIMESTAMP"""


def load_market_rows(connection: Any, frame: pd.DataFrame, batch_size: int = 1000) -> int:
    clean = frame.where(pd.notna(frame), None)
    total = 0
    for start in range(0, len(clean), batch_size):
        batch = clean.iloc[start : start + batch_size]
        rows = [tuple(row) for row in batch[list(INSERT_COLUMNS)].to_numpy()]
        try:
            with connection.cursor() as cursor:
                cursor.executemany(_UPSERT_SQL, rows)
            connection.commit()
            total += len(rows)
        except Exception:
            connection.rollback()
            raise
    return total
