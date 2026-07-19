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
