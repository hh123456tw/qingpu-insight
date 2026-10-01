"""低估物件雷達: which current 591 sale listings ask clearly less than the model expects.

Data flow::

    latest sale listings (listing_current, else the listing_snapshots.parquet export)
      -> select_candidates (active, A17–A19, valid 591 sale URL, newest first, capped)
      -> ListingRadarRunner: one detail page per listing through the assistant's
         DetailPageBrowser (3–6 s random delay, stop on verification, skip delisted,
         reuse captures newer than ``refresh_hours``)
      -> conversation_valuation.valuate_listing_with_context (the assistant's path)
      -> assess_record / rank_records (gap, interval position, quality gates, flags)
      -> ListingRadarStore (Parquet under data/processed/listing_radar) + report

"Low against the model" is not "a bargain": the model cannot see renovation, light,
views, or undisclosed defects, so every surface that shows a ranking says so.
"""

from __future__ import annotations

import dataclasses
import json
import math
import random
import re
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from qingpu_insight.conversation_listing_capture import CapturedListing
from qingpu_insight.conversation_listing_parser import (
    ListingDelisted,
    ListingPageVerificationRequired,
    scrub_contact_text,
)
from qingpu_insight.conversation_urls import (
    Initial591Url,
    Unsupported591Url,
    parse_initial_591_url,
)

RADAR_STATIONS = ("A17", "A18", "A19")
DEFAULT_MAX_LISTINGS = 60
MAX_LISTINGS_LIMIT = 200
DEFAULT_REFRESH_HOURS = 24.0
DEFAULT_DELAY_SECONDS = (3.0, 6.0)
DEFAULT_RADIUS_M = 2_000.0
MAX_CONSECUTIVE_FAILURES = 3
MIN_NET_AREA_PING = 8.0
MAX_NET_AREA_PING = 120.0
NEW_PROJECT_MAX_AGE_YEARS = 2.0
WIDE_INTERVAL_RATIO = 0.6
RADAR_JOB_TYPE = "listing_radar"

_BATCH_ID = re.compile(r"radar-\d{8}T\d{6}Z-[0-9a-f]{8}\Z")
_LISTING_ID = re.compile(r"[0-9A-Za-z_-]{1,64}\Z")
# Detail fields kept in the capture cache: what the valuation and the radar page need.
# Address, builder and community names are dropped; free text is contact-scrubbed again.
_CACHED_DETAIL_FIELDS = (
    "listing_type",
    "source_listing_id",
    "title",
    "total_price_twd",
    "unit_price_twd_per_ping",
    "area_ping",
    "layout",
    "building_type",
    "floor",
    "total_floors",
    "age_years",
    "parking_type",
    "latitude",
    "longitude",
    "main_building_area_ping",
    "auxiliary_building_area_ping",
    "common_area_ping",
    "listed_common_area_percent",
)
_TEXT_FIELDS = ("title", "layout", "building_type", "floor", "parking_type")
_LIST_COLUMNS = ("confidence_reasons", "limitations", "flags", "ineligible_reasons")

RADAR_CAVEATS = (
    "「開價低於模型估值」不等於「便宜」。開價偏低可能反映未揭露的瑕疵，例如頂樓加蓋、"
    "凶宅、海砂屋、持分產權、地上權，或急售、議價空間等原因。",
    "模型只看得到坪數、格局、屋齡、樓層、位置、車位與公設比，看不到裝潢、採光、景觀、"
    "座向、噪音與屋況。",
    "估值與 90% 區間來自與「591 物件分析」相同的模型與換算；區間外仍有約一成成交。",
    "本頁僅供研究參考，不構成買賣建議；請自行查證謄本、實價登錄與現場屋況。",
)

PRICE_ANCHOR_LABELS = {
    "same_building": "同棟成交錨定",
    "nearby_sales": "鄰近成交錨定",
    "station_baseline": "站點基準",
}
COMMON_AREA_SOURCE_LABELS = {
    "591_areas": "591 主建物／附屬／共用坪數換算",
    "591_listed": "591 標示公設比",
}
FLAG_LABELS = {
    "new_project": "新成屋（屋齡未滿 2 年）",
    "presale_anchor": "主要依同棟預售／完工前成交推估",
    "fallback_valuation": "使用降級估價",
    "common_area_unused": "未使用公設比",
    "parking_unverified": "車位坪數無法確認",
    "wide_interval": "估價區間偏寬",
}


