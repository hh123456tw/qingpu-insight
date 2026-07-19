from pathlib import Path

import pandas as pd

from qingpu_insight.moi import read_moi_csv, roc_date_to_timestamp

FIXTURES = Path(__file__).parent / "fixtures"


def test_roc_date_conversion() -> None:
    assert roc_date_to_timestamp("1150615") == pd.Timestamp("2026-06-15")
    assert pd.isna(roc_date_to_timestamp(""))


def test_resale_parser_removes_metadata_and_other_districts() -> None:
    frame = read_moi_csv(FIXTURES / "moi_resale.csv", "resale")

    assert frame["district"].tolist() == ["中壢區"]
    assert frame["transaction_type"].tolist() == ["resale"]
    assert frame.loc[0, "total_price_twd"] == 20_000_000
    assert frame.loc[0, "transaction_date"] == pd.Timestamp("2026-06-15")


def test_presale_parser_keeps_type_separate() -> None:
    frame = read_moi_csv(FIXTURES / "moi_presale.csv", "presale")

    assert frame["district"].tolist() == ["大園區"]
    assert frame["transaction_type"].tolist() == ["presale"]
    assert frame.loc[0, "parking_price_twd"] == 1_800_000
