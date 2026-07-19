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
        raw_dir=root / 'data' / 'raw',
        processed_dir=root / 'data' / 'processed',
        report_dir=root / 'outputs' / 'reports',
        districts=('中壢區', '大園區'),
        stations=(
            Station('A17', '領航站', '桃園市大園區領航北路四段351號'),
            Station('A18', '高鐵桃園站', '桃園市中壢區高鐵北路一段5號'),
            Station('A19', '桃園體育園區站', '桃園市中壢區高鐵南路二段350號'),
        ),
        radius_m=2_000.0,
        sources=SourceConfig(
            moi_base_url='https://plvr.land.moi.gov.tw',
            doorplate_url=(
                'https://opendata.tycg.gov.tw/api/dataset/'
                'ec47dbd5-9ed8-4c8d-8ce1-ccb63b1b72e6/resource/'
                '4ee7723b-84dc-41c3-865e-6ea3f7bb02a9/download'
            ),
        ),
        thresholds=Thresholds(),
    )
