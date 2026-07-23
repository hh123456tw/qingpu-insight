from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

import pandas as pd

from qingpu_insight.report_contracts import (
    EvidenceCandidate,
    EvidenceFact,
    EvidencePack,
    ReportRequest,
)

ALLOWLISTED_FACT_KINDS = frozenset({
    "asking_price",
    "unit_price",
    "area",
    "building_age",
    "station_distance",
    "model_interval",
    "nearby_transactions_summary",
    "data_freshness",
    "location_evidence",
})

PII_COLUMNS = frozenset({"phone", "contact", "email", "structured_address"})


class EvidenceRepository(Protocol):
    def current_dataset_version(self) -> str: ...

    def load_candidates(self, candidate_ids: Sequence[str]) -> pd.DataFrame: ...

    def load_market_evidence(self, candidate_ids: Sequence[str]) -> pd.DataFrame: ...


def _compute_fact_id(dataset_version: str, candidate_id: str, kind: str, unit: str) -> str:
    raw = f"{dataset_version}|{candidate_id}|{kind}|{unit}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _strip_pii(df: pd.DataFrame) -> pd.DataFrame:
    to_drop = [c for c in df.columns if c.lower() in PII_COLUMNS]
    if to_drop:
        df = df.drop(columns=to_drop)
    return df