class CaptureFn(Protocol):
    def __call__(self, initial: Initial591Url) -> CapturedListing: ...


ValuateFn = Callable[[dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]]
ProgressFn = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class RadarCandidate:
    source_listing_id: str
    source_url: str
    snapshot_at: str | None = None


@dataclass(frozen=True)
class ListingRadarRequest:
    max_listings: int = DEFAULT_MAX_LISTINGS
    refresh_hours: float = DEFAULT_REFRESH_HOURS
    trigger: str = "manual"

    def __post_init__(self) -> None:
        if type(self.max_listings) is not int or not 1 <= self.max_listings <= MAX_LISTINGS_LIMIT:
            raise ValueError(f"max_listings must be an integer from 1 to {MAX_LISTINGS_LIMIT}")
        if (
            isinstance(self.refresh_hours, bool)
            or not isinstance(self.refresh_hours, int | float)
            or not 0 <= float(self.refresh_hours) <= 24 * 14
        ):
            raise ValueError("refresh_hours must be between 0 and 336")
        if self.trigger not in {"manual", "scheduled", "web"}:
            raise ValueError("unsupported trigger")


@dataclass
class RadarRunResult:
    radar_batch_id: str
    status: str
    started_at: str
    finished_at: str
    records: list[dict[str, Any]]
    counts: dict[str, int]
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def candidates(self) -> list[dict[str, Any]]:
        ranked = [r for r in self.records if r.get("rank") is not None]
        return sorted(ranked, key=lambda r: r["rank"])


# --------------------------------------------------------------------------- candidates


def load_candidate_frame(repository: Any, snapshots_export: Path) -> tuple[pd.DataFrame, str]:
    """Latest sale listings: the published current table, else the snapshot export."""
    if repository is not None:
        try:
            current = repository.load_current("sale")
        except Exception:
            current = pd.DataFrame()
        if current is not None and not current.empty:
            return current, "listing_current"
    if snapshots_export.exists():
        frame = pd.read_parquet(snapshots_export)
        return frame, snapshots_export.name
    return pd.DataFrame(), "none"


