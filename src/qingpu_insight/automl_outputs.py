from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from qingpu_insight.automl_search import json_safe

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")


class AutoMLRunOutputStore:
    def __init__(self, base_path: str | Path) -> None:
        self._base = Path(base_path)

    def _validate_run_id(self, run_id: str) -> None:
        if _UUID_RE.match(run_id) is None:
            raise ValueError(f"invalid run_id: {run_id}")

    def write(self, run_id: str, market: str, snapshot: dict[str, object]) -> None:
        self._validate_run_id(run_id)
        if market not in {"resale", "presale"}:
            raise ValueError(f"invalid market: {market}")
        run_dir = self._base / "outputs" / "automl" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        target = run_dir / f"{market}-trials.json"
        tmp = target.with_suffix(".tmp")
        safe = json_safe(snapshot)
        data = json.dumps(safe, ensure_ascii=False, default=str)
        tmp.write_text(data, encoding="utf-8")
        os.replace(str(tmp), str(target))

    def get(self, run_id: str, market: str) -> dict[str, object] | None:
        self._validate_run_id(run_id)
        if market not in {"resale", "presale"}:
            raise ValueError(f"invalid market: {market}")
        target = self._base / "outputs" / "automl" / run_id / f"{market}-trials.json"
        if not target.exists():
            return None
        return json.loads(target.read_text(encoding="utf-8"))

    def copy_trials_to(self, run_id: str, market: str, stage: str | Path) -> tuple[str, str]:
        self._validate_run_id(run_id)
        if market not in {"resale", "presale"}:
            raise ValueError(f"invalid market: {market}")
        src = self._base / "outputs" / "automl" / run_id / f"{market}-trials.json"
        if not src.exists():
            raise FileNotFoundError(f"no trials file found for run {run_id}, market {market}")
        stage_path = Path(stage)
        if not stage_path.is_absolute():
            stage_path = self._base / stage_path
        dest_dir = stage_path / "automl"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{market}-trials.json"
        content = src.read_bytes()
        sha256 = hashlib.sha256(content).hexdigest()
        dest.write_bytes(content)
        rel = Path("automl") / f"{market}-trials.json"
        return (rel.as_posix(), sha256)
