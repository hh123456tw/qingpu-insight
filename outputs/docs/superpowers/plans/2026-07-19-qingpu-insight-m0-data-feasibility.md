# Qingpu Insight M0 Data Feasibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible command-line pipeline that downloads official Taoyuan real-estate data, normalizes resale and presale transactions, locates records around A17–A19, and publishes a go/no-go feasibility report.

**Architecture:** A small Python package separates source acquisition, parsing, address matching, geospatial assignment, and reporting. Raw files are immutable and checksum-tracked; generated Parquet and Markdown outputs can be rebuilt from the raw manifest. M0 uses files and Parquet rather than MySQL because its purpose is to prove data feasibility before the application schema is locked.

**Tech Stack:** Python 3.12, pandas 2.2+, PyArrow, Requests, PyProj, pytest, responses, Ruff, Git

## Global Constraints

- Limit transactions to Taoyuan City records in 中壢區 and 大園區.
- Keep resale and presale data separate from ingestion through reporting.
- Assign a transaction to A17, A18, or A19 only when its eligible coordinate is within 2,000 metres of the nearest station.
- Preserve downloaded source files unchanged and store SHA-256, source URL, download time, and byte size.
- Use only official sources in M0: Ministry of the Interior, Taoyuan Metro, and Taoyuan City open data.
- Do not scrape property-listing websites.
- Do not commit raw downloads, generated Parquet, local reports, secrets, virtual environments, or `.superpowers/`.
- Treat doorplate coordinates as reference data with possible errors; every match must carry a quality label.
- Use Traditional Chinese for user-facing report text and English snake_case for Python identifiers and stored column names.
- M0 passes only when both transaction types have at least 500 assigned records overall, each station/type cell has at least 50 records, eligible coordinate coverage is at least 60%, and each type has at least 100 assigned records from the latest 24 months present in the data.

## Plan Boundaries

This plan implements M0 only. After its report is accepted, create separate implementation plans for M1 market analysis, M2 valuation models, M3 web application, and M4 deployment/LLM capabilities. Do not introduce Flask, MySQL, frontend code, model training, listing-site ingestion, or cloud infrastructure during M0.

## Target File Map

```text
pyproject.toml                         Package metadata and dependencies
.gitignore                             Repository hygiene
README.md                              M0 setup and commands
src/qingpu_insight/__init__.py         Package version
src/qingpu_insight/config.py           Paths, sources, stations, thresholds
src/qingpu_insight/downloads.py        Official downloads and checksums
src/qingpu_insight/archives.py         Safe ZIP extraction
src/qingpu_insight/moi.py              MOI CSV parsing and normalization
src/qingpu_insight/addresses.py        Address normalization and matching
src/qingpu_insight/geo.py              Coordinate conversion and station assignment
src/qingpu_insight/feasibility.py      Metrics and decision logic
src/qingpu_insight/reporting.py        Markdown/CSV report rendering
src/qingpu_insight/cli.py              Command-line orchestration
tests/fixtures/                         Small synthetic source samples
tests/test_config.py                    Configuration tests
tests/test_downloads.py                 HTTP and manifest tests
tests/test_archives.py                  ZIP safety tests
tests/test_moi.py                       CSV normalization tests
tests/test_addresses.py                 Address matching tests
tests/test_geo.py                       Distance and assignment tests
tests/test_feasibility.py               Threshold and report tests
tests/test_cli.py                       Offline end-to-end test
```

---

### Task 1: Package Foundation and Immutable Configuration

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/qingpu_insight/__init__.py`
- Create: `src/qingpu_insight/config.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Produces: `SourceConfig`, `Station`, `Thresholds`, `Settings`, and `get_settings(root: Path) -> Settings`.
- Consumes: no project code.

- [ ] **Step 1: Write the failing configuration tests**

```python
# tests/test_config.py
from pathlib import Path

from qingpu_insight.config import get_settings


def test_settings_use_project_relative_paths(tmp_path: Path) -> None:
    settings = get_settings(tmp_path)

    assert settings.raw_dir == tmp_path / "data" / "raw"
    assert settings.processed_dir == tmp_path / "data" / "processed"
    assert settings.report_dir == tmp_path / "outputs" / "reports"


def test_settings_lock_scope_and_thresholds(tmp_path: Path) -> None:
    settings = get_settings(tmp_path)

    assert settings.districts == ("中壢區", "大園區")
    assert [station.code for station in settings.stations] == ["A17", "A18", "A19"]
    assert settings.radius_m == 2_000.0
    assert settings.thresholds.minimum_total_by_type == 500
    assert settings.thresholds.minimum_station_type_cell == 50
    assert settings.thresholds.minimum_coordinate_coverage == 0.60
    assert settings.thresholds.minimum_recent_by_type == 100
```

- [ ] **Step 2: Run the tests and confirm the package is absent**

Run: `python -m pytest tests/test_config.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'qingpu_insight'`.

- [ ] **Step 3: Add package metadata and dependencies**