def _sale_url(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        initial = parse_initial_591_url(value.strip())
    except (Unsupported591Url, ValueError):
        return None
    return initial.request_url if "sale.591.com.tw" in initial.request_url else None


def select_candidates(frame: pd.DataFrame, max_listings: int) -> list[RadarCandidate]:
    """Active sale listings in (or not yet known to be outside) A17–A19, newest first."""
    if frame is None or frame.empty or "source_listing_id" not in frame:
        return []
    rows = frame.copy()
    if "snapshot_at" not in rows:
        rows["snapshot_at"] = None
    if "listing_type" in rows:
        rows = rows[rows["listing_type"] == "sale"]
    if "active" in rows:
        rows = rows[rows["active"].fillna(False).astype(bool)]
    elif "batch_id" in rows and not rows.empty:
        stamps = pd.to_datetime(rows["snapshot_at"], errors="coerce", utc=True)
        latest_batch = rows.assign(_stamp=stamps).sort_values("_stamp").iloc[-1]["batch_id"]
        rows = rows[rows["batch_id"] == latest_batch]
    if "station_code" in rows:
        station = rows["station_code"].astype("object")
        rows = rows[station.isna() | station.isin(RADAR_STATIONS)]
    rows = rows.assign(
        _stamp=pd.to_datetime(rows["snapshot_at"], errors="coerce", utc=True),
        _id=rows["source_listing_id"].astype(str),
    ).sort_values(["_stamp", "_id"], ascending=[False, True], na_position="last")

    candidates: list[RadarCandidate] = []
    seen: set[str] = set()
    for _, row in rows.iterrows():
        listing_id = str(row["_id"])
        url = _sale_url(row.get("source_url"))
        if listing_id in seen or url is None or not _LISTING_ID.fullmatch(listing_id):
            continue
        seen.add(listing_id)
        stamp = row["_stamp"]
        candidates.append(
            RadarCandidate(
                source_listing_id=listing_id,
                source_url=url,
                snapshot_at=stamp.isoformat() if pd.notna(stamp) else None,
            )
        )
        if len(candidates) >= max_listings:
            break
    return candidates


# --------------------------------------------------------------------------- storage


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def cached_detail_payload(captured: CapturedListing) -> dict[str, Any]:
    detail = dataclasses.asdict(captured.detail)
    payload = {key: _json_value(detail.get(key)) for key in _CACHED_DETAIL_FIELDS}
    for key in _TEXT_FIELDS:
        if isinstance(payload.get(key), str):
            payload[key] = scrub_contact_text(payload[key])
    return payload


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _atomic_write_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    frame.to_parquet(tmp, index=False)
    tmp.replace(path)


class ListingRadarStore:
    """Parquet files under ``data/processed/listing_radar``; no MySQL needed.

    ``captures.parquet`` caches each listing's last detail capture (scrubbed); each run
    writes ``runs/<radar_batch_id>.parquet`` + ``.json``, then ``latest.json`` points at it.
    """

    _CAPTURE_COLUMNS = ("source_listing_id", "captured_at", "status", "final_url", "payload_json")

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)

    @property
    def captures_path(self) -> Path:
        return self.base_dir / "captures.parquet"

    def load_captures(self) -> pd.DataFrame:
        try:
            return pd.read_parquet(self.captures_path)
        except (FileNotFoundError, ValueError, OSError):
            return pd.DataFrame(columns=list(self._CAPTURE_COLUMNS))

    def capture_index(self) -> dict[str, dict[str, Any]]:
        frame = self.load_captures()
        return {str(row["source_listing_id"]): dict(row) for _, row in frame.iterrows()}

    def write_captures(self, index: dict[str, dict[str, Any]]) -> None:
        frame = pd.DataFrame(list(index.values()), columns=list(self._CAPTURE_COLUMNS))
        _atomic_write_parquet(self.captures_path, frame)

    def save_run(self, result: RadarRunResult) -> None:
        if not _BATCH_ID.fullmatch(result.radar_batch_id):
            raise ValueError("invalid radar batch id")
        runs = self.base_dir / "runs"
        frame = records_frame(result.records)
        _atomic_write_parquet(runs / f"{result.radar_batch_id}.parquet", frame)
        meta = {
            **result.meta,
            "radar_batch_id": result.radar_batch_id,
            "status": result.status,
            "started_at": result.started_at,
            "finished_at": result.finished_at,
            "counts": result.counts,
        }
        _atomic_write_text(
            runs / f"{result.radar_batch_id}.json", json.dumps(meta, ensure_ascii=False, indent=2)
        )
        if result.counts.get("valued", 0) > 0:
            _atomic_write_text(
                self.base_dir / "latest.json",
                json.dumps({"radar_batch_id": result.radar_batch_id}),
            )

    def load_latest(self) -> tuple[dict[str, Any] | None, pd.DataFrame]:
        try:
            pointer = json.loads((self.base_dir / "latest.json").read_text(encoding="utf-8"))
            batch_id = pointer["radar_batch_id"]
        except (OSError, ValueError, KeyError, TypeError):
            return None, pd.DataFrame()
        if not isinstance(batch_id, str) or not _BATCH_ID.fullmatch(batch_id):
            return None, pd.DataFrame()
        runs = self.base_dir / "runs"
        try:
            meta = json.loads((runs / f"{batch_id}.json").read_text(encoding="utf-8"))
            frame = pd.read_parquet(runs / f"{batch_id}.parquet")
        except (OSError, ValueError):
            return None, pd.DataFrame()
        for column in _LIST_COLUMNS:
            if column in frame:
                frame[column] = frame[column].map(_decode_list)
        return meta, frame


def _decode_list(value: Any) -> list[Any]:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            return []
        return decoded if isinstance(decoded, list) else []
    return []


