import hashlib
import io
import json
from pathlib import Path
from zipfile import ZipFile

import requests
import responses

from qingpu_insight.downloads import DownloadRecord, download_file, download_season, write_manifest


def taoyuan_zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as bundle:
        bundle.writestr("H_lvr_land_A.csv", "resale")
        bundle.writestr("H_lvr_land_B.csv", "presale")
    return buffer.getvalue()


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
    responses.get(url, body=taoyuan_zip_bytes(), status=200)

    record = download_season("https://plvr.land.moi.gov.tw", "115S2", tmp_path / "115S2.zip")

    assert record.source_url == url
    assert record.path.name == "115S2.zip"


@responses.activate
def test_download_file_retries_a_transient_connection_failure(tmp_path: Path) -> None:
    url = "https://example.test/retry.csv"
    responses.get(url, body=requests.ConnectionError("connection reset"))
    responses.get(url, body=b"recovered", status=200)

    record = download_file(url, tmp_path / "retry.csv", retry_delays=(0.0,))

    assert record.path.read_bytes() == b"recovered"
    assert len(responses.calls) == 2


@responses.activate
def test_download_season_reuses_an_existing_valid_archive(tmp_path: Path) -> None:
    archive = tmp_path / "115S2.zip"
    archive.write_bytes(taoyuan_zip_bytes())

    record = download_season("https://plvr.land.moi.gov.tw", "115S2", archive)

    assert record.path == archive
    assert len(responses.calls) == 0


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


def test_write_manifest_merges_previous_records_by_source_url(tmp_path: Path) -> None:
    first_path = tmp_path / "first.csv"
    second_path = tmp_path / "second.csv"
    first_path.write_bytes(b"first")
    second_path.write_bytes(b"second")
    manifest = tmp_path / "manifest.json"
    first = DownloadRecord(
        source_url="https://example.test/first.csv",
        path=first_path,
        sha256=hashlib.sha256(b"first").hexdigest(),
        byte_size=5,
        downloaded_at="2026-07-19T00:00:00+00:00",
    )
    second = DownloadRecord(
        source_url="https://example.test/second.csv",
        path=second_path,
        sha256=hashlib.sha256(b"second").hexdigest(),
        byte_size=6,
        downloaded_at="2026-07-20T00:00:00+00:00",
    )

    write_manifest([first], manifest)
    write_manifest([second], manifest)

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert [item["source_url"] for item in payload] == [first.source_url, second.source_url]
