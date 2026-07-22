import argparse
import json
import os
import sys
import urllib.parse
from collections import Counter
from datetime import UTC, datetime
from math import isfinite
from numbers import Real
from pathlib import Path

import joblib
import pandas as pd
import pymysql

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
from qingpu_insight.listing_591 import (
    ListingSchemaError,
    SourceListing,
    extract_rendered_page,
)
from qingpu_insight.listing_capture import ChromeConfig, Selenium591Source, create_chrome
from qingpu_insight.listing_detail_enrichment import (
    DetailEnrichmentBlocked,
    ListingDetailEnricher,
)
from qingpu_insight.listing_events import detect_listing_events
from qingpu_insight.listing_geocoding import (
    DoorplateListingGeocoder,
    GeocodingService,
    MySQLGeocodeCache,
)
from qingpu_insight.listing_location import assign_listing_life_circle
from qingpu_insight.listing_normalization import NormalizedListing, normalize_listing
from qingpu_insight.listing_repository import (
    ListingRepository,
    MySQLListingRepository,
    ParquetListingRepository,
    valid_listing_batch_id,
)
from qingpu_insight.listing_sources import CaptureBatch, ListingSource
from qingpu_insight.listing_valuation import compare_listing_to_model
from qingpu_insight.location_evidence import LocationEvidence
from qingpu_insight.market_cleaning import build_market_dataset
from qingpu_insight.model_features import build_model_frame
from qingpu_insight.model_training import (
    CandidateEvaluation,
    RecentMedianBaseline,
    candidate_estimators,
    evaluate_candidate,
    leakage_audit,
    metric_rows,
    select_release_candidate,
    split_by_time,
)
from qingpu_insight.moi import read_moi_csv
from qingpu_insight.mysql_loader import load_market_rows
from qingpu_insight.reporting import write_report
from qingpu_insight.valuation import ModelRegistry, ValuationBundle, train_artifact
from qingpu_insight.valuation_reporting import write_evaluation, write_model_card

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
    manifest = settings.raw_dir / "manifest.json"
    errors: list[str] = []
    for season in iter_seasons(start, end):
        archive = settings.raw_dir / "seasons" / f"{season}.zip"
        try:
            record = download_season(settings.sources.moi_base_url, season, archive)
            write_manifest([record], manifest)
            extract_taoyuan_tables(archive, settings.raw_dir / "seasons" / season)
        except Exception as error:
            errors.append(f"{season}: {error}")
    current = settings.raw_dir / "current"
    for name in ("h_lvr_land_a.csv", "h_lvr_land_b.csv"):
        try:
            record = download_current_table(settings.sources.moi_base_url, name, current / name)
            write_manifest([record], manifest)
        except Exception as error:
            errors.append(f"{name}: {error}")
    try:
        record = download_file(settings.sources.doorplate_url, settings.raw_dir / "doorplates.csv")
        write_manifest([record], manifest)
    except Exception as error:
        errors.append(f"doorplates.csv: {error}")
    if errors:
        raise RuntimeError("acquisition incomplete: " + "; ".join(errors))


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