def records_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for record in records:
        row = dict(record)
        for column in _LIST_COLUMNS:
            row[column] = json.dumps(row.get(column) or [], ensure_ascii=False)
        for key in _TEXT_FIELDS:
            if isinstance(row.get(key), str):
                row[key] = scrub_contact_text(row[key])
        rows.append(row)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- ranking


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def assess_record(record: dict[str, Any], *, radius_m: float = DEFAULT_RADIUS_M) -> dict[str, Any]:
    """Gap, interval position, quality gates and flags for one valued listing.

    ``score`` is how far the asking price sits below the estimate in units of the
    interval's lower log-radius: ``ln(estimate / asking) / ln(estimate / low)``. It is
    1.0 exactly at the 90% interval low and above 1 when the asking price is below it, so
    a 15% gap in a tight interval outranks a 15% gap in a wide one.
    """
    out = dict(record)
    asking = _finite(record.get("asking_price_twd"))
    estimate = _finite(record.get("estimate_twd"))
    low = _finite(record.get("interval_low_twd"))
    high = _finite(record.get("interval_high_twd"))

    gap = score = width = None
    below = False
    if asking and estimate and asking > 0 and estimate > 0:
        gap = (asking - estimate) / estimate
        if low and 0 < low < estimate:
            score = math.log(estimate / asking) / math.log(estimate / low)
            below = asking < low
        if low and high and high > low:
            width = (high - low) / estimate
    out.update(gap_pct=gap, score=score, below_interval=below, interval_width_ratio=width)

    reasons: list[str] = []
    if record.get("listing_type", "sale") != "sale":
        reasons.append("not_sale")
    if asking is None or asking <= 0:
        reasons.append("missing_asking_price")
    if estimate is None or score is None:
        reasons.append("missing_estimate")
    if not record.get("has_coordinates"):
        reasons.append("missing_coordinates")
    if not record.get("layout"):
        reasons.append("missing_layout")
    if record.get("age_years") is None:
        reasons.append("missing_age")
    if record.get("confidence") not in {"high", "medium"}:
        reasons.append("low_confidence")
    net_area = _finite(record.get("net_area_ping"))
    if net_area is None or not MIN_NET_AREA_PING <= net_area <= MAX_NET_AREA_PING:
        reasons.append("area_out_of_range")
    if record.get("degraded"):
        reasons.append("fallback_valuation")
    distance = _finite(record.get("station_distance_m"))
    if distance is None or distance > radius_m:
        reasons.append("out_of_area")

    flags: list[str] = []
    age = _finite(record.get("age_years"))
    if age is not None and age < NEW_PROJECT_MAX_AGE_YEARS:
        flags.append("new_project")
    if any(str(r).startswith("新建案") for r in record.get("confidence_reasons") or []):
        flags.append("presale_anchor")
    if record.get("degraded"):
        flags.append("fallback_valuation")
    if not record.get("common_area_source"):
        flags.append("common_area_unused")
    if record.get("parking_unverified"):
        flags.append("parking_unverified")
    if width is not None and width > WIDE_INTERVAL_RATIO:
        flags.append("wide_interval")

    out["ineligible_reasons"] = reasons
    out["eligible"] = not reasons
    out["flags"] = flags
    out["reason"] = _reason_text(out)
    return out


def _reason_text(record: dict[str, Any]) -> str:
    gap = record.get("gap_pct")
    if gap is None:
        return "無法比較開價與估值"
    parts: list[str] = []
    if record.get("below_interval"):
        low = _finite(record.get("interval_low_twd")) or 0
        asking = _finite(record.get("asking_price_twd")) or 0
        below_low = (low - asking) / low if low else 0
        parts.append(f"明顯低於區間：開價比 90% 區間下限低 {below_low:.1%}（比估值低 {-gap:.1%}）")
    elif gap < 0:
        parts.append(f"開價比估值低 {-gap:.1%}，仍在 90% 區間內")
    else:
        parts.append(f"開價比估值高 {gap:.1%}")
    anchor = PRICE_ANCHOR_LABELS.get(str(record.get("price_anchor")))
    if anchor:
        parts.append(anchor)
    flag_text = [FLAG_LABELS[f] for f in record.get("flags", []) if f in FLAG_LABELS]
    if flag_text:
        parts.append("注意：" + "、".join(flag_text))
    return "；".join(parts)


def rank_records(
    records: Iterable[dict[str, Any]], *, radius_m: float = DEFAULT_RADIUS_M
) -> list[dict[str, Any]]:
    """Rank eligible listings that ask less than the estimate, most clearly low first."""
    assessed = []
    for record in records:
        if record.get("status") == "valued":
            assessed.append(assess_record(record, radius_m=radius_m))
        else:
            assessed.append({**record, "rank": None, "eligible": False})
    ranked = sorted(
        (
            r
            for r in assessed
            if r.get("eligible") and r.get("score") is not None and r["score"] > 0
        ),
        key=lambda r: (-r["score"], r["gap_pct"], str(r.get("source_listing_id"))),
    )
    order = {id(r): position for position, r in enumerate(ranked, start=1)}
    for record in assessed:
        record["rank"] = order.get(id(record))
    return assessed


# --------------------------------------------------------------------------- runner


