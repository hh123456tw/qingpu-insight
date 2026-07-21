import argparse
import json
import os
import sys
import urllib.parse
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
from qingpu_insight.valuation import ValuationBundle, train_artifact
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
