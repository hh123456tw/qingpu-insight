from __future__ import annotations

from typing import Any

from qingpu_insight.admin_dashboard import AdminDashboardService, ReadinessItem
from qingpu_insight.jobs import JobRun


class StubJobs:
    def __init__(self, runs: list[JobRun]) -> None:
        self._runs = runs

    def list_recent(
        self, limit: int = 20, job_type: str | None = None
    ) -> list[JobRun]:
        return self._runs[:limit]

    def list_active(self, job_type: str) -> list[JobRun]:
        return [
            r
            for r in self._runs
            if r.job_type == job_type
            and r.status in ("pending", "running", "retry_wait")
        ]


class _FakeModelObservatory:
    def __init__(self, status: dict[str, Any]) -> None:
        self._status = status

    def status(self) -> dict[str, Any]:
        return self._status


def dashboard_with(
    mysql_ready: bool = True,
    backup: Any = None,
    model_status: dict[str, Any] | None = None,
) -> AdminDashboardService:
    probes: dict[str, Any] = {}
    if mysql_ready:
        probes["mysql"] = lambda: ReadinessItem(
            "mysql", "ready", "MySQL 連線正常。", {"reachable": True}
        )
    else:
        probes["mysql"] = lambda: ReadinessItem(
            "mysql", "blocked", "MySQL 無法連線。", {"reachable": False}
        )

    model_obs = None
    if model_status is not None:
        model_obs = _FakeModelObservatory(model_status)

    return AdminDashboardService(
        probes=probes,
        jobs=StubJobs([]),
        health_repository=None,
        backup_repository=backup,
        model_observatory=model_obs,
    )


def test_dashboard_blocks_mutations_when_mysql_probe_fails() -> None:
    service = AdminDashboardService(
        probes={
            "mysql": lambda: ReadinessItem(
                "mysql", "blocked", "MySQL 無法連線。", {"reachable": False}
            )
        },
        jobs=StubJobs([]),
        health_repository=None,
        backup_repository=None,
        model_observatory=None,
    )
    result = service.read()
    assert result["mutation_ready"] is False
    assert result["readiness"][0]["message"] == "MySQL 無法連線。"


def test_dashboard_reports_action_items_without_inventing_status() -> None:
    result = dashboard_with(
        mysql_ready=True,
        backup=None,
        model_status={"candidate_count": 1, "official_models": {}},
    ).read()
    assert result["mutation_ready"] is True
    assert [item["code"] for item in result["action_items"]] == [
        "backup_missing",
        "candidate_waiting_review",
    ]
