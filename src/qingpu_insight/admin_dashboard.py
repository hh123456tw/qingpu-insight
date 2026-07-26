from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class ReadinessItem:
    code: str
    status: Literal["ready", "warning", "blocked"]
    message: str
    technical: dict[str, str | int | bool | None]


class AdminDashboardService:
    def __init__(
        self,
        probes: dict[str, Callable[[], ReadinessItem]],
        jobs: object,
        health_repository: object | None = None,
        backup_repository: object | None = None,
        model_observatory: object | None = None,
    ) -> None:
        self._probes = probes
        self._jobs = jobs
        self._health_repository = health_repository
        self._backup_repository = backup_repository
        self._model_observatory = model_observatory

    def read(self) -> dict[str, Any]:
        readiness = []
        for _code, probe in self._probes.items():
            readiness.append(probe())

        readiness_dicts = [
            {
                "code": r.code,
                "status": r.status,
                "message": r.message,
                "technical": r.technical,
            }
            for r in readiness
        ]

        mysql_items = [r for r in readiness if r.code == "mysql"]
        mutation_ready = True
        if mysql_items:
            mutation_ready = mysql_items[0].status != "blocked"

        active_jobs: list[dict[str, Any]] = []
        recent_jobs: list[dict[str, Any]] = []
        try:
            runs = self._jobs.list_active("listing_update")
            active_jobs = [_job_to_dict(r) for r in runs]
        except Exception:
            pass
        try:
            runs = self._jobs.list_recent(20)
            recent_jobs = [_job_to_dict(r) for r in runs]
        except Exception:
            pass

        health: dict[str, Any] | None = None
        if self._health_repository is not None:
            try:
                latest = self._health_repository.latest()
                if latest is not None:
                    health = {
                        "status": latest.status,
                        "checked_at": latest.checked_at.isoformat(),
                        "items": [
                            {
                                "code": item.code,
                                "status": item.status,
                                "summary": item.summary,
                                "value": item.value,
                                "unit": item.unit,
                            }
                            for item in latest.items
                        ],
                    }
            except Exception:
                pass

        backup: dict[str, Any] | None = None
        if self._backup_repository is not None:
            try:
                records = self._backup_repository.list_recent(1)
                if records:
                    r = records[0]
                    backup = {
                        "backup_id": r.backup_id,
                        "status": r.status,
                        "sha256": r.sha256,
                        "size_bytes": r.size_bytes,
                        "created_at": r.created_at.isoformat(),
                        "restore_status": r.restore_status,
                        "restore_checked_at": (
                            r.restore_checked_at.isoformat()
                            if r.restore_checked_at
                            else None
                        ),
                    }
            except Exception:
                pass

        models: dict[str, Any] | None = None
        if self._model_observatory is not None:
            try:
                models = self._model_observatory.status()
            except Exception:
                pass

        action_items: list[dict[str, str]] = []
        if not mutation_ready:
            action_items.append({
                "code": "mysql_unreachable",
                "message": "MySQL 無法連線，所有更新功能已暫停。",
                "section": "readiness",
            })

        if self._backup_repository is None:
            action_items.append({
                "code": "backup_missing",
                "message": "尚未設定資料庫備份。",
                "section": "backup",
            })

        if models is not None:
            official_models = models.get("official_models", {})
            candidate_count = models.get("candidate_count", 0) or 0
            has_official = any(
                m.get("available", False) for m in official_models.values()
            )
            if not has_official and candidate_count > 0:
                action_items.append({
                    "code": "candidate_waiting_review",
                    "message": "有候選模型等待審查。",
                    "section": "models",
                })

        return {
            "mutation_ready": mutation_ready,
            "readiness": readiness_dicts,
            "active_jobs": active_jobs,
            "recent_jobs": recent_jobs,
            "health": health,
            "backup": backup,
            "models": models,
            "action_items": action_items,
        }


def _job_to_dict(run: Any) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "job_type": run.job_type,
        "status": run.status,
        "trigger": run.trigger,
        "attempt": run.attempt,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "input_version": run.input_version,
        "output_version": run.output_version,
        "summary": run.summary,
        "error_code": run.error_code,
        "error_message": run.error_message,
    }