def new_radar_batch_id(now: datetime) -> str:
    return f"radar-{now.astimezone(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class ListingRadarRunner:
    """Capture, value and rank candidates; persist whatever finished, even when stopped."""

    def __init__(
        self,
        *,
        store: ListingRadarStore,
        capture: CaptureFn | None,
        valuate: ValuateFn,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        delay_seconds: tuple[float, float] = DEFAULT_DELAY_SECONDS,
        refresh_hours: float = DEFAULT_REFRESH_HOURS,
        max_consecutive_failures: int = MAX_CONSECUTIVE_FAILURES,
        radius_m: float = DEFAULT_RADIUS_M,
    ) -> None:
        if delay_seconds[0] < 0 or delay_seconds[0] > delay_seconds[1]:
            raise ValueError("invalid delay range")
        self._store = store
        self._capture = capture
        self._valuate = valuate
        self._clock = clock
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._delay = delay_seconds
        self._refresh = timedelta(hours=refresh_hours)
        self._max_failures = max_consecutive_failures
        self._radius_m = radius_m

    def run(
        self,
        candidates: list[RadarCandidate],
        *,
        max_listings: int = DEFAULT_MAX_LISTINGS,
        source: str | None = None,
        progress: ProgressFn | None = None,
    ) -> RadarRunResult:
        started = self._clock()
        batch_id = new_radar_batch_id(started)
        selected = candidates[:max_listings]
        captures = self._store.capture_index()
        counts: Counter[str] = Counter(candidates=len(selected))
        records: list[dict[str, Any]] = []
        status = "completed"
        live_fetches = 0
        consecutive_failures = 0

        for position, candidate in enumerate(selected, start=1):
            cached = captures.get(candidate.source_listing_id)
            cached_at = _parse_time(cached.get("captured_at")) if cached else None
            fresh = cached_at is not None and self._clock() - cached_at < self._refresh
            if fresh:
                counts["from_cache"] += 1
                record = self._from_capture_row(candidate, cached, source="cache")
            elif self._capture is None:
                record = self._skip(candidate, "not_cached", "離線模式且無近期擷取")
            else:
                if live_fetches:
                    self._sleep(self._rng.uniform(*self._delay))
                live_fetches += 1
                outcome = self._fetch(candidate)
                if outcome == "verification":
                    status = "stopped_verification"
                    counts["verification_required"] += 1
                    break
                row, failed = outcome
                captures[candidate.source_listing_id] = row
                self._store.write_captures(captures)
                counts["captured_live"] += 1
                consecutive_failures = consecutive_failures + 1 if failed else 0
                record = self._from_capture_row(candidate, row, source="live")
            counts[record["status"]] += 1
            records.append(record)
            if progress is not None:
                progress(
                    {
                        "stage": "capturing",
                        "processed": position,
                        "total": len(selected),
                        "valued": counts["valued"],
                        "captured_live": counts["captured_live"],
                        "from_cache": counts["from_cache"],
                    }
                )
            if consecutive_failures >= self._max_failures:
                status = "stopped_failures"
                break

        ranked = rank_records(records, radius_m=self._radius_m)
        counts["ranked"] = sum(1 for r in ranked if r.get("rank") is not None)
        counts["below_interval"] = sum(
            1 for r in ranked if r.get("rank") is not None and r.get("below_interval")
        )
        finished = self._clock()
        valued = [r for r in ranked if r.get("status") == "valued"]
        result = RadarRunResult(
            radar_batch_id=batch_id,
            status=status,
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
            records=[{**r, "radar_batch_id": batch_id, "generated_at": finished.isoformat()}
                     for r in ranked],
            counts=dict(counts),
            meta={
                "source": source,
                "model_versions": sorted({str(r.get("model_version")) for r in valued}),
                "dataset_versions": sorted({str(r.get("dataset_version")) for r in valued}),
                "refresh_hours": self._refresh.total_seconds() / 3600,
                "delay_seconds": list(self._delay),
                "radius_m": self._radius_m,
                "caveats": list(RADAR_CAVEATS),
            },
        )
        self._store.save_run(result)
        return result

    def _fetch(self, candidate: RadarCandidate):
        now = self._clock().isoformat()
        base = {
            "source_listing_id": candidate.source_listing_id,
            "captured_at": now,
            "final_url": candidate.source_url,
            "payload_json": None,
        }
        try:
            initial = parse_initial_591_url(candidate.source_url)
            assert self._capture is not None
            captured = self._capture(initial)
        except ListingPageVerificationRequired:
            return "verification"
        except ListingDelisted:
            return {**base, "status": "delisted"}, False
        except Exception as error:  # timeouts, parse errors, driver crashes, odd redirects
            return {**base, "status": f"capture_failed:{type(error).__name__}"}, True
        payload = cached_detail_payload(captured)
        return {
            **base,
            "status": "captured",
            "final_url": captured.final_url,
            "payload_json": json.dumps(payload, ensure_ascii=False),
        }, False

    def _skip(self, candidate: RadarCandidate, status: str, reason: str) -> dict[str, Any]:
        return {
            "source_listing_id": candidate.source_listing_id,
            "url": candidate.source_url,
            "status": status,
            "status_reason": reason,
        }

    def _from_capture_row(
        self, candidate: RadarCandidate, row: dict[str, Any], *, source: str
    ) -> dict[str, Any]:
        capture_status = str(row.get("status") or "")
        if capture_status == "delisted":
            return self._skip(candidate, "delisted", "591 顯示物件已下架或不存在")
        if capture_status != "captured" or not row.get("payload_json"):
            reason = capture_status.partition(":")[2] or "capture_failed"
            return self._skip(candidate, "capture_failed", reason)
        payload = json.loads(row["payload_json"])
        record = {
            "source_listing_id": candidate.source_listing_id,
            "url": row.get("final_url") or candidate.source_url,
            "captured_at": row.get("captured_at"),
            "capture_source": source,
            "listing_type": payload.get("listing_type"),
            "title": payload.get("title"),
            "layout": payload.get("layout"),
            "floor": payload.get("floor"),
            "building_type": payload.get("building_type"),
            "area_ping": _finite(payload.get("area_ping")),
            "age_years": _finite(payload.get("age_years")),
            "asking_price_twd": payload.get("total_price_twd"),
            "has_coordinates": payload.get("latitude") is not None
            and payload.get("longitude") is not None,
        }
        if payload.get("listing_type") != "sale":
            return {**record, "status": "not_sale", "status_reason": "僅支援中古屋"}
        try:
            public, context = self._valuate(payload)
        except Exception as error:
            message = str(error) if isinstance(error, ValueError) else type(error).__name__
            return {**record, "status": "valuation_failed",
                    "status_reason": scrub_contact_text(message[:200])}
        common_area = public.get("common_area") or {}
        record.update(
            status="valued",
            status_reason=None,
            station_code=context.get("station_code"),
            station_distance_m=_finite(context.get("station_distance_m")),
            net_area_ping=_finite(context.get("net_area_ping")),
            parking_area_ping=_finite(context.get("parking_area_ping")),
            parking_unverified=bool(context.get("parking_unverified")),
            price_anchor=context.get("price_anchor"),
            degraded=bool(context.get("degraded")),
            estimate_twd=public.get("point_estimate_twd"),
            interval_low_twd=public.get("low_estimate_twd"),
            interval_high_twd=public.get("high_estimate_twd"),
            confidence=public.get("confidence"),
            confidence_reasons=list(public.get("confidence_reasons") or []),
            limitations=list(public.get("limitations") or []),
            common_area_source=common_area.get("source") if common_area.get("provided") else None,
            common_area_ratio=_finite(common_area.get("ratio")),
            model_version=public.get("model_version"),
            dataset_version=public.get("dataset_version"),
        )
        if (
            record["station_distance_m"] is not None
            and record["station_distance_m"] > self._radius_m
        ):
            record.update(status="out_of_area", status_reason="不在 A17–A19 生活圈範圍內")
        return record