```toml
# pyproject.toml
[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"

[project]
name = "qingpu-insight"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "pandas>=2.2,<3",
  "pyarrow>=18,<22",
  "pyproj>=3.7,<4",
  "requests>=2.32,<3",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.3,<10",
  "responses>=0.25,<1",
  "ruff>=0.9,<1",
]

[project.scripts]
qingpu-data = "qingpu_insight.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

```gitignore
# .gitignore
.venv/
__pycache__/
.pytest_cache/
.ruff_cache/
*.py[cod]
.env
.superpowers/
data/raw/
data/processed/
outputs/reports/
*.parquet
```

- [ ] **Step 4: Add immutable configuration objects**

```python
# src/qingpu_insight/__init__.py
__version__ = "0.1.0"
```

```python
# src/qingpu_insight/config.py
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceConfig:
    moi_base_url: str
    doorplate_url: str


@dataclass(frozen=True)
class Station:
    code: str
    name: str
    official_address: str


@dataclass(frozen=True)
class Thresholds:
    minimum_total_by_type: int = 500
    minimum_station_type_cell: int = 50
    minimum_coordinate_coverage: float = 0.60
    minimum_recent_by_type: int = 100


@dataclass(frozen=True)
class Settings:
    root: Path
    raw_dir: Path
    processed_dir: Path
    report_dir: Path
    districts: tuple[str, ...]
    stations: tuple[Station, ...]
    radius_m: float
    sources: SourceConfig
    thresholds: Thresholds


def get_settings(root: Path) -> Settings:
    root = root.resolve()
    return Settings(
        root=root,
        raw_dir=root / "data" / "raw",
        processed_dir=root / "data" / "processed",
        report_dir=root / "outputs" / "reports",
        districts=("中壢區", "大園區"),
        stations=(
            Station("A17", "領航站", "桃園市大園區領航北路四段351號"),
            Station("A18", "高鐵桃園站", "桃園市中壢區高鐵北路一段5號"),
            Station("A19", "桃園體育園區站", "桃園市中壢區高鐵南路二段350號"),
        ),
        radius_m=2_000.0,
        sources=SourceConfig(
            moi_base_url="https://plvr.land.moi.gov.tw",
            doorplate_url=(
                "https://opendata.tycg.gov.tw/api/dataset/"
                "ec47dbd5-9ed8-4c8d-8ce1-ccb63b1b72e6/resource/"
                "4ee7723b-84dc-41c3-865e-6ea3f7bb02a9/download"
            ),
        ),
        thresholds=Thresholds(),
    )
```

- [ ] **Step 5: Install, verify, lint, and commit**

Run:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest tests/test_config.py -v
.\.venv\Scripts\python -m ruff check src tests
```

Expected: two tests pass and Ruff reports `All checks passed!`.

Commit:

```powershell
git add pyproject.toml .gitignore src/qingpu_insight/__init__.py src/qingpu_insight/config.py tests/test_config.py
git commit -m "chore: establish M0 data package"
```

---

### Task 2: Official Download Client and Safe Archive Extraction

**Files:**
- Create: `src/qingpu_insight/downloads.py`
- Create: `src/qingpu_insight/archives.py`
- Create: `tests/test_downloads.py`
- Create: `tests/test_archives.py`

**Interfaces:**
- Consumes: `SourceConfig` from Task 1.
- Produces: `DownloadRecord`, `download_file(url, destination, session=None) -> DownloadRecord`, `download_season(base_url, season, destination, session=None) -> DownloadRecord`, `download_current_table(base_url, table_name, destination, session=None) -> DownloadRecord`, `write_manifest(records, path) -> None`, and `extract_taoyuan_tables(archive, destination) -> tuple[Path, ...]`.

- [ ] **Step 1: Write failing HTTP and archive tests**

```python
# tests/test_downloads.py
import hashlib
import json
from pathlib import Path

import responses

from qingpu_insight.downloads import DownloadRecord, download_file, download_season, write_manifest


@responses.activate
def test_download_file_streams_bytes_and_records_sha256(tmp_path: Path) -> None:
    url = "https://example.test/source.csv"
    body = b"a,b\n1,2\n"
    responses.get(url, body=body, status=200)

    record = download_file(url, tmp_path / "source.csv")

    assert record.path.read_bytes() == body
    assert record.sha256 == hashlib.sha256(body).hexdigest()
    assert record.byte_size == len(body)
    assert record.source_url == url


@responses.activate
def test_download_season_uses_official_history_endpoint(tmp_path: Path) -> None:
    url = "https://plvr.land.moi.gov.tw/DownloadHistory?type=season&fileName=115S2"
    responses.get(url, body=b"PK-test", status=200)

    record = download_season(
        "https://plvr.land.moi.gov.tw", "115S2", tmp_path / "115S2.zip"
    )

    assert record.source_url == url
    assert record.path.name == "115S2.zip"


def test_write_manifest_is_stable_json(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_bytes(b"x")
    record = DownloadRecord(
        source_url="https://example.test/source.csv",
        path=source,
        sha256=hashlib.sha256(b"x").hexdigest(),
        byte_size=1,
        downloaded_at="2026-07-19T00:00:00+00:00",
    )

    write_manifest([record], tmp_path / "manifest.json")
    payload = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))

    assert payload[0]["path"] == str(source)
    assert payload[0]["byte_size"] == 1
```

