from __future__ import annotations

import numpy as np
import pytest

from qingpu_insight.automl_control import AutoMLControlRegistry
from qingpu_insight.automl_outputs import AutoMLRunOutputStore


class TestAutoMLControlRegistry:
    def test_stop_request_is_idempotent_and_scoped(self) -> None:
        registry = AutoMLControlRegistry()
        registry.register("run-a")
        registry.register("run-b")
        assert registry.request_stop("run-a") is True
        assert registry.request_stop("run-a") is True
        assert registry.should_stop("run-a") is True
        assert registry.should_stop("run-b") is False

    def test_request_stop_unknown_run(self) -> None:
        registry = AutoMLControlRegistry()
        assert registry.request_stop("unknown") is False

    def test_should_stop_unknown_run(self) -> None:
        registry = AutoMLControlRegistry()
        assert registry.should_stop("unknown") is False

    def test_unregister_clears_state(self) -> None:
        registry = AutoMLControlRegistry()
        registry.register("run-a")
        registry.request_stop("run-a")
        assert registry.should_stop("run-a") is True
        registry.unregister("run-a")
        assert registry.should_stop("run-a") is False

    def test_multiple_registries_are_independent(self) -> None:
        r1 = AutoMLControlRegistry()
        r2 = AutoMLControlRegistry()
        r1.register("run-a")
        r2.register("run-a")
        r1.request_stop("run-a")
        assert r1.should_stop("run-a") is True
        assert r2.should_stop("run-a") is False


class TestAutoMLRunOutputStore:
    def test_partial_output_write_is_atomic_and_json_safe(
        self, tmp_path
    ) -> None:
        store = AutoMLRunOutputStore(tmp_path)
        store.write(
            "00000000-0000-0000-0000-000000000001",
            "resale",
            {"completed_trials": np.int64(2), "trials": [{"mae": np.float64(123.5)}]},
        )
        assert (
            store.get("00000000-0000-0000-0000-000000000001", "resale")[
                "completed_trials"
            ]
            == 2
        )
        assert not list(tmp_path.rglob("*.tmp"))

    def test_write_rejects_invalid_uuid(self, tmp_path) -> None:
        store = AutoMLRunOutputStore(tmp_path)
        with pytest.raises(ValueError, match="run_id"):
            store.write("not-a-uuid", "resale", {})

    def test_get_missing_run_returns_none(self, tmp_path) -> None:
        store = AutoMLRunOutputStore(tmp_path)
        assert (
            store.get("00000000-0000-0000-0000-000000000001", "resale") is None
        )

    def test_copy_trials_to_returns_path_and_checksum(
        self, tmp_path
    ) -> None:
        store = AutoMLRunOutputStore(tmp_path)
        run_id = "00000000-0000-0000-0000-000000000001"
        data = {"completed_trials": 2, "trials": []}
        store.write(run_id, "resale", data)

        rel_path, sha256 = store.copy_trials_to(run_id, "resale", "candidate-stage")

        dest = tmp_path / "candidate-stage" / "automl" / "resale-trials.json"
        assert dest.exists()
        assert rel_path == "candidate-stage/automl/resale-trials.json"
        assert isinstance(sha256, str) and len(sha256) == 64

    def test_copy_trials_to_missing_run_raises(self, tmp_path) -> None:
        store = AutoMLRunOutputStore(tmp_path)
        with pytest.raises(FileNotFoundError):
            store.copy_trials_to("00000000-0000-0000-0000-000000000001", "resale", "stage")