# --------------------------------------------------------------------------- report


_STATUS_LABELS = {
    "valued": "完成估價",
    "delisted": "已下架",
    "capture_failed": "擷取失敗",
    "valuation_failed": "無法估價",
    "out_of_area": "不在生活圈",
    "not_sale": "非中古屋",
    "not_cached": "離線且無擷取",
}
_RUN_STATUS_LABELS = {
    "completed": "完成",
    "stopped_verification": "遇到 591 驗證頁，已停止（已完成的物件已保存）",
    "stopped_failures": "連續擷取失敗，已停止（已完成的物件已保存）",
}


def public_candidate(record: dict[str, Any]) -> dict[str, Any]:
    """Fields safe and useful to show for a ranked listing."""
    keys = (
        "rank",
        "source_listing_id",
        "url",
        "title",
        "station_code",
        "station_distance_m",
        "area_ping",
        "net_area_ping",
        "layout",
        "floor",
        "age_years",
        "asking_price_twd",
        "estimate_twd",
        "interval_low_twd",
        "interval_high_twd",
        "gap_pct",
        "below_interval",
        "score",
        "price_anchor",
        "common_area_source",
        "confidence",
        "flags",
        "reason",
        "captured_at",
        "model_version",
    )
    out = {}
    for key in keys:
        value = record.get(key)
        if isinstance(value, float) and not math.isfinite(value):
            value = None
        if hasattr(value, "item"):
            value = value.item()
        if key in _TEXT_FIELDS and isinstance(value, str):
            value = scrub_contact_text(value)
        out[key] = value
    return out