def market_build(root: Path, input_path: str, output_path: str, quality_output_path: str) -> int:
    frame = pd.read_parquet(root / input_path)
    clean, quality = build_market_dataset(frame)
    output_resolved = root / output_path
    quality_resolved = root / quality_output_path
    output_resolved.parent.mkdir(parents=True, exist_ok=True)
    quality_resolved.parent.mkdir(parents=True, exist_ok=True)
    clean.to_parquet(output_resolved, index=False)
    quality_resolved.write_text(
        json.dumps(quality.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return 0


def mysql_load(root: Path, input_path: str) -> int:
    url = os.environ.get("QINGPU_DATABASE_URL")
    if url is None:
        raise RuntimeError("QINGPU_DATABASE_URL is required")
    frame = pd.read_parquet(root / input_path)
    parsed = urllib.parse.urlparse(url)
    connection = pymysql.connect(
        host=parsed.hostname or "localhost",
        port=parsed.port or 3306,
        user=parsed.username or "",
        password=parsed.password or "",
        database=parsed.path.lstrip("/"),
        charset="utf8mb4",
    )
    try:
        n = load_market_rows(connection, frame)
    finally:
        connection.close()
    print(f"Loaded {n} market rows into MySQL.")
    return 0


def model_train(root: Path, input_path: str, artifact_dir: str, report_dir: str) -> int:
    frame = pd.read_parquet(root / input_path)
    artifact_path = root / artifact_dir
    report_path = root / report_dir

    for transaction_type in ("resale", "presale"):
        print(f"Training {transaction_type} model...")
        mf = build_model_frame(frame, transaction_type)
        split = split_by_time(mf)

        baseline = RecentMedianBaseline().fit(split.train)
        baseline_pred = baseline.predict(split.test)
        baseline_actual = split.test["target_unit_price_twd"].values
        baseline_metrics = metric_rows(baseline_actual, baseline_pred, split.test)
        baseline_mae = float(baseline_metrics.loc["overall", "mae"])
        baseline_station_mape = {
            idx.split(":", 1)[1]: float(row["mape"])
            for idx, row in baseline_metrics.iterrows()
            if idx.startswith("station:")
        }
        baseline_eval = CandidateEvaluation(
            name="baseline",
            estimator=baseline,
            overall_mae=baseline_mae,
            station_mape=baseline_station_mape,
            metrics=baseline_metrics,
        )

        candidates = [baseline_eval]
        for name, estimator in candidate_estimators().items():
            candidates.append(evaluate_candidate(name, estimator, split))

        selected = select_release_candidate(candidates)
        leakage = leakage_audit(split)

        temp_bundle = ValuationBundle(
            transaction_type=transaction_type,
            model_name="",
            model_version="",
            pipeline=None,
            interval_abs_residual_twd_per_ping=0,
            feature_ranges={},
            feature_hard_ranges={},
            feature_medians={},
            global_importance=[],
            reference_rows=pd.DataFrame(),
            data_min_date="",
            data_max_date=str(split.train["transaction_date"].max().date()),
            metrics={},
        )

        train_artifact(transaction_type, selected, split, temp_bundle, artifact_path)
        bundle: ValuationBundle = joblib.load(artifact_path / f"{transaction_type}.joblib")

        eval_path = write_evaluation(bundle, candidates, split, report_path)
        card_path = write_model_card(bundle, candidates, leakage, report_path)

        artifact_file = artifact_path / f"{transaction_type}.joblib"
        print(f"  {transaction_type}: {selected.name} -> {artifact_file}")
        print(f"    evaluation: {eval_path}")
        print(f"    model card: {card_path}")

    return 0


listing_type_choices = ("sale", "newhouse", "rental")


def create_listing_source(
    root: Path, config: ChromeConfig | None = None
) -> ListingSource:
    return Selenium591Source(base_dir=root, config=config or ChromeConfig())


def create_mysql_connection_factory():
    database_url = os.environ.get("QINGPU_DATABASE_URL")
    if not database_url:
        raise ValueError("QINGPU_DATABASE_URL is required for persistent geocoding")
    parsed = urllib.parse.urlparse(database_url)
    if parsed.scheme not in ("mysql", "mysql+pymysql"):
        raise ValueError(
            f"Unsupported scheme: {parsed.scheme!r}; "
            "expected 'mysql' or 'mysql+pymysql'"
        )
    if parsed.query:
        raise ValueError("unsupported database URL query parameters")
    if parsed.fragment:
        raise ValueError("database URL fragment is not supported")
    database = parsed.path.lstrip("/")
    if not database:
        raise ValueError("QINGPU_DATABASE_URL must include a database name")
    kwargs = {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 3306,
        "user": urllib.parse.unquote(parsed.username or ""),
        "password": urllib.parse.unquote(parsed.password or ""),
        "database": database,
        "charset": "utf8mb4",
    }
    return lambda: pymysql.connect(**kwargs)


def create_listing_repository(root: Path) -> ListingRepository:
    if os.environ.get("QINGPU_DATABASE_URL"):
        return MySQLListingRepository(create_mysql_connection_factory()())
    return ParquetListingRepository(root / "data" / "processed")


def _normalized_to_rows(normalized: list[NormalizedListing]) -> list[dict]:
    return [
        {
            "source": n.source,
            "source_listing_id": n.source_listing_id,
            "listing_type": n.listing_type,
            "snapshot_at": n.snapshot_at,
            "source_url": n.source_url,
            "title": n.title,
            "asking_price_twd": n.asking_price_twd,
            "monthly_rent_twd": n.monthly_rent_twd,
            "building_area_ping": n.building_area_ping,
            "asking_unit_price_low_twd_per_ping": n.asking_unit_price_low_twd_per_ping,
            "asking_unit_price_high_twd_per_ping": n.asking_unit_price_high_twd_per_ping,
            "building_area_min_ping": n.building_area_min_ping,
            "building_area_max_ping": n.building_area_max_ping,
            "acquisition_representation": n.acquisition_representation,
            "acquisition_schema_version": n.acquisition_schema_version,
            "building_type": n.building_type,
            "bedrooms": n.bedrooms,
            "living_rooms": n.living_rooms,
            "bathrooms": n.bathrooms,
            "building_age_years": n.building_age_years,
            "floor": n.floor,
            "total_floors": n.total_floors,
            "parking_type": n.parking_type,
            "latitude": n.latitude,
            "longitude": n.longitude,
            "structured_address": n.structured_address,
            "address_source_url": n.address_source_url,
            "address_observed_at": n.address_observed_at,
            "location_method": n.location_method,
            "location_confidence": n.location_confidence,
            "location_reason": n.location_reason,
            "geocoded_at": n.geocoded_at,
            "geocoder_version": n.geocoder_version,
            "raw_hash": n.raw_hash,
        }
        for n in normalized
    ]


def _enrich_rows_with_geocoder(rows: pd.DataFrame, geocoder) -> pd.DataFrame:
    """Apply structured-address evidence only to unresolved rows.

    Source coordinates are immutable provenance: they are intentionally not
    passed to the geocoder even when an address is also available.
    """
    enriched = rows.copy()
    required = {"location_method", "structured_address"}
    if enriched.empty or not required.issubset(enriched.columns):
        return enriched
    candidates = enriched.index[
        enriched["location_method"].eq("unknown")
        & enriched["structured_address"].map(
            lambda address: isinstance(address, str) and bool(address.strip())
        )
    ]
    for index in candidates:
        evidence: LocationEvidence = geocoder.enrich(enriched.loc[index].to_dict())
        enriched.at[index, "latitude"] = evidence.latitude
        enriched.at[index, "longitude"] = evidence.longitude
        enriched.at[index, "location_method"] = evidence.method
        enriched.at[index, "location_confidence"] = evidence.confidence
        enriched.at[index, "location_reason"] = evidence.reason
        enriched.at[index, "geocoded_at"] = evidence.geocoded_at
        enriched.at[index, "geocoder_version"] = evidence.geocoder_version
    return enriched


def _json_safe_location_value(value: object) -> str:
    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return "null"
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return str(value)


def _listing_location_quality(rows: pd.DataFrame) -> dict[str, object]:
    def counts(column: str) -> dict[str, int]:
        if column not in rows:
            return {}
        values = rows[column].value_counts(dropna=False)
        return {
            _json_safe_location_value(key): int(value)
            for key, value in sorted(
                values.items(), key=lambda item: _json_safe_location_value(item[0])
            )
        }

    eligible = (
        int(rows["location_eligible"].eq(True).sum())
        if "location_eligible" in rows
        else 0
    )
    unknown = (
        int(rows["location_method"].eq("unknown").sum())
        if "location_method" in rows
        else 0
    )
    return {
        "location": {
            "eligible": eligible,
            "unknown": unknown,
            "by_method": counts("location_method"),
            "by_reason": counts("location_reason"),
        }
    }


def _write_listing_location_quality(
    root: Path, rows: pd.DataFrame, *, diagnostics: dict[str, int] | None = None
) -> None:
    path = root / "outputs" / "reports" / "listing-quality.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    quality = _listing_location_quality(rows)
    if diagnostics:
        quality["detail"] = {
            str(reason): int(count) for reason, count in sorted(diagnostics.items())
        }
    path.write_text(
        json.dumps(quality, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def create_listing_geocoding_service(
    doorplates: pd.DataFrame, connection_factory=None
) -> GeocodingService:
    """Create the only default geocoder: local official doorplate evidence.

    It is deliberately local and must be called explicitly by
    ``listing-build --geocoder-enabled``; no browser, secret, or network is
    implicitly configured for offline builds.
    """
    if connection_factory is None:
        raise ValueError("QINGPU_DATABASE_URL is required for persistent geocoding")
    cache = MySQLGeocodeCache(connection_factory)
    cache.ensure_schema()
    return GeocodingService(DoorplateListingGeocoder(doorplates), cache)


def create_listing_detail_enricher(args) -> ListingDetailEnricher:
    if args.page_timeout < 1:
        raise ValueError("page-timeout 必須 >= 1")
    if not args.profile_dir:
        raise ValueError("detail enrichment requires --profile-dir")
    return ListingDetailEnricher(
        browser_factory=create_chrome,
        chrome_config=ChromeConfig(
            headless=False,
            profile_dir=args.profile_dir,
            page_timeout_seconds=args.page_timeout,
        ),
    )


def _source_listings_from_extraction(
    extraction,
    representation: str = "unknown",
    schema_version: str = "unknown",
) -> list[SourceListing]:
    acquisition_representation = (
        representation if representation != "unknown" else extraction.representation
    )
    acquisition_schema_version = (
        schema_version if schema_version != "unknown" else extraction.schema_version
    )
    return [
        SourceListing(
            source_listing_id=listing.source_listing_id,
            listing_type=listing.listing_type,
            source_url=listing.source_url,
            payload={
                **listing.payload,
                "representation": acquisition_representation,
                "schema_version": acquisition_schema_version,
            },
        )
        for listing in extraction.listings
    ]


def _rejection_summary(rejections: Counter[str]) -> str:
    return ",".join(
        f"{reason}:{count}" for reason, count in sorted(rejections.items())
    )


def _capture_summary(batch: CaptureBatch) -> str:
    representations = sorted({page.representation for page in batch.pages})
    representation = ",".join(representations) if representations else "unknown"
    batch_path = batch.batch_dir or "unknown"
    return (
        f"captured_pages={len(batch.pages)} "
        f"accepted={sum(page.accepted_count for page in batch.pages)} "
        f"rejected={sum(page.rejected_count for page in batch.pages)} "
        f"representation={representation} "
        f"complete={str(batch.is_complete).lower()} "
        f"batch_path={batch_path}"
    )


def listing_scrape(root: Path, args) -> int:
    for lt in args.types:
        if lt not in listing_type_choices:
            print(f"未知的 listing 類型: {lt}", file=sys.stderr)
            return 1
    if args.max_pages < 1:
        print("max-pages 必須 >= 1", file=sys.stderr)
        return 1
    if args.delay_min > args.delay_max:
        print("delay-min 不能大於 delay-max", file=sys.stderr)
        return 1

    exit_code = 0
    for listing_type in args.types:
        config = ChromeConfig(
            headless=args.headless,
            profile_dir=args.profile_dir,
            page_timeout_seconds=args.page_timeout,
            delay_seconds=(args.delay_min, args.delay_max),
        )
        source = create_listing_source(root, config)
        batch = source.capture(listing_type, max_pages=args.max_pages)
        print(f"[{listing_type}] {_capture_summary(batch)}")
        if sum(page.accepted_count for page in batch.pages) == 0:
            exit_code = 1
        if batch.errors:
            exit_code = 1
            for err in batch.errors:
                print(
                    f"[{listing_type}] 頁面 {err.page_number}: {err.message}",
                    file=sys.stderr,
                )
    return exit_code


def _load_listing_manifest(manifest_path: Path) -> object | None:
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError):
        return None


def _listing_manifest_recency_key(manifest_path: Path) -> tuple[int, float, str]:
    deterministic_fallback = manifest_path.parent.as_posix()
    manifest = _load_listing_manifest(manifest_path)
    try:
        started_at = datetime.fromisoformat(manifest["started_at"])
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        timestamp = started_at.astimezone(UTC).timestamp()
    except (
        OverflowError,
        TypeError,
        ValueError,
        KeyError,
    ):
        return (0, 0.0, deterministic_fallback)
    return (1, timestamp, deterministic_fallback)


def _latest_listing_batch(listing_root: Path) -> Path | None:
    manifests = sorted(listing_root.rglob("manifest.json"))
    if not manifests:
        return None
    return max(manifests, key=_listing_manifest_recency_key).parent


def _valid_listing_build_manifest(manifest: object) -> bool:
    if not isinstance(manifest, dict):
        return False
    if (
        not valid_listing_batch_id(manifest.get("batch_id"))
        or manifest.get("listing_type") not in listing_type_choices
        or not isinstance(manifest.get("started_at"), str)
        or not manifest["started_at"]
        or not isinstance(manifest.get("is_complete"), bool)
    ):
        return False
    if "source" in manifest and not isinstance(manifest["source"], str):
        return False
    if "reached_terminal_page" in manifest and not isinstance(
        manifest["reached_terminal_page"], bool
    ):
        return False

    pages = manifest.get("pages")
    if not isinstance(pages, list) or (manifest["is_complete"] and not pages):
        return False
    page_numbers: set[int] = set()
    for page in pages:
        if not isinstance(page, dict):
            return False
        page_number = page.get("page_number")
        if (
            not isinstance(page_number, int)
            or isinstance(page_number, bool)
            or page_number <= 0
            or page_number in page_numbers
        ):
            return False
        page_numbers.add(page_number)
    return True


def _has_valid_source_coordinates(listing: SourceListing) -> bool:
    latitude = listing.payload.get("lat")
    longitude = listing.payload.get("lng")
    return (
        isinstance(latitude, Real)
        and not isinstance(latitude, bool)
        and isinstance(longitude, Real)
        and not isinstance(longitude, bool)
        and isfinite(float(latitude))
        and isfinite(float(longitude))
        and 20.0 < float(latitude) < 30.0
        and 115.0 < float(longitude) < 125.0
    )


def _enrich_newhouse_details(
    listings: list[SourceListing], batch_root: Path, detail_enricher
) -> tuple[list[SourceListing], dict[str, int]]:
    """Use an explicitly injected visible-browser enricher before normalize.

    Offline builds call this only when their caller has supplied the concrete
    browser-backed component.  It never constructs Chrome on its own.
    """
    enriched: list[SourceListing] = []
    diagnostics: Counter[str] = Counter()
    for listing in listings:
        if listing.listing_type != "newhouse" or _has_valid_source_coordinates(listing):
            enriched.append(listing)
            continue
        result = detail_enricher.enrich(listing, batch_root)
        address = result.payload.get("structured_address")
        if not isinstance(address, str) or not address.strip():
            diagnostics["detail_address_missing"] += 1
        enriched.append(result)
    return enriched, dict(diagnostics)


def _mark_detail_blocked(manifest_path: Path, reason: str) -> None:
    manifest = _load_listing_manifest(manifest_path)
    if not isinstance(manifest, dict):
        return
    errors = manifest.get("errors")
    manifest["errors"] = errors if isinstance(errors, list) else []
    manifest["errors"].append(
        {
            "page_number": 0,
            "code": "detail_enrichment_blocked",
            "message": reason,
        }
    )
    manifest["is_complete"] = False
    manifest["reached_terminal_page"] = False
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(manifest_path)


def listing_build(root: Path, args, *, detail_enricher=None) -> int:
    doorplates_path = root / "data" / "raw" / "doorplates.csv"
    if not doorplates_path.exists():
        print(
            "缺少 data/raw/doorplates.csv。請先執行 acquire 或手動下載門牌資料。",
            file=sys.stderr,
        )
        return 1

    if args.batch_dir:
        batch_root = Path(args.batch_dir)
    else:
        listing_root = root / "data" / "raw" / "listings" / "591"
        if not listing_root.exists():
            print("尚未有原始 listing 資料，請先執行 listing-scrape。", file=sys.stderr)
            return 1
        batch_root = _latest_listing_batch(listing_root)
        if batch_root is None:
            print("尚未有原始 listing 資料，請先執行 listing-scrape。", file=sys.stderr)
            return 1

    manifest_path = batch_root / "manifest.json"
    if not manifest_path.exists():
        print(f"找不到 manifest: {manifest_path}", file=sys.stderr)
        return 1

    manifest = _load_listing_manifest(manifest_path)
    if not _valid_listing_build_manifest(manifest):
        print(f"無效的 manifest: {manifest_path}", file=sys.stderr)
        return 1
    if not manifest["is_complete"]:
        print("拒絕處理不完整的 listing 批次。", file=sys.stderr)
        return 1
    try:
        listing_type = manifest["listing_type"]
        batch_id = manifest["batch_id"]
        started_at = datetime.fromisoformat(manifest["started_at"])
    except (KeyError, TypeError, ValueError):
        print(f"無效的 manifest: {manifest_path}", file=sys.stderr)
        return 1

    settings = get_settings(root)
    doorplates = build_doorplate_frame(doorplates_path)
    stations = station_points(settings.stations, doorplates)

    listings_map: dict[str, SourceListing] = {}
    rejection_reasons: Counter[str] = Counter()
    for page_info in sorted(manifest["pages"], key=lambda p: p["page_number"]):
        html_path = batch_root / f"page-{page_info['page_number']:04d}.html"
        if not html_path.exists():
            print(f"批次缺少頁面檔案: {html_path}", file=sys.stderr)
            return 1
        try:
            extraction = extract_rendered_page(
                html_path.read_text(encoding="utf-8"),
                listing_type,
            )
            rejection_reasons.update(
                rejected.reason_code for rejected in extraction.rejected
            )
            parsed = _source_listings_from_extraction(
                extraction,
                str(page_info.get("representation", "unknown")),
                str(page_info.get("schema_version", "unknown")),
            )
        except ListingSchemaError as exc:
            if rejection_reasons:
                print(f"rejection_reasons={_rejection_summary(rejection_reasons)}")
            print(
                f"批次頁面 {page_info['page_number']} 解析失敗: {exc}",
                file=sys.stderr,
            )
            return 1
        for sl in parsed:
            if sl.source_listing_id not in listings_map:
                listings_map[sl.source_listing_id] = sl

    if rejection_reasons:
        print(f"rejection_reasons={_rejection_summary(rejection_reasons)}")

    if not listings_map:
        print("批次中無有效 listing 資料。", file=sys.stderr)
        return 1

    source_listings = list(listings_map.values())
    detail_diagnostics: dict[str, int] = {}
    if detail_enricher is not None:
        try:
            source_listings, detail_diagnostics = _enrich_newhouse_details(
                source_listings, batch_root, detail_enricher
            )
        except DetailEnrichmentBlocked as exc:
            _mark_detail_blocked(manifest_path, str(exc))
            print(f"detail enrichment blocked: {exc}", file=sys.stderr)
            return 1
        if detail_diagnostics:
            print(
                " ".join(
                    f"{reason}={count}"
                    for reason, count in sorted(detail_diagnostics.items())
                )
            )

    normalized = [
        normalize_listing(sl, started_at) for sl in source_listings
    ]
    rows = _normalized_to_rows(normalized)
    df = pd.DataFrame(rows)
    if args.geocoder_enabled:
        try:
            geocoding_service = create_listing_geocoding_service(
                doorplates, create_mysql_connection_factory()
            )
        except (OSError, ValueError, pymysql.MySQLError) as exc:
            print(f"無法啟用官方門牌 geocoder: {exc}", file=sys.stderr)
            return 1
        df = _enrich_rows_with_geocoder(df, geocoding_service)
    located = assign_listing_life_circle(df, stations, settings.radius_m)

    batch = CaptureBatch(
        batch_id=batch_id,
        source=manifest.get("source", "591"),
        listing_type=listing_type,
        started_at=started_at,
        reached_terminal_page=bool(
            manifest.get("reached_terminal_page", manifest["is_complete"])
        ),
    )
    repo = create_listing_repository(root)
    repo.save_batch(batch, located)
    _write_listing_location_quality(root, located, diagnostics=detail_diagnostics)
    all_snapshots = repo.load_snapshots()
    if not all_snapshots.empty:
        snapshots_path = root / "data" / "processed" / "listing_snapshots.parquet"
        snapshots_path.parent.mkdir(parents=True, exist_ok=True)
        all_snapshots.to_parquet(snapshots_path, index=False)
    print(f"已儲存 {len(located)} 筆 listing 資料至批次 {batch_id}")
    return 0


def listing_sync(root: Path, args) -> int:
    for lt in args.types:
        if lt not in listing_type_choices:
            print(f"未知的 listing 類型: {lt}", file=sys.stderr)
            return 1
    if args.max_pages < 1:
        print("max-pages 必須 >= 1", file=sys.stderr)
        return 1
    if args.delay_min > args.delay_max:
        print("delay-min 不能大於 delay-max", file=sys.stderr)
        return 1

    doorplates_path = root / "data" / "raw" / "doorplates.csv"
    if not doorplates_path.exists():
        print(
            "缺少 data/raw/doorplates.csv。請先執行 acquire 或手動下載門牌資料。",
            file=sys.stderr,
        )
        return 1

    settings = get_settings(root)
    doorplates = build_doorplate_frame(doorplates_path)
    stations = station_points(settings.stations, doorplates)
    repo = create_listing_repository(root)

    market_path = settings.processed_dir / "market_transactions.parquet"
    try:
        market_frame = pd.read_parquet(market_path)
    except (FileNotFoundError, ValueError):
        market_frame = pd.DataFrame()
    model_registry = ModelRegistry(root / "artifacts")

    exit_code = 0
    for listing_type in args.types:
        config = ChromeConfig(
            headless=args.headless,
            profile_dir=args.profile_dir,
            page_timeout_seconds=args.page_timeout,
            delay_seconds=(args.delay_min, args.delay_max),
        )
        source = create_listing_source(root, config)
        batch = source.capture(listing_type, max_pages=args.max_pages)
        print(f"[{listing_type}] {_capture_summary(batch)}")

        if sum(page.accepted_count for page in batch.pages) == 0:
            exit_code = 1
        if batch.errors:
            exit_code = 1
            for err in batch.errors:
                print(
                    f"[{listing_type}] 頁面 {err.page_number}: {err.message}",
                    file=sys.stderr,
                )

        all_listings: list[SourceListing] = []
        rejection_reasons: Counter[str] = Counter()
        schema_failed = False
        for page in batch.pages:
            try:
                extraction = extract_rendered_page(page.html, listing_type)
                rejection_reasons.update(
                    rejected.reason_code for rejected in extraction.rejected
                )
                all_listings.extend(
                    _source_listings_from_extraction(
                        extraction, page.representation, page.schema_version
                    )
                )
            except ListingSchemaError as e:
                exit_code = 1
                schema_failed = True
                print(
                    f"[{listing_type}] 頁面 {page.page_number}: 解析失敗: {e}",
                    file=sys.stderr,
                )

        if rejection_reasons:
            print(
                f"[{listing_type}] rejection_reasons="
                f"{_rejection_summary(rejection_reasons)}"
            )

        if schema_failed:
            continue

        if not all_listings:
            continue

        normalized = [
            normalize_listing(sl, batch.started_at) for sl in all_listings
        ]
        rows = _normalized_to_rows(normalized)
        df = pd.DataFrame(rows)
        located = assign_listing_life_circle(df, stations, settings.radius_m)

        # Add default state columns before event detection
        located["active"] = True
        located["consecutive_absences"] = 0
        located["last_seen_batch_id"] = batch.batch_id
        located["model_evidence"] = None

        previous = repo.load_current(listing_type)
        event_result = detect_listing_events(previous, located, batch)

        # Merge state from event detection into located
        if not event_result.state.empty:
            merge_cols = ["source", "listing_type", "source_listing_id"]
            state_subset = event_result.state[
                merge_cols + ["active", "consecutive_absences", "last_seen_batch_id"]
            ].drop_duplicates(subset=merge_cols)
            for col in ("active", "consecutive_absences", "last_seen_batch_id"):
                if col in located.columns:
                    located = located.drop(columns=[col])
            located = located.merge(state_subset, on=merge_cols, how="left")
            located["active"] = located["active"].fillna(True)
            located["consecutive_absences"] = located["consecutive_absences"].fillna(0).astype(int)
            located["last_seen_batch_id"] = located["last_seen_batch_id"].fillna(batch.batch_id)

            # Also write absent-listing state rows into current
            absent = event_result.state[
                ~event_result.state["source_listing_id"].isin(
                    located["source_listing_id"]
                )
            ].copy()
            if not absent.empty:
                located = pd.concat([located, absent], ignore_index=True)

        if not event_result.events.empty:
            repo.append_events(event_result.events)

        # ---- Valuation integration (IMPORTANT-7) ----
        if listing_type in ("sale", "newhouse") and not market_frame.empty:
            listing_map = {n.source_listing_id: n for n in normalized}
            for idx, row in located.iterrows():
                sid = row.get("source_listing_id")
                nl = listing_map.get(sid)
                if nl is None:
                    continue
                station_code = row.get("station_code")
                station_dist = row.get("station_distance_m")
                location_eligible = bool(row.get("location_eligible", False))
                if pd.isna(station_code) or station_code is None:
                    continue
                try:
                    result = compare_listing_to_model(
                        nl, model_registry, market_frame,
                        station_code=str(station_code),
                        station_distance_m=float(station_dist) if pd.notna(station_dist) else None,
                        location_eligible=location_eligible,
                    )
                    located.at[idx, "model_evidence"] = json.dumps(result, ensure_ascii=False)
                except Exception as exc:
                    print(
                        f"  valuation failed for {sid}: {exc}",
                        file=sys.stderr,
                    )

        repo.save_batch(batch, located)

    all_snapshots = repo.load_snapshots()
    if all_snapshots.empty:
        return exit_code
    snapshots_path = root / "data" / "processed" / "listing_snapshots.parquet"
    snapshots_path.parent.mkdir(parents=True, exist_ok=True)
    all_snapshots.to_parquet(snapshots_path, index=False)

    return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qingpu-data")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("acquire", "run"):
        command = subparsers.add_parser(name)
        command.add_argument("--start-season", default="110S3")
        command.add_argument("--end-season", default="115S2")
    analyse_parser = subparsers.add_parser("analyse")
    analyse_parser.add_argument("--allow-no-go", action="store_true")
    market_parser = subparsers.add_parser("market-build")
    market_parser.add_argument("--input", default="data/processed/transactions.parquet")
    market_parser.add_argument("--output", default="data/processed/market_transactions.parquet")
    market_parser.add_argument("--quality-output", default="outputs/reports/m1-market-quality.json")
    mysql_parser = subparsers.add_parser("mysql-load")
    mysql_parser.add_argument("--input", default="data/processed/market_transactions.parquet")
    model_parser = subparsers.add_parser("model-train")
    model_parser.add_argument("--input", default="data/processed/market_transactions.parquet")
    model_parser.add_argument("--artifact-dir", default="artifacts")
    model_parser.add_argument("--report-dir", default="outputs/reports")

    scrape_parser = subparsers.add_parser(
        "listing-scrape", help="capture raw listing data from 591"
    )
    scrape_parser.add_argument("--types", nargs="+", required=True)
    scrape_parser.add_argument("--max-pages", type=int, default=10)
    scrape_parser.add_argument("--delay-min", type=float, default=2.0)
    scrape_parser.add_argument("--delay-max", type=float, default=5.0)
    scrape_parser.add_argument("--page-timeout", type=int, default=30)
    scrape_parser.add_argument("--headless", action="store_true")
    scrape_parser.add_argument("--profile-dir", default=None)

    build_parser = subparsers.add_parser(
        "listing-build",
        help="normalize and persist an existing raw batch",
    )
    build_parser.add_argument("--batch-dir", default=None)
    build_parser.add_argument("--geocoder-enabled", action="store_true")
    build_parser.add_argument("--detail-enrichment-enabled", action="store_true")
    build_parser.add_argument("--profile-dir", default=None)
    build_parser.add_argument("--page-timeout", type=int, default=30)

    sync_parser = subparsers.add_parser(
        "listing-sync",
        help="capture, normalize, locate, persist, detect events, and value listings",
    )
    sync_parser.add_argument("--types", nargs="+", required=True)
    sync_parser.add_argument("--max-pages", type=int, default=10)
    sync_parser.add_argument("--delay-min", type=float, default=2.0)
    sync_parser.add_argument("--delay-max", type=float, default=5.0)
    sync_parser.add_argument("--page-timeout", type=int, default=30)
    sync_parser.add_argument("--headless", action="store_true")
    sync_parser.add_argument("--profile-dir", default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path.cwd()
    if args.command in ("acquire", "run"):
        acquire(root, args.start_season, args.end_season)
    if args.command in ("analyse", "run"):
        return analyse(root, getattr(args, "allow_no_go", False))
    if args.command == "market-build":
        return market_build(root, args.input, args.output, args.quality_output)
    if args.command == "mysql-load":
        return mysql_load(root, args.input)
    if args.command == "model-train":
        return model_train(root, args.input, args.artifact_dir, args.report_dir)
    if args.command == "listing-scrape":
        return listing_scrape(root, args)
    if args.command == "listing-build":
        detail_enricher = None
        if args.detail_enrichment_enabled:
            try:
                detail_enricher = create_listing_detail_enricher(args)
            except ValueError as exc:
                print(f"無法啟用 detail enrichment: {exc}", file=sys.stderr)
                return 1
        return listing_build(root, args, detail_enricher=detail_enricher)
    if args.command == "listing-sync":
        return listing_sync(root, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