```python
# tests/test_archives.py
from pathlib import Path
from zipfile import ZipFile

import pytest

from qingpu_insight.archives import extract_taoyuan_tables


def test_extract_taoyuan_tables_keeps_resale_and_presale(tmp_path: Path) -> None:
    archive = tmp_path / "season.zip"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("H_lvr_land_A.csv", "resale")
        bundle.writestr("H_lvr_land_B.csv", "presale")
        bundle.writestr("A_lvr_land_A.csv", "taipei")

    paths = extract_taoyuan_tables(archive, tmp_path / "out")

    assert [path.name for path in paths] == ["h_lvr_land_a.csv", "h_lvr_land_b.csv"]


def test_extract_rejects_zip_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("../H_lvr_land_A.csv", "unsafe")

    with pytest.raises(ValueError, match="unsafe archive member"):
        extract_taoyuan_tables(archive, tmp_path / "out")
```

- [ ] **Step 2: Run the tests and verify missing modules**

Run: `.\.venv\Scripts\python -m pytest tests/test_downloads.py tests/test_archives.py -v`

Expected: collection fails because `qingpu_insight.downloads` and `qingpu_insight.archives` do not exist.

- [ ] **Step 3: Implement checksum-tracked downloads**

```python
# src/qingpu_insight/downloads.py
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Iterable

import requests


@dataclass(frozen=True)
class DownloadRecord:
    source_url: str
    path: Path
    sha256: str
    byte_size: int
    downloaded_at: str


def download_file(
    url: str,
    destination: Path,
    session: requests.Session | None = None,
) -> DownloadRecord:
    client = session or requests.Session()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    byte_size = 0
    with client.get(url, stream=True, timeout=(10, 120)) as response:
        response.raise_for_status()
        with temporary.open("wb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                output.write(chunk)
                digest.update(chunk)
                byte_size += len(chunk)
    temporary.replace(destination)
    return DownloadRecord(
        source_url=url,
        path=destination,
        sha256=digest.hexdigest(),
        byte_size=byte_size,
        downloaded_at=datetime.now(UTC).isoformat(),
    )


def download_season(
    base_url: str,
    season: str,
    destination: Path,
    session: requests.Session | None = None,
) -> DownloadRecord:
    url = f"{base_url}/DownloadHistory?type=season&fileName={season}"
    return download_file(url, destination, session)


def download_current_table(
    base_url: str,
    table_name: str,
    destination: Path,
    session: requests.Session | None = None,
) -> DownloadRecord:
    allowed = {"h_lvr_land_a.csv", "h_lvr_land_b.csv"}
    if table_name.lower() not in allowed:
        raise ValueError(f"unsupported current table: {table_name}")
    url = f"{base_url}/Download?fileName={table_name.lower()}"
    return download_file(url, destination, session)


def write_manifest(records: Iterable[DownloadRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = []
    for record in records:
        item = asdict(record)
        item["path"] = str(record.path)
        payload.append(item)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
```

- [ ] **Step 4: Implement safe Taoyuan extraction**

```python
# src/qingpu_insight/archives.py
from pathlib import Path, PurePosixPath
from zipfile import ZipFile


TARGET_NAMES = {"h_lvr_land_a.csv", "h_lvr_land_b.csv"}


def extract_taoyuan_tables(archive: Path, destination: Path) -> tuple[Path, ...]:
    destination.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    with ZipFile(archive) as bundle:
        for member in bundle.infolist():
            relative = PurePosixPath(member.filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"unsafe archive member: {member.filename}")
            name = relative.name.lower()
            if name not in TARGET_NAMES:
                continue
            output = destination / name
            output.write_bytes(bundle.read(member))
            extracted.append(output)
    return tuple(sorted(extracted, key=lambda path: path.name))
```

- [ ] **Step 5: Run tests, lint, and commit**

Run:

```powershell
.\.venv\Scripts\python -m pytest tests/test_downloads.py tests/test_archives.py -v
.\.venv\Scripts\python -m ruff check src tests
```

Expected: five tests pass and Ruff reports no errors.

Commit:

```powershell
git add src/qingpu_insight/downloads.py src/qingpu_insight/archives.py tests/test_downloads.py tests/test_archives.py
git commit -m "feat: download and extract official MOI data"
```

---

### Task 3: Normalize MOI Resale and Presale Tables

**Files:**
- Create: `src/qingpu_insight/moi.py`
- Create: `tests/fixtures/moi_resale.csv`
- Create: `tests/fixtures/moi_presale.csv`
- Create: `tests/test_moi.py`

**Interfaces:**
- Consumes: extracted `h_lvr_land_a.csv` and `h_lvr_land_b.csv` paths from Task 2.
- Produces: `roc_date_to_timestamp(value: object) -> pd.Timestamp`, `read_moi_csv(path: Path, transaction_type: Literal["resale", "presale"]) -> pd.DataFrame`, and the canonical columns `transaction_type`, `record_id`, `district`, `address`, `transaction_date`, `building_area_sqm`, `total_price_twd`, `unit_price_sqm_twd`, `building_type`, `floor`, `total_floors`, `parking_type`, `parking_area_sqm`, `parking_price_twd`, `source_file`.

- [ ] **Step 1: Add small source fixtures and failing parser tests**