class EvidenceBuilder:
    def __init__(self, repository: EvidenceRepository) -> None:
        self._repository = repository

    def build(self, request: ReportRequest) -> EvidencePack:
        version = self._repository.current_dataset_version()
        candidates_df = self._repository.load_candidates(request.candidate_ids)
        market_df = self._repository.load_market_evidence(request.candidate_ids)

        if "dataset_version" in candidates_df.columns:
            candidates_df = candidates_df[candidates_df["dataset_version"] == version]

        candidates_df = _strip_pii(candidates_df)
        market_df = _strip_pii(market_df)
        candidates_df = candidates_df.sort_values("listing_id").reset_index(drop=True)

        candidate_models: list[EvidenceCandidate] = []
        all_facts: list[EvidenceFact] = []
        limitations: list[str] = []

        for _, row in candidates_df.iterrows():
            cid = str(row["listing_id"])
            candidate = EvidenceCandidate(
                candidate_id=cid,
                listing_type=str(row.get("listing_type", "")),
            )
            candidate_models.append(candidate)
            facts = self._generate_facts(version, cid, row, limitations)
            all_facts.extend(facts)

        candidate_ids_in_pack = {c.candidate_id for c in candidate_models}
        market_df_filtered = market_df[market_df["listing_id"].isin(candidate_ids_in_pack)]
        self._generate_market_facts(version, market_df_filtered, all_facts, limitations)

        seen: set[str] = set()
        unique: list[EvidenceFact] = []
        for f in all_facts:
            if f.fact_id not in seen:
                seen.add(f.fact_id)
                unique.append(f)
        unique.sort(key=lambda f: (f.fact_id, f.kind))

        pack_id = _compute_fact_id(
            version, "|".join(sorted(request.candidate_ids)), "pack", "pack"
        )

        return EvidencePack(
            pack_id=pack_id,
            dataset_version=version,
            generated_at=datetime.now(UTC).isoformat(),
            candidates=tuple(candidate_models),
            facts=tuple(unique),
            limitations=tuple(limitations),
        )

    def _generate_facts(
        self,
        version: str,
        candidate_id: str,
        row: pd.Series,
        limitations: list[str],
    ) -> list[EvidenceFact]:
        facts: list[EvidenceFact] = []
        observed_at = str(row.get("snapshot_at", ""))

        price = row.get("price")
        if price is not None and pd.notna(price):
            facts.append(
                EvidenceFact(
                    fact_id=_compute_fact_id(version, candidate_id, "asking_price", "twd"),
                    kind="asking_price",
                    label="Asking Price",
                    value=str(int(price)),
                    unit="twd",
                    source_type="listing",
                    source_version=version,
                    observed_at=observed_at,
                )
            )
        else:
            limitations.append(f"{candidate_id}: missing asking price")

        unit_price = row.get("price_per_ping")
        if unit_price is not None and pd.notna(unit_price):
            facts.append(
                EvidenceFact(
                    fact_id=_compute_fact_id(version, candidate_id, "unit_price", "twd_per_ping"),
                    kind="unit_price",
                    label="Unit Price",
                    value=str(int(unit_price)),
                    unit="twd_per_ping",
                    source_type="listing",
                    source_version=version,
                    observed_at=observed_at,
                )
            )
        else:
            limitations.append(f"{candidate_id}: missing unit price")

        area = row.get("building_area_ping")
        if area is not None and pd.notna(area):
            facts.append(
                EvidenceFact(
                    fact_id=_compute_fact_id(version, candidate_id, "area", "ping"),
                    kind="area",
                    label="Building Area",
                    value=f"{float(area):.2f}",
                    unit="ping",
                    source_type="listing",
                    source_version=version,
                    observed_at=observed_at,
                )
            )
        else:
            limitations.append(f"{candidate_id}: missing building area")

        age = row.get("building_age_years")
        if age is not None and pd.notna(age):
            facts.append(
                EvidenceFact(
                    fact_id=_compute_fact_id(version, candidate_id, "building_age", "years"),
                    kind="building_age",
                    label="Building Age",
                    value=f"{float(age):.1f}",
                    unit="years",
                    source_type="listing",
                    source_version=version,
                    observed_at=observed_at,
                )
            )
        else:
            limitations.append(f"{candidate_id}: missing building age")

        station = row.get("station_code")
        distance = row.get("station_distance_m")
        if (
            station is not None
            and pd.notna(station)
            and distance is not None
            and pd.notna(distance)
        ):
            facts.append(
                EvidenceFact(
                    fact_id=_compute_fact_id(version, candidate_id, "station_distance", "m"),
                    kind="station_distance",
                    label="Station Distance",
                    value=f"{station} {float(distance):.0f}m",
                    unit="m",
                    source_type="listing",
                    source_version=version,
                    observed_at=observed_at,
                )
            )
        else:
            limitations.append(f"{candidate_id}: missing station or distance")

        self._add_model_interval_fact(version, candidate_id, row, facts, limitations, observed_at)

        if observed_at:
            facts.append(
                EvidenceFact(
                    fact_id=_compute_fact_id(version, candidate_id, "data_freshness", "iso"),
                    kind="data_freshness",
                    label="Data Freshness",
                    value=observed_at,
                    unit="iso",
                    source_type="listing",
                    source_version=version,
                    observed_at=observed_at,
                )
            )

        loc_method = row.get("location_method")
        if loc_method is not None and pd.notna(loc_method):
            facts.append(
                EvidenceFact(
                    fact_id=_compute_fact_id(version, candidate_id, "location_evidence", "method"),
                    kind="location_evidence",
                    label="Location Method",
                    value=str(loc_method),
                    unit="method",
                    source_type="listing",
                    source_version=version,
                    observed_at=observed_at,
                )
            )
        else:
            limitations.append(f"{candidate_id}: missing location method")

        return facts

    def _add_model_interval_fact(
        self,
        version: str,
        candidate_id: str,
        row: pd.Series,
        facts: list[EvidenceFact],
        limitations: list[str],
        observed_at: str,
    ) -> None:
        val_low = row.get("valuation_low")
        val_high = row.get("valuation_high")
        if (
            val_low is not None and pd.notna(val_low)
            and val_high is not None and pd.notna(val_high)
        ):
            facts.append(
                EvidenceFact(
                    fact_id=_compute_fact_id(version, candidate_id, "model_interval", "twd"),  # noqa: E501
                    kind="model_interval",
                    label="Model Valuation Interval",
                    value=f"{int(val_low)}-{int(val_high)}",
                    unit="twd",
                    source_type="valuation",
                    source_version=version,
                    observed_at=observed_at,
                )
            )
            return

        model_ev = row.get("model_evidence")
        if model_ev is not None and pd.notna(model_ev) and model_ev:
            try:
                ev = json.loads(model_ev) if isinstance(model_ev, str) else model_ev
                low = ev.get("low") or ev.get("min")
                high = ev.get("high") or ev.get("max")
                if low is not None and high is not None:
                    facts.append(
                        EvidenceFact(
                            fact_id=_compute_fact_id(version, candidate_id, "model_interval", "twd"),  # noqa: E501
                            kind="model_interval",
                            label="Model Valuation Interval",
                            value=f"{int(low)}-{int(high)}",
                            unit="twd",
                            source_type="valuation",
                            source_version=version,
                            observed_at=observed_at,
                        )
                    )
                else:
                    limitations.append(f"{candidate_id}: model evidence incomplete")
            except (json.JSONDecodeError, TypeError, ValueError):
                limitations.append(f"{candidate_id}: model evidence unparseable")
        else:
            limitations.append(f"{candidate_id}: missing model valuation")

    def _generate_market_facts(
        self,
        version: str,
        market_df: pd.DataFrame,
        facts: list[EvidenceFact],
        limitations: list[str],
    ) -> None:
        if market_df.empty:
            return

        now_iso = datetime.now(UTC).isoformat()
        for listing_id in market_df["listing_id"].unique():
            subset = market_df[market_df["listing_id"] == listing_id]
            kind = "nearby_transactions_summary"
            value = f"{len(subset)} transactions"
            facts.append(
                EvidenceFact(
                    fact_id=_compute_fact_id(version, str(listing_id), kind, "count"),
                    kind=kind,
                    label="Nearby Official Transactions",
                    value=value,
                    unit="count",
                    source_type="market_transactions",
                    source_version=version,
                    observed_at=now_iso,
                )
            )