def _wan(value: Any) -> str:
    number = _finite(value)
    return "—" if number is None else f"{number / 10000:,.0f} 萬"


def write_radar_report(output_dir: Path, result: RadarRunResult) -> tuple[Path, Path]:
    """``<id>.json`` and ``<id>.md`` under ``output_dir`` (outputs/listing-radar)."""
    candidates = [public_candidate(r) for r in result.candidates]
    excluded = Counter(
        reason
        for r in result.records
        if r.get("status") == "valued" and not r.get("eligible")
        for reason in r.get("ineligible_reasons") or []
    )
    report = {
        "radar_batch_id": result.radar_batch_id,
        "status": result.status,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "counts": result.counts,
        "excluded_by_quality_gate": dict(excluded),
        "model_versions": result.meta.get("model_versions", []),
        "dataset_versions": result.meta.get("dataset_versions", []),
        "ranking_rule": (
            "score = ln(估值/開價) ÷ ln(估值/區間下限)；≥1 代表開價低於 90% 區間下限。"
            "僅排名資料完整（座標、格局、屋齡、坪數合理）、信心度非低、非降級估價、"
            "且開價低於估值的中古屋。"
        ),
        "caveats": list(RADAR_CAVEATS),
        "candidates": candidates,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{result.radar_batch_id}.json"
    _atomic_write_text(json_path, json.dumps(report, ensure_ascii=False, indent=2, default=str))

    lines = [
        f"# 低估物件雷達 {result.radar_batch_id}",
        "",
        f"- 狀態：{_RUN_STATUS_LABELS.get(result.status, result.status)}",
        f"- 期間：{result.started_at} → {result.finished_at}",
        f"- 模型版本：{', '.join(report['model_versions']) or '—'}",
        "- 物件：" + "，".join(
            f"{_STATUS_LABELS.get(k, k)} {v}"
            for k, v in sorted(result.counts.items())
            if k in _STATUS_LABELS
        ),
        f"- 排名候選：{result.counts.get('ranked', 0)} 筆，"
        f"其中明顯低於區間 {result.counts.get('below_interval', 0)} 筆",
        "",
        "## 排名規則",
        "",
        report["ranking_rule"],
        "",
        "## 請先讀這段",
        "",
        *[f"- {caveat}" for caveat in RADAR_CAVEATS],
        "",
        "## 候選物件",
        "",
        "| # | 站 | 坪數 | 屋齡 | 開價 | 估值 | 90% 區間 | 差距 | 說明 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for item in candidates:
        gap = item.get("gap_pct")
        lines.append(
            "| {rank} | {station} | {area} | {age} | {ask} | {est} | {low}–{high} | {gap} | "
            "[{reason}]({url}) |".format(
                rank=item["rank"],
                station=item.get("station_code") or "—",
                area=f"{item['area_ping']:g}" if item.get("area_ping") else "—",
                age=f"{item['age_years']:g}" if item.get("age_years") is not None else "—",
                ask=_wan(item.get("asking_price_twd")),
                est=_wan(item.get("estimate_twd")),
                low=_wan(item.get("interval_low_twd")),
                high=_wan(item.get("interval_high_twd")),
                gap=f"{gap:+.1%}" if gap is not None else "—",
                reason=str(item.get("reason") or "").replace("|", "／"),
                url=item.get("url"),
            )
        )
    if not candidates:
        lines.append("| — | — | — | — | — | — | — | — | 本批沒有符合條件的候選 |")
    md_path = output_dir / f"{result.radar_batch_id}.md"
    _atomic_write_text(md_path, "\n".join(lines) + "\n")
    return json_path, md_path