```csv
鄉鎮市區,土地位置建物門牌,交易年月日,建物移轉總面積平方公尺,總價元,單價元平方公尺,建物型態,移轉層次,總樓層數,車位類別,車位移轉總面積(平方公尺),車位總價元,編號
The villages and towns urban district,land sector position building sector house number,transaction year month and day,building shifting total area,Total price NTD,the unit price,building state,shifting level,total floor number,the berth category,berth shifting total area,berth total price NTD,serial number
中壢區,高鐵北路一段5號,1150615,40.0,20000000,500000,住宅大樓(11層含以上有電梯),八層,十五層,坡道平面,12.0,2000000,H-001
桃園區,中正路1號,1150615,30.0,9000000,300000,華廈(10層含以下有電梯),五層,十層,,,0,H-002
```

```csv
鄉鎮市區,土地位置建物門牌,交易年月日,建物移轉總面積平方公尺,總價元,單價元平方公尺,建物型態,移轉層次,總樓層數,車位類別,車位移轉總面積(平方公尺),車位總價元,編號
The villages and towns urban district,land sector position building sector house number,transaction year month and day,building shifting total area,Total price NTD,the unit price,building state,shifting level,total floor number,the berth category,berth shifting total area,berth total price NTD,serial number
大園區,領航北路四段351號,1141220,32.5,18000000,553846,住宅大樓(11層含以上有電梯),十二層,二十層,坡道平面,10.0,1800000,H-101
```

```python
# tests/test_moi.py
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
```

- [ ] **Step 2: Run tests and verify the parser is missing**

Run: `.\.venv\Scripts\python -m pytest tests/test_moi.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'qingpu_insight.moi'`.

- [ ] **Step 3: Implement strict normalization**

```python
# src/qingpu_insight/moi.py
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
    frame = pd.read_csv(path, dtype=str, encoding="utf-8-sig", low_memory=False)
    frame = frame.rename(columns={key: value for key, value in COLUMN_MAP.items() if key in frame})
    required = {"district", "address", "transaction_date", "total_price_twd"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing MOI columns: {sorted(missing)}")
    frame = frame[frame["district"].isin(("中壢區", "大園區"))].copy()
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
```

- [ ] **Step 4: Run parser tests and inspect dtypes**

Run:

```powershell
.\.venv\Scripts\python -m pytest tests/test_moi.py -v
.\.venv\Scripts\python -c "from pathlib import Path; from qingpu_insight.moi import read_moi_csv; print(read_moi_csv(Path('tests/fixtures/moi_resale.csv'), 'resale').dtypes)"
```

Expected: three tests pass; price and area columns are numeric and `transaction_date` is `datetime64[ns]`.

- [ ] **Step 5: Lint and commit**

Run: `.\.venv\Scripts\python -m ruff check src tests`

Expected: `All checks passed!`.

Commit:

```powershell
git add src/qingpu_insight/moi.py tests/fixtures/moi_resale.csv tests/fixtures/moi_presale.csv tests/test_moi.py
git commit -m "feat: normalize Taoyuan resale and presale data"
```

---

### Task 4: Doorplate Matching and A17–A19 Assignment

**Files:**
- Create: `src/qingpu_insight/addresses.py`
- Create: `src/qingpu_insight/geo.py`
- Create: `tests/test_addresses.py`
- Create: `tests/test_geo.py`

**Interfaces:**
- Consumes: canonical `address` and `district` from Task 3; `Station` from Task 1; official doorplate CSV bytes downloaded by Task 2.
- Produces: `normalize_address(value: str) -> str`, `build_doorplate_frame(path: Path) -> pd.DataFrame`, `match_addresses(transactions, doorplates) -> pd.DataFrame`, `station_points(stations, doorplates) -> pd.DataFrame`, and `assign_life_circle(transactions, stations, radius_m) -> pd.DataFrame`.
- Match qualities: `exact`, `nearest_number`, `road_only`, `unmatched`. Only `exact` and `nearest_number` are eligible for the 2 km decision.

- [ ] **Step 1: Write failing address and geospatial tests**

```python
# tests/test_addresses.py
import pandas as pd

from qingpu_insight.addresses import match_addresses, normalize_address


def test_normalize_address_removes_city_district_and_width_variants() -> None:
    assert normalize_address("桃園市中壢區高鐵北路１段５號") == "高鐵北路一段5號"


def test_match_addresses_marks_exact_and_nearest_number() -> None:
    transactions = pd.DataFrame(
        {
            "district": ["中壢區", "中壢區"],
            "address": ["高鐵北路一段5號", "高鐵北路一段8號"],
        }
    )
    doorplates = pd.DataFrame(
        {
            "district": ["中壢區", "中壢區"],
            "normalized_address": ["高鐵北路一段5號", "高鐵北路一段10號"],
            "road_key": ["高鐵北路一段", "高鐵北路一段"],
            "house_number": [5, 10],
            "twd97_x": [276000.0, 276010.0],
            "twd97_y": [2767000.0, 2767010.0],
        }
    )

    result = match_addresses(transactions, doorplates)

    assert result["match_quality"].tolist() == ["exact", "nearest_number"]
    assert result["coordinate_eligible"].tolist() == [True, True]
```

