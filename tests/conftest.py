from pathlib import Path

import pandas as pd
import pytest


@pytest.fixture
def market_frame() -> pd.DataFrame:
    path = Path(__file__).parent / "fixtures" / "market_transactions.csv"
    df = pd.read_csv(path)
    df["transaction_date"] = pd.to_datetime(df["transaction_date"])
    df["completion_date"] = pd.to_datetime(df["completion_date"])
    df["coordinate_eligible"] = df["coordinate_eligible"].astype(bool)
    df["analysis_eligible"] = df["analysis_eligible"].astype(bool)
    return df
