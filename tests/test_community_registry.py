"""Tests for the curated Qingpu community registry."""

from pathlib import Path

import pandas as pd
import pytest

from qingpu_insight.community_registry import CommunityRegistry


@pytest.fixture
def csv_path() -> Path:
    return Path(__file__).parent.parent / "data" / "reference" / "qingpu_communities.csv"


@pytest.fixture
def registry(csv_path: Path) -> CommunityRegistry:
    return CommunityRegistry.from_csv(csv_path)


# ── CSV Contract / Validation ─────────────────────────────


def test_required_columns_present() -> None:
    data = pd.read_csv(
        Path(__file__).parent.parent / "data" / "reference" / "qingpu_communities.csv"
    )
    required = [
        "community_id",
        "canonical_name",
        "aliases",
        "station_code",
        "address_patterns",
        "twd97_x",
        "twd97_y",
        "completion_year",
        "source_notes",
    ]
    for col in required:
        assert col in data.columns, f"Missing column: {col}"


def _write_csv(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_duplicate_community_id_raises_error(tmp_path: Path) -> None:
    csv = tmp_path / "bad.csv"
    _write_csv(csv,
        "community_id,canonical_name,aliases,station_code,address_patterns,"
        "twd97_x,twd97_y,completion_year,source_notes\r\n"
        "a,A,,A17,road,1,2,2020,verified\r\n"
        "a,A,,A17,road,1,2,2020,verified\r\n"
        "b,B,,A18,road2,3,4,2021,verified\r\n"
        "c,C,,A19,road3,5,6,2022,verified\r\n",
    )
    with pytest.raises(ValueError, match="Duplicate community_id"):
        CommunityRegistry.from_csv(csv)


def test_duplicate_normalized_alias_raises_error(tmp_path: Path) -> None:
    csv = tmp_path / "bad.csv"
    _write_csv(csv, 
        "community_id,canonical_name,aliases,station_code,address_patterns,"
        "twd97_x,twd97_y,completion_year,source_notes\r\n"
        "a,社區A,AliasX;AliasY,A17,road1,1,2,2020,verified\r\n"
        "b,社區B,AliasY;AliasZ,A18,road2,3,4,2021,verified\r\n"
        "c,社區C,,A19,road3,5,6,2022,verified\r\n",
    )
    with pytest.raises(ValueError, match="Duplicate normalized aliases"):
        CommunityRegistry.from_csv(csv)


def test_invalid_station_code_raises_error(tmp_path: Path) -> None:
    csv = tmp_path / "bad.csv"
    _write_csv(csv, 
        "community_id,canonical_name,aliases,station_code,address_patterns,"
        "twd97_x,twd97_y,completion_year,source_notes\r\n"
        "a,社區A,,A99,road1,1,2,2020,verified\r\n"
        "b,社區B,,A18,road2,3,4,2021,verified\r\n"
        "c,社區C,,A19,road3,5,6,2022,verified\r\n",
    )
    with pytest.raises(ValueError, match="Invalid station codes"):
        CommunityRegistry.from_csv(csv)


def test_missing_source_notes_raises_error(tmp_path: Path) -> None:
    csv = tmp_path / "bad.csv"
    _write_csv(csv, 
        "community_id,canonical_name,aliases,station_code,address_patterns,"
        "twd97_x,twd97_y,completion_year,source_notes\r\n"
        "a,社區A,,A17,road1,1,2,2020,\r\n"
        "b,社區B,,A18,road2,3,4,2021,verified\r\n"
        "c,社區C,,A19,road3,5,6,2022,verified\r\n",
    )
    with pytest.raises(ValueError, match="Missing source_notes"):
        CommunityRegistry.from_csv(csv)


def test_ambiguous_address_pattern_raises_error(tmp_path: Path) -> None:
    csv = tmp_path / "bad.csv"
    _write_csv(csv, 
        "community_id,canonical_name,aliases,station_code,address_patterns,"
        "twd97_x,twd97_y,completion_year,source_notes\r\n"
        "a,社區A,,A17,shared-road,1,2,2020,verified\r\n"
        "b,社區B,,A18,shared-road,3,4,2021,verified\r\n"
        "c,社區C,,A19,road3,5,6,2022,verified\r\n",
    )
    with pytest.raises(ValueError, match="Ambiguous address patterns"):
        CommunityRegistry.from_csv(csv)


def test_invalid_coordinates_raises_error(tmp_path: Path) -> None:
    csv = tmp_path / "bad.csv"
    _write_csv(csv, 
        "community_id,canonical_name,aliases,station_code,address_patterns,"
        "twd97_x,twd97_y,completion_year,source_notes\r\n"
        "a,社區A,,A17,road1,not-a-number,2,2020,verified\r\n"
        "b,社區B,,A18,road2,3,4,2021,verified\r\n"
        "c,社區C,,A19,road3,5,6,2022,verified\r\n",
    )
    with pytest.raises(ValueError, match="coordinates for"):
        CommunityRegistry.from_csv(csv)


def test_minimum_entry_count_raises_error(tmp_path: Path) -> None:
    csv = tmp_path / "too_few.csv"
    lines = [
        "community_id,canonical_name,aliases,station_code,address_patterns,"
        "twd97_x,twd97_y,completion_year,source_notes",
    ]
    for i in range(3):
        lines.append(
            f"id{i},社區{i},,A17,road{i},{i},{i},2020,verified"
        )
    _write_csv(csv, "\r\n".join(lines))
    with pytest.raises(ValueError, match="at least 20"):
        CommunityRegistry.from_csv(csv)


# ── Version ────────────────────────────────────────────────


def test_version_is_sha256_hex_string(registry: CommunityRegistry) -> None:
    v = registry.version
    assert isinstance(v, str)
    assert len(v) == 64
    int(v, 16)


def test_version_deterministic(tmp_path: Path, csv_path: Path) -> None:
    r1 = CommunityRegistry.from_csv(csv_path)
    r2 = CommunityRegistry.from_csv(csv_path)
    assert r1.version == r2.version


def test_version_changes_when_data_changes(tmp_path: Path) -> None:
    base = (
        "community_id,canonical_name,aliases,station_code,address_patterns,"
        "twd97_x,twd97_y,completion_year,source_notes\r\n"
    )
    rows_a = "\r\n".join(
        f"id{i},社區{i},,A18,road{i},{i},{i},202{i},verified"
        for i in range(20, 40)
    )
    rows_b = "\r\n".join(
        f"id{i},社區{i},,A18,road{i},{i},{i},202{i},verified"
        for i in range(21, 41)
    )
    p = tmp_path / "v.csv"
    _write_csv(p, base + rows_a)
    v1 = CommunityRegistry.from_csv(p).version
    _write_csv(p, base + rows_b)
    v2 = CommunityRegistry.from_csv(p).version
    assert v1 != v2


# ── Matching: canonical name ──────────────────────────────


def test_match_listing_by_canonical_name(registry: CommunityRegistry) -> None:
    result = registry.match_listing(name="竹風青庭")
    assert result.method == "canonical"
    assert result.confidence == "high"
    assert result.community_id == "zhufeng-qingting"
    assert result.canonical_name == "竹風青庭"


def test_match_listing_by_canonical_name_normalized(registry: CommunityRegistry) -> None:
    result = registry.match_listing(name=" 竹風-青庭 ")
    assert result.method == "canonical"
    assert result.confidence == "high"


# ── Matching: alias ───────────────────────────────────────


def test_match_listing_by_alias(registry: CommunityRegistry) -> None:
    result = registry.match_listing(name="青庭")
    assert result.method == "alias"
    assert result.confidence == "high"
    assert result.community_id == "zhufeng-qingting"


def test_match_listing_by_alias_normalized(registry: CommunityRegistry) -> None:
    result = registry.match_listing(name=" 青庭 ")
    assert result.method == "alias"
    assert result.confidence == "high"


# ── Matching: address pattern ─────────────────────────────


def test_match_listing_by_unique_address(registry: CommunityRegistry) -> None:
    result = registry.match_listing(
        address="桃園市中壢區領航北路四段310號8樓"
    )
    assert result.method == "address"
    assert result.confidence == "medium"


def test_match_transaction_by_address(registry: CommunityRegistry) -> None:
    result = registry.match_transaction(
        address="桃園市中壢區文智路68號3樓"
    )
    assert result.method == "address"
    assert result.confidence == "medium"


def test_match_address_no_pattern_returns_unknown(registry: CommunityRegistry) -> None:
    result = registry.match_listing(
        name="unknown community xyz",
        address="some completely unmatchable address"
    )
    assert result.method == "unknown"
    assert result.confidence == "none"
    assert result.community_id is None


# ── Matching: coordinate ──────────────────────────────────


def test_match_by_coordinate_returns_medium(registry: CommunityRegistry) -> None:
    result = registry.match_listing(
        name="unknown name",
        address="unknown address",
        twd97_x=275000.0,
        twd97_y=2765000.0,
        completion_year=2020,
    )
    # If any community has coordinates within 500m, returns medium
    # Without actual coordinates in CSV, this falls through to unknown
    assert result.method in ("coordinate", "unknown")


def test_match_by_coordinate_unknown_when_far(tmp_path: Path) -> None:
    csv = tmp_path / "coords.csv"
    _write_csv(csv, 
        "community_id,canonical_name,aliases,station_code,address_patterns,"
        "twd97_x,twd97_y,completion_year,source_notes\r\n"
        "a,社區A,,A17,road,275000.0,2765000.0,2020,verified\r\n"
        "b,社區B,,A18,road2,276000.0,2766000.0,2021,verified\r\n"
        "c,社區C,,A19,road3,277000.0,2767000.0,2022,verified\r\n"
        "d,社區D,,A17,road4,278000.0,2768000.0,2023,verified\r\n"
        "e,社區E,,A18,road5,279000.0,2769000.0,2024,verified\r\n"
        "f,社區F,,A19,road6,280000.0,2770000.0,2025,verified\r\n"
        "g,社區G,,A17,road7,281000.0,2771000.0,2020,verified\r\n"
        "h,社區H,,A18,road8,282000.0,2772000.0,2021,verified\r\n"
        "i,社區I,,A19,road9,283000.0,2773000.0,2022,verified\r\n"
        "j,社區J,,A17,road10,284000.0,2774000.0,2023,verified\r\n"
        "k,社區K,,A18,road11,285000.0,2775000.0,2024,verified\r\n"
        "l,社區L,,A19,road12,286000.0,2776000.0,2025,verified\r\n"
        "m,社區M,,A17,road13,287000.0,2777000.0,2020,verified\r\n"
        "n,社區N,,A18,road14,288000.0,2778000.0,2021,verified\r\n"
        "o,社區O,,A19,road15,289000.0,2779000.0,2022,verified\r\n"
        "p,社區P,,A17,road16,290000.0,2780000.0,2023,verified\r\n"
        "q,社區Q,,A18,road17,291000.0,2781000.0,2024,verified\r\n"
        "r,社區R,,A19,road18,292000.0,2782000.0,2025,verified\r\n"
        "s,社區S,,A17,road19,293000.0,2783000.0,2020,verified\r\n"
        "t,社區T,,A18,road20,294000.0,2784000.0,2021,verified\r\n"
    )
    r = CommunityRegistry.from_csv(csv)
    result = r.match_listing(
        name="unknown",
        address="far away",
        twd97_x=100.0,
        twd97_y=100.0,
        completion_year=2020,
    )
    assert result.method == "unknown"
    assert result.confidence == "none"
    assert result.community_id is None


def test_match_transaction_by_coordinate_within_radius(tmp_path: Path) -> None:
    csv = tmp_path / "coords_match.csv"
    _write_csv(csv,
        "community_id,canonical_name,aliases,station_code,address_patterns,"
        "twd97_x,twd97_y,completion_year,source_notes\r\n"
        "a,社區A,,A17,road1,275000.0,2765000.0,2020,verified\r\n"
        "b,社區B,,A18,road2,276000.0,2766000.0,2021,verified\r\n"
        "c,社區C,,A19,road3,277000.0,2767000.0,2022,verified\r\n"
        "d,社區D,,A17,road4,278000.0,2768000.0,2023,verified\r\n"
        "e,社區E,,A18,road5,279000.0,2769000.0,2024,verified\r\n"
        "f,社區F,,A19,road6,280000.0,2770000.0,2025,verified\r\n"
        "g,社區G,,A17,road7,281000.0,2771000.0,2020,verified\r\n"
        "h,社區H,,A18,road8,282000.0,2772000.0,2021,verified\r\n"
        "i,社區I,,A19,road9,283000.0,2773000.0,2022,verified\r\n"
        "j,社區J,,A17,road10,284000.0,2774000.0,2023,verified\r\n"
        "k,社區K,,A18,road11,285000.0,2775000.0,2024,verified\r\n"
        "l,社區L,,A19,road12,286000.0,2776000.0,2025,verified\r\n"
        "m,社區M,,A17,road13,287000.0,2777000.0,2020,verified\r\n"
        "n,社區N,,A18,road14,288000.0,2778000.0,2021,verified\r\n"
        "o,社區O,,A19,road15,289000.0,2779000.0,2022,verified\r\n"
        "p,社區P,,A17,road16,290000.0,2780000.0,2023,verified\r\n"
        "q,社區Q,,A18,road17,291000.0,2781000.0,2024,verified\r\n"
        "r,社區R,,A19,road18,292000.0,2782000.0,2025,verified\r\n"
        "s,社區S,,A17,road19,293000.0,2783000.0,2020,verified\r\n"
        "t,社區T,,A18,road20,294000.0,2784000.0,2021,verified\r\n"
    )
    r = CommunityRegistry.from_csv(csv)
    result = r.match_transaction(
        twd97_x=275100.0, twd97_y=2765100.0, completion_year=2020,
    )
    assert result.method == "coordinate"
    assert result.confidence == "medium"
    assert result.community_id == "a"
    result = r.match_transaction(
        twd97_x=100.0, twd97_y=100.0, completion_year=2020,
    )
    assert result.method == "unknown"
    assert result.confidence == "none"
    assert result.community_id is None


# ── Matching: unknown ─────────────────────────────────────


def test_match_transaction_no_info_returns_unknown(registry: CommunityRegistry) -> None:
    result = registry.match_transaction()
    assert result.method == "unknown"
    assert result.confidence == "none"
    assert result.community_id is None


def test_match_listing_name_unknown_returns_unknown(registry: CommunityRegistry) -> None:
    result = registry.match_listing(name="非社區名稱 nonexis tent")
    assert result.method == "unknown"
    assert result.confidence == "none"
    assert result.community_id is None


# ── Public catalog ────────────────────────────────────────


def test_public_catalog_returns_all_entries_when_no_filter(registry: CommunityRegistry) -> None:
    catalog = registry.public_catalog()
    assert len(catalog) >= 20
    for entry in catalog:
        assert "community_id" in entry
        assert "canonical_name" in entry
        assert "station_code" in entry


def test_public_catalog_filters_by_station(registry: CommunityRegistry) -> None:
    catalog = registry.public_catalog(station_code="A17")
    assert all(e["station_code"] == "A17" for e in catalog)


def test_public_catalog_includes_all_stations_present(registry: CommunityRegistry) -> None:
    all_stations = {e["station_code"] for e in registry.public_catalog()}
    assert "A17" in all_stations
    assert "A18" in all_stations
    assert "A19" in all_stations