```python
# tests/test_geo.py
import pandas as pd

from qingpu_insight.geo import assign_life_circle


def test_assign_life_circle_uses_nearest_station_inside_radius() -> None:
    transactions = pd.DataFrame(
        {
            "twd97_x": [100.0, 5_000.0],
            "twd97_y": [0.0, 0.0],
            "coordinate_eligible": [True, True],
        }
    )
    stations = pd.DataFrame(
        {
            "station_code": ["A17", "A18", "A19"],
            "twd97_x": [0.0, 1_000.0, 2_000.0],
            "twd97_y": [0.0, 0.0, 0.0],
        }
    )

    result = assign_life_circle(transactions, stations, radius_m=2_000.0)

    assert result.loc[0, "station_code"] == "A17"
    assert result.loc[0, "station_distance_m"] == 100.0
    assert pd.isna(result.loc[1, "station_code"])
```

- [ ] **Step 2: Run tests and verify modules are missing**

Run: `.\.venv\Scripts\python -m pytest tests/test_addresses.py tests/test_geo.py -v`

Expected: collection fails for missing `addresses` and `geo` modules.

- [ ] **Step 3: Implement deterministic address normalization and matching**

```python
# src/qingpu_insight/addresses.py
from pathlib import Path
import re

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
    match = re.search(r"^(.+?(?:路|街)(?:[一二三四五六七八九]段)?)", address)
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
            "地區": "area",
            "巷": "lane",
            "弄": "alley",
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
```

- [ ] **Step 4: Implement station lookup and distance assignment**

```python
# src/qingpu_insight/geo.py
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
    if not located["coordinate_eligible"].all():
        missing = located.loc[~located["coordinate_eligible"], "station_code"].tolist()
        raise ValueError(f"station addresses not located precisely: {missing}")
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
```

- [ ] **Step 5: Add NumPy as an explicit dependency, test, lint, and commit**

Add `"numpy>=2,<3",` to the `dependencies` array in `pyproject.toml`, then run:

```powershell
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest tests/test_addresses.py tests/test_geo.py -v
.\.venv\Scripts\python -m ruff check src tests
```

Expected: three tests pass and Ruff reports no errors.

Commit:

```powershell
git add pyproject.toml src/qingpu_insight/addresses.py src/qingpu_insight/geo.py tests/test_addresses.py tests/test_geo.py
git commit -m "feat: locate transactions in A17 to A19 life circles"
```

---

### Task 5: Feasibility Metrics and Decision Report

**Files:**
- Create: `src/qingpu_insight/feasibility.py`
- Create: `src/qingpu_insight/reporting.py`
- Create: `tests/test_feasibility.py`

**Interfaces:**
- Consumes: assigned transaction frames from Task 4 and `Thresholds` from Task 1.
- Produces: `FeasibilityResult`, `evaluate_feasibility(frame, thresholds) -> FeasibilityResult`, `render_markdown(result, sources) -> str`, and `write_report(result, report_dir, sources) -> tuple[Path, Path]`.

- [ ] **Step 1: Write failing decision and rendering tests**

```python
# tests/test_feasibility.py
from pathlib import Path

import pandas as pd

from qingpu_insight.config import Thresholds
from qingpu_insight.feasibility import evaluate_feasibility
from qingpu_insight.reporting import write_report


def sample_frame(rows_per_cell: int) -> pd.DataFrame:
    rows = []
    for transaction_type in ("resale", "presale"):
        for station_code in ("A17", "A18", "A19"):
            for index in range(rows_per_cell):
                rows.append(
                    {
                        "transaction_type": transaction_type,
                        "station_code": station_code,
                        "transaction_date": pd.Timestamp("2026-01-01"),
                        "coordinate_eligible": True,
                        "match_quality": "exact",
                        "total_price_twd": 10_000_000 + index,
                    }
                )
    return pd.DataFrame(rows)


def test_feasibility_passes_when_all_thresholds_pass() -> None:
    thresholds = Thresholds(
        minimum_total_by_type=100,
        minimum_station_type_cell=20,
        minimum_coordinate_coverage=0.60,
        minimum_recent_by_type=100,
    )
    result = evaluate_feasibility(sample_frame(60), thresholds)

    assert result.decision == "GO"
    assert result.failed_checks == ()


def test_feasibility_reports_failed_station_cell() -> None:
    result = evaluate_feasibility(sample_frame(10), Thresholds())

    assert result.decision == "NO-GO"
    assert "minimum_station_type_cell" in result.failed_checks


def test_write_report_creates_markdown_and_csv(tmp_path: Path) -> None:
    result = evaluate_feasibility(sample_frame(60), Thresholds(100, 20, 0.60, 100))

    markdown, csv = write_report(result, tmp_path, ["https://data.gov.tw/dataset/77051"])

    assert "# 青埔智價 M0 資料可行性報告" in markdown.read_text(encoding="utf-8")
    assert "GO" in markdown.read_text(encoding="utf-8")
    assert csv.exists()
```

- [ ] **Step 2: Run tests and verify report modules are missing**

Run: `.\.venv\Scripts\python -m pytest tests/test_feasibility.py -v`

Expected: collection fails for missing `feasibility` and `reporting` modules.

- [ ] **Step 3: Implement explicit metrics and gate logic**

