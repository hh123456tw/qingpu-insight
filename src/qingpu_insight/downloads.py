import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

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
