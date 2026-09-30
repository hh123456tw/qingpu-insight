import json
import os
import uuid
from pathlib import Path
from typing import Any

# The web app keeps at most this many valuation records (outputs/valuations/*.json);
# older records are deleted first, so a shared result link expires after enough newer ones.
DEFAULT_MAX_VALUATION_RECORDS = 5000


class FileValuationStore:
    def __init__(self, root: Path, *, max_records: int | None = None):
        if max_records is not None and max_records < 1:
            raise ValueError("max_records must be at least 1")
        self.root = root
        self.max_records = max_records
        root.mkdir(parents=True, exist_ok=True)

    def save(self, value: dict[str, Any]) -> str:
        valuation_id = str(uuid.uuid4())
        self.save_with_id(valuation_id, value)
        return valuation_id

    def save_with_id(self, valuation_id: str, value: dict[str, Any]) -> None:
        path = self.root / f"{valuation_id}.json"
        tmp = path.with_suffix(".tmp")
        self.root.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        if self.max_records is not None:
            self._prune(keep=path)

    def _prune(self, keep: Path) -> None:
        """Delete the oldest records beyond ``max_records`` (never the one just saved)."""
        records: list[tuple[int, str, Path]] = []
        with os.scandir(self.root) as entries:
            for entry in entries:
                if not entry.is_file() or not entry.name.endswith(".json"):
                    continue
                try:
                    records.append((entry.stat().st_mtime_ns, entry.name, Path(entry.path)))
                except OSError:
                    continue
        excess = len(records) - self.max_records
        if excess <= 0:
            return
        records.sort()
        for _, _, path in records:
            if excess <= 0:
                break
            if path == keep:
                continue
            try:
                path.unlink()
            except OSError:
                continue
            excess -= 1

    def get(self, valuation_id: str) -> dict[str, Any] | None:
        try:
            parsed = uuid.UUID(valuation_id)
        except ValueError:
            return None
        path = self.root / f"{parsed}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