```python
# src/qingpu_insight/feasibility.py
from dataclasses import dataclass

import pandas as pd

from qingpu_insight.config import Thresholds


@dataclass(frozen=True)
class FeasibilityResult:
    decision: str
    failed_checks: tuple[str, ...]
    summary: pd.DataFrame
    coordinate_coverage: float
    latest_date: pd.Timestamp
    recent_cutoff: pd.Timestamp


def evaluate_feasibility(frame: pd.DataFrame, thresholds: Thresholds) -> FeasibilityResult:
    if frame.empty:
        raise ValueError("cannot evaluate an empty transaction frame")
    latest_date = frame["transaction_date"].max()
    if pd.isna(latest_date):
        raise ValueError("transaction frame has no valid transaction dates")
    recent_cutoff = latest_date - pd.DateOffset(months=24)
    eligible = frame["coordinate_eligible"].fillna(False)
    coordinate_coverage = float(eligible.mean())
    assigned = frame[frame["station_code"].notna()].copy()
    summary = (
        assigned.groupby(["transaction_type", "station_code"], observed=True)
        .agg(
            assigned_records=("station_code", "size"),
            first_date=("transaction_date", "min"),
            last_date=("transaction_date", "max"),
            median_total_price_twd=("total_price_twd", "median"),
        )
        .reset_index()
    )
    total_by_type = assigned.groupby("transaction_type").size()
    recent_by_type = assigned[assigned["transaction_date"] >= recent_cutoff].groupby(
        "transaction_type"
    ).size()
    expected_types = {"resale", "presale"}
    failed: list[str] = []
    if any(total_by_type.get(kind, 0) < thresholds.minimum_total_by_type for kind in expected_types):
        failed.append("minimum_total_by_type")
    expected_cells = {(kind, station) for kind in expected_types for station in ("A17", "A18", "A19")}
    actual_cells = {
        (row.transaction_type, row.station_code): row.assigned_records
        for row in summary.itertuples(index=False)
    }
    if any(
        actual_cells.get(cell, 0) < thresholds.minimum_station_type_cell
        for cell in expected_cells
    ):
        failed.append("minimum_station_type_cell")
    if coordinate_coverage < thresholds.minimum_coordinate_coverage:
        failed.append("minimum_coordinate_coverage")
    if any(recent_by_type.get(kind, 0) < thresholds.minimum_recent_by_type for kind in expected_types):
        failed.append("minimum_recent_by_type")
    return FeasibilityResult(
        decision="GO" if not failed else "NO-GO",
        failed_checks=tuple(failed),
        summary=summary,
        coordinate_coverage=coordinate_coverage,
        latest_date=latest_date,
        recent_cutoff=recent_cutoff,
    )
```

- [ ] **Step 4: Implement reproducible Markdown and CSV output**

```python
# src/qingpu_insight/reporting.py
from pathlib import Path
from typing import Iterable

from qingpu_insight.feasibility import FeasibilityResult


CHECK_LABELS = {
    "minimum_total_by_type": "中古屋或預售屋的總筆數不足",
    "minimum_station_type_cell": "至少一個站點／交易類型組合不足 50 筆",
    "minimum_coordinate_coverage": "可用座標覆蓋率低於 60%",
    "minimum_recent_by_type": "最近 24 個月的中古屋或預售屋不足 100 筆",
}


def render_markdown(result: FeasibilityResult, sources: Iterable[str]) -> str:
    failures = (
        "\n".join(f"- {CHECK_LABELS[item]} (`{item}`)" for item in result.failed_checks)
        if result.failed_checks
        else "- 所有 M0 門檻均通過。"
    )
    source_lines = "\n".join(f"- {source}" for source in sources)
    table = result.summary.to_markdown(index=False) if not result.summary.empty else "無可歸屬紀錄。"
    return f"""# 青埔智價 M0 資料可行性報告

## 結論

**{result.decision}**

## 品質摘要

- 可用座標覆蓋率：{result.coordinate_coverage:.1%}
- 最新交易日期：{result.latest_date.date().isoformat()}
- 最近資料門檻起日：{result.recent_cutoff.date().isoformat()}

{failures}

## A17～A19 可用紀錄

{table}

## 官方來源

{source_lines}

## 決策規則

只有在中古屋與預售屋總筆數、各站點／類型筆數、座標覆蓋率及最近 24 個月筆數全部通過時，結果才是 GO。NO-GO 代表先修正資料範圍或定位方法，不進入模型與網站實作。
"""


def write_report(
    result: FeasibilityResult,
    report_dir: Path,
    sources: Iterable[str],
) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = report_dir / "m0-data-feasibility.md"
    csv_path = report_dir / "m0-station-summary.csv"
    markdown_path.write_text(render_markdown(result, sources), encoding="utf-8")
    result.summary.to_csv(csv_path, index=False, encoding="utf-8-sig")
    return markdown_path, csv_path
```

- [ ] **Step 5: Add tabulate dependency, run tests, lint, and commit**

Add `"tabulate>=0.9,<1",` to `dependencies` in `pyproject.toml`, then run:

```powershell
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest tests/test_feasibility.py -v
.\.venv\Scripts\python -m ruff check src tests
```

Expected: three tests pass and Ruff reports no errors.

Commit:

```powershell
git add pyproject.toml src/qingpu_insight/feasibility.py src/qingpu_insight/reporting.py tests/test_feasibility.py
git commit -m "feat: gate development on M0 feasibility metrics"
```

---

### Task 6: Offline End-to-End CLI and M0 Documentation

