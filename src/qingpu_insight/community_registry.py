"""Curated registry of Qingpu communities with deterministic matching."""

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pandas as pd

_PUNCTUATION = re.compile(r"[\s\-_()（）\[\]{}【】「」『』,，;；:：.。、]+")
_VALID_STATIONS = frozenset({"A17", "A18", "A19"})
_DEFAULT_RADIUS_M = 500.0
_DEFAULT_YEAR_TOLERANCE = 5


@dataclass(frozen=True)
class CommunityMatch:
    community_id: str | None
    canonical_name: str | None
    method: Literal["canonical", "alias", "address", "coordinate", "unknown"]
    confidence: Literal["high", "medium", "none"]


def _normalize(text: str) -> str:
    return _PUNCTUATION.sub("", text.strip().lower())


def _compute_version(data: pd.DataFrame) -> str:
    sorted_data = data.sort_values("community_id", ignore_index=True)
    canonical = sorted_data.to_csv(index=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate(data: pd.DataFrame) -> None:
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
    missing = [c for c in required if c not in data.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    dupe_ids = data["community_id"][data["community_id"].duplicated(keep=False)].unique()
    if len(dupe_ids):
        raise ValueError(f"Duplicate community_id values: {list(dupe_ids)}")

    alias_map: dict[str, list[str]] = {}
    for _, row in data.iterrows():
        raw = str(row.get("aliases", ""))
        for alias in raw.split(";"):
            alias = alias.strip()
            if alias:
                norm = _normalize(alias)
                alias_map.setdefault(norm, []).append(row["community_id"])
    dupe_aliases = {a: ids for a, ids in alias_map.items() if len(ids) > 1}
    if dupe_aliases:
        details = "; ".join(f"{a}: {', '.join(ids)}" for a, ids in dupe_aliases.items())
        raise ValueError(f"Duplicate normalized aliases: {details}")

    invalid_stations = data[~data["station_code"].isin(_VALID_STATIONS)]
    if not invalid_stations.empty:
        raise ValueError(
            f"Invalid station codes: {invalid_stations['station_code'].unique().tolist()}"
        )

    empty_notes = data[
        data["source_notes"].isna() | (data["source_notes"].astype(str).str.strip() == "")
    ]
    if not empty_notes.empty:
        raise ValueError(f"Missing source_notes for: {empty_notes['community_id'].tolist()}")

    pattern_map: dict[str, list[str]] = {}
    for _, row in data.iterrows():
        raw = str(row.get("address_patterns", ""))
        for pattern in raw.split(";"):
            pattern = pattern.strip()
            if pattern:
                norm = _normalize(pattern)
                pattern_map.setdefault(norm, []).append(row["community_id"])
    ambiguous = {p: ids for p, ids in pattern_map.items() if len(ids) > 1}
    if ambiguous:
        details = "; ".join(f"{p}: {', '.join(ids)}" for p, ids in ambiguous.items())
        raise ValueError(f"Ambiguous address patterns: {details}")

    for _, row in data.iterrows():
        x_raw = row.get("twd97_x")
        y_raw = row.get("twd97_y")
        if pd.isna(x_raw) and pd.isna(y_raw):
            continue
        has_x = not pd.isna(x_raw)
        has_y = not pd.isna(y_raw)
        if has_x and not isinstance(x_raw, (int, float)):
            raise ValueError(
                f"Non-numeric coordinates for {row['community_id']}: ({x_raw}, {y_raw})"
            )
        if has_y and not isinstance(y_raw, (int, float)):
            raise ValueError(
                f"Non-numeric coordinates for {row['community_id']}: ({x_raw}, {y_raw})"
            )
        x = float(x_raw) if has_x else float("nan")
        y = float(y_raw) if has_y else float("nan")
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError(
                f"Non-finite coordinates for {row['community_id']}: ({x}, {y})"
            )

    if len(data) < 20:
        raise ValueError(f"Registry requires at least 20 entries, got {len(data)}")


@dataclass
class CommunityRegistry:
    _data: pd.DataFrame
    _version: str
    _coordinate_radius_m: float = _DEFAULT_RADIUS_M
    _year_tolerance: int = _DEFAULT_YEAR_TOLERANCE

    @classmethod
    def from_csv(cls, path: Path) -> "CommunityRegistry":
        data = pd.read_csv(path)
        for col in ["community_id", "canonical_name", "aliases", "station_code",
                     "address_patterns", "source_notes"]:
            if col in data.columns:
                data[col] = data[col].fillna("").astype(str)
        data["completion_year"] = pd.to_numeric(
            data.get("completion_year", pd.Series(dtype=float)), errors="coerce"
        )
        data["twd97_x"] = pd.to_numeric(
            data.get("twd97_x", pd.Series(dtype=float)), errors="coerce"
        )
        data["twd97_y"] = pd.to_numeric(
            data.get("twd97_y", pd.Series(dtype=float)), errors="coerce"
        )
        _validate(data)
        version = _compute_version(data)
        return cls(_data=data, _version=version)

    @property
    def version(self) -> str:
        return self._version

    def _match_by_canonical_name(self, name: str) -> CommunityMatch | None:
        norm_name = _normalize(name)
        for _, row in self._data.iterrows():
            if _normalize(row["canonical_name"]) == norm_name:
                return CommunityMatch(
                    community_id=row["community_id"],
                    canonical_name=row["canonical_name"],
                    method="canonical",
                    confidence="high",
                )
        return None

    def _match_by_alias(self, name: str) -> CommunityMatch | None:
        norm_name = _normalize(name)
        for _, row in self._data.iterrows():
            aliases = [a.strip() for a in str(row.get("aliases", "")).split(";") if a.strip()]
            for alias in aliases:
                if _normalize(alias) == norm_name:
                    return CommunityMatch(
                        community_id=row["community_id"],
                        canonical_name=row["canonical_name"],
                        method="alias",
                        confidence="high",
                    )
        return None

    def _match_by_address(self, address: str | None) -> CommunityMatch | None:
        if not address:
            return None
        norm_address = _normalize(address)
        matched: list[str] = []
        for _, row in self._data.iterrows():
            raw_patterns = str(row.get("address_patterns", ""))
            patterns = [p.strip() for p in raw_patterns.split(";") if p.strip()]
            for pattern in patterns:
                if _normalize(pattern) in norm_address:
                    matched.append(row["community_id"])
                    break
        if len(matched) == 1:
            row = self._data.loc[self._data["community_id"] == matched[0]].iloc[0]
            return CommunityMatch(
                community_id=row["community_id"],
                canonical_name=row["canonical_name"],
                method="address",
                confidence="medium",
            )
        return None

    def _match_by_coordinate(
        self,
        twd97_x: float | None,
        twd97_y: float | None,
        completion_year: int | None,
    ) -> CommunityMatch | None:
        if twd97_x is None or twd97_y is None:
            return None
        coords = self._data[["twd97_x", "twd97_y"]].apply(pd.to_numeric, errors="coerce")
        valid = self._data[coords["twd97_x"].notna() & coords["twd97_y"].notna()]
        if valid.empty:
            return None
        best_distance = float("inf")
        best_idx: int | None = None
        for idx in valid.index:
            row = valid.loc[idx]
            dist = math.sqrt(
                (float(twd97_x) - float(row["twd97_x"])) ** 2
                + (float(twd97_y) - float(row["twd97_y"])) ** 2
            )
            if dist <= self._coordinate_radius_m:
                cy = row.get("completion_year")
                if (
                    completion_year is not None
                    and cy is not None
                    and not (isinstance(cy, float) and math.isnan(cy))
                ):
                    if abs(float(completion_year) - float(cy)) > self._year_tolerance:
                        continue
                if dist < best_distance:
                    best_distance = dist
                    best_idx = idx
        if best_idx is not None:
            row = valid.loc[best_idx]
            return CommunityMatch(
                community_id=row["community_id"],
                canonical_name=row["canonical_name"],
                method="coordinate",
                confidence="medium",
            )
        return None

    def match_transaction(
        self,
        address: str | None = None,
        twd97_x: float | None = None,
        twd97_y: float | None = None,
        completion_year: int | None = None,
    ) -> CommunityMatch:
        result = self._match_by_address(address)
        if result:
            return result
        result = self._match_by_coordinate(twd97_x, twd97_y, completion_year)
        if result:
            return result
        return CommunityMatch(
            community_id=None, canonical_name=None, method="unknown", confidence="none"
        )

    def match_listing(
        self,
        name: str | None = None,
        address: str | None = None,
        twd97_x: float | None = None,
        twd97_y: float | None = None,
        completion_year: int | None = None,
    ) -> CommunityMatch:
        if name:
            result = self._match_by_canonical_name(name)
            if result:
                return result
            result = self._match_by_alias(name)
            if result:
                return result
        result = self._match_by_address(address)
        if result:
            return result
        result = self._match_by_coordinate(twd97_x, twd97_y, completion_year)
        if result:
            return result
        return CommunityMatch(
            community_id=None, canonical_name=None, method="unknown", confidence="none"
        )

    def public_catalog(self, station_code: str | None = None) -> list[dict[str, str]]:
        subset = self._data
        if station_code is not None:
            subset = subset[subset["station_code"] == station_code]
        result: list[dict[str, str]] = []
        for _, row in subset.iterrows():
            result.append(
                {
                    "community_id": str(row["community_id"]),
                    "canonical_name": str(row["canonical_name"]),
                    "station_code": str(row["station_code"]),
                }
            )
        return result
