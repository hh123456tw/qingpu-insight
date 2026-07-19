import hashlib
import json
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import requests

from qingpu_insight.archives import validate_taoyuan_archive


@dataclass(frozen=True)
class DownloadRecord:
    source_url: str
    path: Path
    sha256: str
    byte_size: int
    downloaded_at: str


def record_file(source_url: str, path: Path) -> DownloadRecord:
    digest = hashlib.sha256()
    byte_size = 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
            byte_size += len(chunk)
    downloaded_at = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
    return DownloadRecord(source_url, path, digest.hexdigest(), byte_size, downloaded_at)


def download_file(
    url: str,
    destination: Path,
    session: requests.Session | None = None,
    retry_delays: tuple[float, ...] = (1.0, 3.0),
    reuse_existing: bool = False,
    validator: Callable[[Path], bool] | None = None,
) -> DownloadRecord:
    client = session or requests.Session()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    if reuse_existing and destination.is_file():
        if validator is None or validator(destination):
            return record_file(url, destination)
    attempts = len(retry_delays) + 1
    for attempt in range(attempts):
        try:
            temporary.unlink(missing_ok=True)
            with client.get(url, stream=True, timeout=(10, 120)) as response:
                response.raise_for_status()
                with temporary.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            output.write(chunk)
            if validator is not None and not validator(temporary):
                raise ValueError(f"downloaded file failed validation: {url}")
            temporary.replace(destination)
            return record_file(url, destination)
        except (OSError, ValueError, requests.RequestException):
            temporary.unlink(missing_ok=True)
            if attempt == attempts - 1:
                raise
            time.sleep(retry_delays[attempt])
    raise RuntimeError("unreachable download retry state")


def download_season(
    base_url: str,
    season: str,
    destination: Path,
    session: requests.Session | None = None,
    retry_delays: tuple[float, ...] = (1.0, 3.0),
) -> DownloadRecord:
    url = f"{base_url}/DownloadHistory?type=season&fileName={season}"
    return download_file(
        url,
        destination,
        session,
        retry_delays=retry_delays,
        reuse_existing=True,
        validator=validate_taoyuan_archive,
    )


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
    previous = []
    if path.is_file():
        previous = json.loads(path.read_text(encoding="utf-8"))
    merged = {item["source_url"]: item for item in previous}
    for record in records:
        item = asdict(record)
        item["path"] = str(record.path)
        merged[record.source_url] = item
    payload = [merged[key] for key in sorted(merged)]
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