**Files:**
- Create: `src/qingpu_insight/cli.py`
- Create: `tests/fixtures/doorplates.csv`
- Create: `tests/test_cli.py`
- Create: `README.md`

**Interfaces:**
- Consumes: every interface from Tasks 1–5.
- Produces: `qingpu-data acquire --start-season 110S3 --end-season 115S2`, `qingpu-data analyse`, and `qingpu-data run --start-season 110S3 --end-season 115S2`.
- `analyse` must operate without network access when raw files already exist.

- [ ] **Step 1: Write the failing offline CLI test**

```csv
省市縣市代碼,鄉鎮市區代碼,村里,鄰,街路段,地區,巷,弄,號,橫座標,縱座標
68000,6800200,,,高鐵北路一段,,,,5號,276000,2767000
68000,6800200,,,高鐵南路二段,,,,350號,275000,2766000
68000,6800600,,,領航北路四段,,,,351號,274000,2768000
```

```python
# tests/test_cli.py
from pathlib import Path

from qingpu_insight.cli import main


FIXTURES = Path(__file__).parent / "fixtures"


def test_analyse_command_builds_outputs_without_network(tmp_path: Path, monkeypatch) -> None:
    raw = tmp_path / "data" / "raw" / "current"
    raw.mkdir(parents=True)
    (raw / "h_lvr_land_a.csv").write_bytes((FIXTURES / "moi_resale.csv").read_bytes())
    (raw / "h_lvr_land_b.csv").write_bytes((FIXTURES / "moi_presale.csv").read_bytes())
    doorplates = tmp_path / "data" / "raw" / "doorplates.csv"
    doorplates.write_bytes((FIXTURES / "doorplates.csv").read_bytes())
    monkeypatch.chdir(tmp_path)

    exit_code = main(["analyse", "--allow-no-go"])

    assert exit_code == 0
    assert (tmp_path / "data" / "processed" / "transactions.parquet").exists()
    assert (tmp_path / "outputs" / "reports" / "m0-data-feasibility.md").exists()
```

- [ ] **Step 2: Run the test and verify the CLI is missing**

Run: `.\.venv\Scripts\python -m pytest tests/test_cli.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'qingpu_insight.cli'`.

- [ ] **Step 3: Implement season enumeration, acquisition, analysis, and exit codes**

```python
# src/qingpu_insight/cli.py
import argparse
from pathlib import Path
import sys

import pandas as pd

from qingpu_insight.addresses import build_doorplate_frame, match_addresses
from qingpu_insight.archives import extract_taoyuan_tables
from qingpu_insight.config import get_settings
from qingpu_insight.downloads import (
    download_current_table,
    download_file,
    download_season,
    write_manifest,
)
from qingpu_insight.feasibility import evaluate_feasibility
from qingpu_insight.geo import assign_life_circle, station_points
from qingpu_insight.moi import read_moi_csv
from qingpu_insight.reporting import write_report


SOURCES = (
    "https://data.gov.tw/dataset/77051",
    "https://data.gov.tw/dataset/157689",
    "https://www.tymetro.com.tw/tymetro-new/tw/_pages/travel-guide/A17",
    "https://www.tymetro.com.tw/tymetro-new/tw/_pages/travel-guide/A18",
    "https://www.tymetro.com.tw/tymetro-new/tw/_pages/travel-guide/A19",
)


def season_key(value: str) -> tuple[int, int]:
    year, quarter = value.upper().split("S", maxsplit=1)
    parsed = (int(year), int(quarter))
    if parsed[0] < 101 or parsed[1] not in (1, 2, 3, 4):
        raise ValueError(f"invalid ROC season: {value}")
    return parsed


def iter_seasons(start: str, end: str) -> tuple[str, ...]:
    start_key = season_key(start)
    end_key = season_key(end)
    if start_key > end_key:
        raise ValueError("start season must not be after end season")
    values = []
    year, quarter = start_key
    while (year, quarter) <= end_key:
        values.append(f"{year}S{quarter}")
        quarter += 1
        if quarter == 5:
            year += 1
            quarter = 1
    return tuple(values)


def acquire(root: Path, start: str, end: str) -> None:
    settings = get_settings(root)
    records = []
    for season in iter_seasons(start, end):
        archive = settings.raw_dir / "seasons" / f"{season}.zip"
        records.append(download_season(settings.sources.moi_base_url, season, archive))
        extract_taoyuan_tables(archive, settings.raw_dir / "seasons" / season)
    current = settings.raw_dir / "current"
    for name in ("h_lvr_land_a.csv", "h_lvr_land_b.csv"):
        records.append(
            download_current_table(settings.sources.moi_base_url, name, current / name)
        )
    records.append(
        download_file(settings.sources.doorplate_url, settings.raw_dir / "doorplates.csv")
    )
    write_manifest(records, settings.raw_dir / "manifest.json")


def _transaction_files(raw_dir: Path) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    for path in sorted(raw_dir.glob("seasons/*/h_lvr_land_[ab].csv")):
        files.append((path, "resale" if path.name.endswith("_a.csv") else "presale"))
    for path in sorted((raw_dir / "current").glob("h_lvr_land_[ab].csv")):
        files.append((path, "resale" if path.name.endswith("_a.csv") else "presale"))
    return files


def analyse(root: Path, allow_no_go: bool) -> int:
    settings = get_settings(root)
    files = _transaction_files(settings.raw_dir)
    if not files:
        raise FileNotFoundError("no MOI transaction CSV files found; run acquire first")
    frames = [read_moi_csv(path, kind) for path, kind in files]
    transactions = pd.concat(frames, ignore_index=True)
    business_columns = [column for column in transactions.columns if column != "source_file"]
    transactions = transactions.drop_duplicates(subset=business_columns)
    doorplates = build_doorplate_frame(settings.raw_dir / "doorplates.csv")
    located = match_addresses(transactions, doorplates)
    stations = station_points(settings.stations, doorplates)
    assigned = assign_life_circle(located, stations, settings.radius_m)
    settings.processed_dir.mkdir(parents=True, exist_ok=True)
    assigned.to_parquet(settings.processed_dir / "transactions.parquet", index=False)
    result = evaluate_feasibility(assigned, settings.thresholds)
    write_report(result, settings.report_dir, SOURCES)
    print(f"M0 decision: {result.decision}")
    return 0 if result.decision == "GO" or allow_no_go else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qingpu-data")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("acquire", "run"):
        command = subparsers.add_parser(name)
        command.add_argument("--start-season", default="110S3")
        command.add_argument("--end-season", default="115S2")
    analyse_parser = subparsers.add_parser("analyse")
    analyse_parser.add_argument("--allow-no-go", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path.cwd()
    if args.command in ("acquire", "run"):
        acquire(root, args.start_season, args.end_season)
    if args.command in ("analyse", "run"):
        return analyse(root, getattr(args, "allow_no_go", False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the offline end-to-end test and the entire test suite**

Run:

```powershell
.\.venv\Scripts\python -m pytest tests/test_cli.py -v
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m ruff check src tests
```

Expected: the CLI test passes, all earlier tests pass, and Ruff reports no errors.

- [ ] **Step 5: Document the exact operator workflow**

```markdown
<!-- README.md -->
# 青埔智價 Qingpu Insight

