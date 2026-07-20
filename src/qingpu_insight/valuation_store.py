import json
import uuid
from pathlib import Path
from typing import Any


class FileValuationStore:
    def __init__(self, root: Path):
        self.root = root
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

    def get(self, valuation_id: str) -> dict[str, Any] | None:
        try:
            parsed = uuid.UUID(valuation_id)
        except ValueError:
            return None
        path = self.root / f"{parsed}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