青埔 A17～A19 房價分析與 AI 估價作品集。本 repository 目前實作 M0 資料可行性管線。

## 官方資料

- 內政部實價登錄：https://data.gov.tw/dataset/77051
- 桃園門牌座標：https://data.gov.tw/dataset/157689
- 桃園捷運 A17／A18／A19 官方站點頁面

使用資料時須標示來源。原始資料不提交 Git。

## Windows 安裝

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

## M0 執行

```powershell
# 下載民國 110 年第 3 季到 115 年第 2 季，加上本期資料與最新門牌座標
.\.venv\Scripts\qingpu-data acquire --start-season 110S3 --end-season 115S2

# 離線重跑清理、定位與可行性報告
.\.venv\Scripts\qingpu-data analyse

# 一次下載並分析
.\.venv\Scripts\qingpu-data run --start-season 110S3 --end-season 115S2
```

產出：

- `data/processed/transactions.parquet`
- `outputs/reports/m0-data-feasibility.md`
- `outputs/reports/m0-station-summary.csv`

`GO` 才進入市場儀表板與模型階段。`NO-GO` 時先查看報告中的資料筆數、定位率與失敗門檻。

## 驗證

```powershell
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m ruff check src tests
```
```

- [ ] **Step 6: Verify the CLI help, commit, and tag M0 plan readiness**

Run:

```powershell
.\.venv\Scripts\qingpu-data --help
.\.venv\Scripts\qingpu-data analyse --help
git status --short
```

Expected: both help commands exit 0; only the four Task 6 files are uncommitted.

Commit:

```powershell
git add src/qingpu_insight/cli.py tests/fixtures/doorplates.csv tests/test_cli.py README.md
git commit -m "feat: deliver offline M0 feasibility workflow"
```

---

## Live Data Acceptance Run

After all six tasks pass offline, run the live official-data workflow once:

```powershell
.\.venv\Scripts\qingpu-data run --start-season 110S3 --end-season 115S2
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m ruff check src tests
git status --short
```

Expected outcomes:

1. Every raw file appears in `data/raw/manifest.json` with a non-empty SHA-256 and positive byte size.
2. `data/processed/transactions.parquet` contains distinct `resale` and `presale` rows.
3. Every assigned row has `station_code` in A17/A18/A19 and `station_distance_m <= 2000`.
4. The Markdown report contains one explicit `GO` or `NO-GO` decision and every failed gate.
5. Tests and Ruff pass after the live run.
6. Git remains clean because raw and generated outputs are ignored.

If the decision is `NO-GO`, do not weaken thresholds silently. Record the observed counts and coordinate coverage in a design-review note, then choose one explicit change: improve address parsing, expand the radius with user approval, or redefine the geographic scope with user approval.

## Self-Review Result

- Spec coverage: M0 acquisition, source traceability, resale/presale isolation, A17–A19 assignment, coordinate quality, explicit thresholds, reproducible outputs, tests, and documentation are covered.
- Scope isolation: database, model, web, LLM, and deployment work are excluded and reserved for later plans.
- Type consistency: configuration, canonical DataFrame columns, station codes, match-quality values, and report interfaces match across all tasks.
- Placeholder scan: the plan contains no incomplete implementation steps or undefined project interfaces.
