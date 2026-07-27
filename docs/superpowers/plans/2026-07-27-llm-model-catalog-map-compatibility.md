# LLM Model Catalog and Map Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace free-form Benchmark model input with a backend-owned live model catalog, expose readiness in the fixed homepage model selector, and keep the homepage map useful when a stale Flask process returns 404 for the new grouped-map endpoint.

**Architecture:** A focused `llm_model_catalog.py` owns Ollama discovery, the two approved Gemini Benchmark models, safe public DTOs, and fail-closed `model_id` resolution. Benchmark execution receives the resolved provider and exact model through a production-wired runner, while the admin and homepage consume catalog projections without learning Ollama connection details. The map loader keeps the grouped endpoint as its primary path and performs a clearly labelled recent-100 compatibility request only for an HTTP 404.

**Tech Stack:** Python 3.11, Flask, `requests`, dataclasses, existing report providers and benchmark engine, vanilla JavaScript, Leaflet, Node.js contract tests, pytest, Ruff, PowerShell.

## Global Constraints

- Use TDD for every implementation task: first add the focused failing test, run it and observe the expected failure, then add only the implementation required by that task.
- Gemini Benchmark models are exactly `gemini-3.5-flash-lite` and `gemma-4-31b-it`.
- Homepage conversation models remain exactly `gemini-3.5-flash-lite`, `gemma-4-31b-it`, `gemma4:e2b`, and `rule`.
- The browser submits only `model_id`; the server is the only authority that resolves provider and model.
- Split a model ID only at its first `:` so an Ollama tag such as `gemma4:e2b` remains intact.
- Fetch Ollama models only from the configured backend base URL with a two-second timeout; never accept an Ollama URL from a browser request.
- Never expose API keys, Ollama digests, local paths, host details, or raw upstream error text.
- Failed Ollama discovery must not hide the two fixed Gemini entries.
- Only `/api/market/map-points` HTTP 404 activates compatibility mode; 400, 401, 403, 409, 422, 500, network errors, and malformed JSON remain visible failures.
- Compatibility mode uses the current market filters with `limit=100`, excludes viewport parameters, renders only finite latitude/longitude pairs, and explicitly says it is not the complete grouped dataset.
- Do not add model download/delete controls, an automatic Web restart supervisor, dynamic Gemini discovery, or offline map tiles.
- Use the shared repository virtual environment at `C:\Users\cygnu\Documents\Codex\qingpu-insight\.venv\Scripts\python.exe`.
- Preserve unrelated user changes and never commit secrets or generated benchmark output.

---

## File Map

- Create `src/qingpu_insight/llm_model_catalog.py`: immutable public model option, live Ollama `/api/tags` discovery, fixed Gemini options, safe warnings, and catalog membership resolution.
- Create `src/qingpu_insight/web_benchmark_runner.py`: construct the exact selected report provider and adapt `llm_benchmark.run_benchmark` to the admin job result.
- Create `tests/test_llm_model_catalog.py`: discovery, sanitisation, readiness, ordering, and fail-closed resolution tests.
- Create `tests/test_web_benchmark_runner.py`: exact provider/model construction and benchmark result projection tests.
- Modify `src/qingpu_insight/provider_ops.py`: pass both resolved provider and model to the configured Benchmark runner.
- Modify `src/qingpu_insight/admin_web.py`: add the catalog to `AdminRuntime`, expose `GET /api/admin/llm-models`, and accept only `model_id` for Benchmark submission.
- Modify `src/qingpu_insight/web.py`: create one dynamically configured catalog, wire the production Benchmark runner, and share Ollama readiness with the conversation catalog.
- Modify `tests/test_provider_ops.py`: lock the new runner interface and failure behaviour.
- Modify `tests/test_web.py`: lock the admin catalog/Benchmark HTTP contracts and production composition.
- Modify `src/qingpu_insight/templates/admin.html`: replace provider/free-text controls with one model select, help text, and refresh button.
- Modify `src/qingpu_insight/static/admin.js`: load/render/refresh the catalog and submit only `model_id`.
- Modify `tests/js/admin_contract.cjs`: test safe presentation and the exact Benchmark payload.
- Modify `src/qingpu_insight/conversation_models.py`: publish `ollama_ready` beside dynamic Gemini readiness.
- Modify `src/qingpu_insight/static/home_assistant.js`: explain unavailable Ollama fallback without adding free-form controls.
- Modify `tests/test_conversation_models.py`, `tests/test_conversation_web.py`, and `tests/js/home_assistant_contract.cjs`: lock homepage readiness behaviour.
- Modify `src/qingpu_insight/static/market_map.mjs`: validate grouped payloads and implement the 404-only recent-100 compatibility request.
- Modify `src/qingpu_insight/static/app.js`: clear stale markers before rendering errors and display compatibility status.
- Modify `tests/js/market_map_contract.mjs`: test normal, compatibility, malformed, non-404, and abort paths.
- Modify `README.md`: document the model selectors, Benchmark execution, and map compatibility message.

---

### Task 1: Live LLM Model Catalog

**Files:**
- Create: `src/qingpu_insight/llm_model_catalog.py`
- Create: `tests/test_llm_model_catalog.py`

**Interfaces:**
- Consumes: `requests.Session.get(url: str, timeout: float)` and dynamic getters `Callable[[], str]` / `Callable[[], bool]`.
- Produces: `BenchmarkModelOption`, `LlmModelCatalog.public_catalog() -> dict[str, object]`, `LlmModelCatalog.resolve(model_id: str) -> BenchmarkModelOption`, and `LlmModelCatalog.ollama_model_ready(model: str) -> bool`.

- [ ] **Step 1: Add catalog tests for live discovery and fixed Gemini entries**

```python
from __future__ import annotations

import pytest

from qingpu_insight.llm_model_catalog import LlmModelCatalog


class FakeResponse:
    def __init__(self, payload: object, *, status_error: Exception | None = None):
        self._payload = payload
        self._status_error = status_error

    def raise_for_status(self) -> None:
        if self._status_error is not None:
            raise self._status_error

    def json(self) -> object:
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse | Exception):
        self.response = response
        self.calls: list[tuple[str, float]] = []

    def get(self, url: str, *, timeout: float):
        self.calls.append((url, timeout))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def make_catalog(session: FakeSession, *, gemini_ready: bool = True) -> LlmModelCatalog:
    return LlmModelCatalog(
        ollama_base_url_getter=lambda: "http://127.0.0.1:11434/",
        gemini_configured_getter=lambda: gemini_ready,
        session=session,
        timeout_seconds=2.0,
    )


def test_public_catalog_discovers_deduplicates_and_sorts_ollama_models():
    session = FakeSession(FakeResponse({
        "models": [
            {"name": "qwen2.5:0.5b", "digest": "secret-digest"},
            {"model": "gemma4:e2b", "path": "C:/private/model"},
            {"name": "gemma4:e2b"},
            {"name": ""},
            {"name": 123},
        ],
    }))

    result = make_catalog(session).public_catalog()

    assert session.calls == [("http://127.0.0.1:11434/api/tags", 2.0)]
    assert [item["id"] for item in result["items"]] == [
        "ollama:gemma4:e2b",
        "ollama:qwen2.5:0.5b",
        "gemini:gemini-3.5-flash-lite",
        "gemini:gemma-4-31b-it",
    ]
    assert result["warnings"] == []
    assert "digest" not in repr(result)
    assert "C:/private" not in repr(result)


def test_public_catalog_keeps_fixed_gemini_models_when_ollama_is_offline():
    result = make_catalog(
        FakeSession(ConnectionError("private host and token")),
        gemini_ready=False,
    ).public_catalog()

    assert [item["model"] for item in result["items"]] == [
        "gemini-3.5-flash-lite",
        "gemma-4-31b-it",
    ]
    assert all(item["ready"] is False for item in result["items"])
    assert result["warnings"] == ["ollama_unavailable"]
    assert "private host" not in repr(result)


def test_resolve_requires_exact_membership_and_preserves_ollama_tag():
    catalog = make_catalog(FakeSession(FakeResponse({
        "models": [{"name": "gemma4:e2b"}],
    })))

    option = catalog.resolve("ollama:gemma4:e2b")

    assert option.provider == "ollama"
    assert option.model == "gemma4:e2b"
    with pytest.raises(ValueError, match="unknown_model_id"):
        catalog.resolve("ollama:not-installed:latest")
    with pytest.raises(ValueError, match="model_not_ready"):
        make_catalog(
            FakeSession(FakeResponse({"models": []})),
            gemini_ready=False,
        ).resolve("gemini:gemma-4-31b-it")


def test_ollama_model_ready_uses_same_live_discovery():
    catalog = make_catalog(FakeSession(FakeResponse({
        "models": [{"name": "gemma4:e2b"}],
    })))

    assert catalog.ollama_model_ready("gemma4:e2b") is True
    assert catalog.ollama_model_ready("missing") is False
```

- [ ] **Step 2: Run the focused tests and confirm the module is missing**

Run:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
& 'C:\Users\cygnu\Documents\Codex\qingpu-insight\.venv\Scripts\python.exe' -m pytest tests/test_llm_model_catalog.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'qingpu_insight.llm_model_catalog'`.

- [ ] **Step 3: Implement the catalog with safe DTOs and fresh membership checks**

Create `src/qingpu_insight/llm_model_catalog.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Literal

import requests

ProviderName = Literal["ollama", "gemini"]
_GEMINI_MODELS = (
    ("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite"),
    ("gemma-4-31b-it", "Gemma 4 31B"),
)


@dataclass(frozen=True)
class BenchmarkModelOption:
    id: str
    provider: ProviderName
    model: str
    label: str
    ready: bool
    note: str


class LlmModelCatalog:
    def __init__(
        self,
        *,
        ollama_base_url_getter: Callable[[], str],
        gemini_configured_getter: Callable[[], bool],
        session: requests.Session | None = None,
        timeout_seconds: float = 2.0,
    ) -> None:
        self._ollama_base_url_getter = ollama_base_url_getter
        self._gemini_configured_getter = gemini_configured_getter
        self._session = session or requests.Session()
        self._timeout_seconds = timeout_seconds

    def _ollama_names(self) -> tuple[tuple[str, ...], bool]:
        try:
            base_url = self._ollama_base_url_getter().rstrip("/")
            response = self._session.get(
                f"{base_url}/api/tags",
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
                raise ValueError("invalid_ollama_catalog")
            names = {
                value
                for entry in payload["models"]
                if isinstance(entry, dict)
                for value in (entry.get("name") or entry.get("model"),)
                if isinstance(value, str) and value.strip()
            }
            return tuple(sorted(names)), True
        except Exception:
            return (), False

    def public_catalog(self) -> dict[str, object]:
        names, discovery_ok = self._ollama_names()
        items = [
            BenchmarkModelOption(
                id=f"ollama:{name}",
                provider="ollama",
                model=name,
                label=f"Ollama｜{name}",
                ready=True,
                note="本機已安裝",
            )
            for name in names
        ]
        gemini_ready = bool(self._gemini_configured_getter())
        items.extend(
            BenchmarkModelOption(
                id=f"gemini:{model}",
                provider="gemini",
                model=model,
                label=f"Gemini｜{label}",
                ready=gemini_ready,
                note="可使用" if gemini_ready else "尚未設定 Gemini API Key",
            )
            for model, label in _GEMINI_MODELS
        )
        return {
            "items": [asdict(item) for item in items],
            "warnings": [] if discovery_ok else ["ollama_unavailable"],
        }

    def resolve(self, model_id: str) -> BenchmarkModelOption:
        if not isinstance(model_id, str) or ":" not in model_id:
            raise ValueError("unknown_model_id")
        provider, model = model_id.split(":", 1)
        items = {
            item["id"]: BenchmarkModelOption(**item)
            for item in self.public_catalog()["items"]
        }
        option = items.get(model_id)
        if (
            option is None
            or option.provider != provider
            or option.model != model
        ):
            raise ValueError("unknown_model_id")
        if not option.ready:
            raise ValueError("model_not_ready")
        return option

    def ollama_model_ready(self, model: str) -> bool:
        names, discovery_ok = self._ollama_names()
        return discovery_ok and model in names
```

- [ ] **Step 4: Run the catalog tests**

Run the command from Step 2.

Expected: all tests pass.

- [ ] **Step 5: Commit the catalog boundary**

```powershell
git add src/qingpu_insight/llm_model_catalog.py tests/test_llm_model_catalog.py
git commit -m "feat(llm): add live benchmark model catalog"
```

---

### Task 2: Exact-Model Production Benchmark Runner

**Files:**
- Create: `src/qingpu_insight/web_benchmark_runner.py`
- Create: `tests/test_web_benchmark_runner.py`
- Modify: `src/qingpu_insight/provider_ops.py`
- Modify: `tests/test_provider_ops.py`

**Interfaces:**
- Consumes: `BenchmarkRequest(provider: Literal["ollama", "gemini"], model: str)`, `OllamaReportProvider`, `GeminiReportProvider`, and `llm_benchmark.run_benchmark`.
- Produces: `BenchmarkRunner.run(provider: str, model: str, cases: list[EvidencePack], output_dir: Path) -> dict[str, Any]` and `ConfiguredWebBenchmarkRunner`.

- [ ] **Step 1: Change the service contract test so provider and exact model reach the runner**

Add to `tests/test_provider_ops.py`:

```python
from pathlib import Path

from qingpu_insight.provider_ops import BenchmarkRequest


def test_execute_benchmark_passes_provider_and_exact_model_to_runner(
    tmp_path, monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    cases = Path("benchmarks")
    cases.mkdir()
    (cases / "m44_cases.json").write_text("[]", encoding="utf-8")
    calls = []

    class Runner:
        def run(self, provider, model, evidence_cases, output_dir):
            calls.append((provider, model, evidence_cases, output_dir))
            return {"case_count": 0, "models": []}

    service = ProviderOpsService(
        rule_provider=object(),
        provider_factory=lambda _name: None,
        env={},
    )
    service.set_benchmark_runner(Runner())
    request = BenchmarkRequest(provider="ollama", model="gemma4:e2b")

    result = service.execute_benchmark("run-1", request)

    assert result["status"] == "succeeded"
    assert calls[0][0:3] == ("ollama", "gemma4:e2b", [])
    assert calls[0][3] == Path("outputs/m44-benchmark/run-1")


def test_execute_benchmark_does_not_expose_runner_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("benchmarks").mkdir()
    Path("benchmarks/m44_cases.json").write_text("[]", encoding="utf-8")

    class Runner:
        def run(self, provider, model, evidence_cases, output_dir):
            raise ConnectionError("http://private-host/?key=secret-value")

    service = ProviderOpsService(object(), lambda _name: None, {})
    service.set_benchmark_runner(Runner())

    result = service.execute_benchmark(
        "run-2",
        BenchmarkRequest(provider="gemini", model="gemma-4-31b-it"),
    )

    assert result["status"] == "failed"
    assert result["error"] == "benchmark_execution_failed"
    assert "private-host" not in repr(result)
```

This test deliberately uses a `provider_factory` that returns `None`: smoke-test provider construction remains separate, while Benchmark construction belongs to the exact-model runner.

- [ ] **Step 2: Run that test and observe the old one-argument runner call**

Run:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
& 'C:\Users\cygnu\Documents\Codex\qingpu-insight\.venv\Scripts\python.exe' -m pytest tests/test_provider_ops.py::test_execute_benchmark_passes_provider_and_exact_model_to_runner -q
```

Expected: fail because `execute_benchmark` either returns `<provider>_unavailable` or calls `runner.run` without provider.

- [ ] **Step 3: Update the protocol and service call**

In `src/qingpu_insight/provider_ops.py`, change the protocol and the execution call to:

```python
class BenchmarkRunner(Protocol):
    def run(
        self,
        provider: str,
        model: str,
        cases: list[EvidencePack],
        output_dir: Path,
    ) -> dict[str, Any]:
        ...
```

Remove the `provider = self._provider_factory(...)` availability block from `execute_benchmark`, then call:

```python
result = runner.run(
    request.provider,
    request.model,
    cases,
    output_dir,
)
```

In the `execute_benchmark` exception branch, return the stable public error
`benchmark_execution_failed` instead of `_redacted_error(e)`. Do not alter
`_smoke_ai`; provider smoke tests must continue using `_provider_factory`.

- [ ] **Step 4: Run all provider operation tests**

Run:

```powershell
& 'C:\Users\cygnu\Documents\Codex\qingpu-insight\.venv\Scripts\python.exe' -m pytest tests/test_provider_ops.py -q
```

Expected: all tests pass after updating existing fake runners to the four-argument signature.

- [ ] **Step 5: Add runner tests that prove exact provider construction**

Create `tests/test_web_benchmark_runner.py`:

```python
from pathlib import Path

import pytest

from qingpu_insight.web_benchmark_runner import ConfiguredWebBenchmarkRunner


def test_runner_constructs_selected_ollama_model(tmp_path):
    from types import SimpleNamespace

    constructed = []
    observed = {}
    selected_provider = object()

    def ollama_factory(base_url, model):
        constructed.append((base_url, model))
        return selected_provider

    def benchmark(cases, providers, output_dir, **metadata):
        observed.update({
            "cases": cases,
            "providers": providers,
            "output_dir": output_dir,
            **metadata,
        })
        return (
            [
                SimpleNamespace(
                    schema_success=True,
                    fact_accuracy=1.0,
                    required_section_coverage=0.8,
                ),
                SimpleNamespace(
                    schema_success=False,
                    fact_accuracy=0.5,
                    required_section_coverage=1.0,
                ),
            ],
            [{
                "model": "gemma4:e2b",
                "p50_latency": 120.0,
                "p95_latency": 250.0,
            }],
        )

    runner = ConfiguredWebBenchmarkRunner(
        ollama_base_url_getter=lambda: "http://127.0.0.1:11434",
        gemini_api_key_getter=lambda: "",
        ollama_factory=ollama_factory,
        gemini_factory=lambda _key, _model: object(),
        benchmark=benchmark,
    )

    result = runner.run("ollama", "gemma4:e2b", [], tmp_path)

    assert constructed == [("http://127.0.0.1:11434", "gemma4:e2b")]
    assert observed["providers"] == {"ollama:gemma4:e2b": selected_provider}
    assert observed["requested_provider"] == "ollama"
    assert observed["requested_model"] == "gemma4:e2b"
    assert result["case_count"] == 2
    assert result["schema_success"] == 0.5
    assert result["fact_accuracy"] == 0.75
    assert result["required_section_success"] == 0.9
    assert result["p50_latency_ms"] == 120.0
    assert result["p95_latency_ms"] == 250.0


def test_runner_uses_dynamic_gemini_key_and_rejects_unknown_provider(tmp_path):
    keys = iter(["first-key", "rotated-key"])
    constructed = []
    runner = ConfiguredWebBenchmarkRunner(
        ollama_base_url_getter=lambda: "http://127.0.0.1:11434",
        gemini_api_key_getter=lambda: next(keys),
        ollama_factory=lambda _base, _model: object(),
        gemini_factory=lambda key, model: constructed.append((key, model)) or object(),
        benchmark=lambda *_args, **_kwargs: ([], []),
    )

    runner.run("gemini", "gemini-3.5-flash-lite", [], tmp_path)
    runner.run("gemini", "gemma-4-31b-it", [], tmp_path)

    assert constructed == [
        ("first-key", "gemini-3.5-flash-lite"),
        ("rotated-key", "gemma-4-31b-it"),
    ]
    with pytest.raises(ValueError, match="unsupported_benchmark_provider"):
        runner.run("rule", "rule", [], tmp_path)
```

- [ ] **Step 6: Run the runner tests and confirm the module is missing**

Run:

```powershell
& 'C:\Users\cygnu\Documents\Codex\qingpu-insight\.venv\Scripts\python.exe' -m pytest tests/test_web_benchmark_runner.py -q
```

Expected: collection fails with `ModuleNotFoundError`.

- [ ] **Step 7: Implement the production adapter**

Create `src/qingpu_insight/web_benchmark_runner.py` with injectable factories for deterministic tests. Use production defaults:

```python
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from qingpu_insight.gemini_report_provider import GeminiReportProvider
from qingpu_insight.llm_benchmark import run_benchmark
from qingpu_insight.ollama_report_provider import OllamaReportProvider
from qingpu_insight.report_contracts import EvidencePack


class ConfiguredWebBenchmarkRunner:
    def __init__(
        self,
        *,
        ollama_base_url_getter: Callable[[], str],
        gemini_api_key_getter: Callable[[], str],
        ollama_factory: Callable[[str, str], object] | None = None,
        gemini_factory: Callable[[str, str], object] | None = None,
        benchmark: Callable[..., tuple[list[Any], list[Any]]] = run_benchmark,
    ) -> None:
        self._ollama_base_url_getter = ollama_base_url_getter
        self._gemini_api_key_getter = gemini_api_key_getter
        self._ollama_factory = ollama_factory or (
            lambda base_url, model: OllamaReportProvider(
                base_url=base_url,
                model=model,
            )
        )
        self._gemini_factory = gemini_factory or (
            lambda api_key, model: GeminiReportProvider(
                api_key=api_key,
                model=model,
            )
        )
        self._benchmark = benchmark

    def run(
        self,
        provider: str,
        model: str,
        cases: list[EvidencePack],
        output_dir: Path,
    ) -> dict[str, Any]:
        if provider == "ollama":
            selected = self._ollama_factory(
                self._ollama_base_url_getter(),
                model,
            )
        elif provider == "gemini":
            api_key = self._gemini_api_key_getter()
            if not api_key:
                raise ValueError("gemini_api_key_not_configured")
            selected = self._gemini_factory(api_key, model)
        else:
            raise ValueError("unsupported_benchmark_provider")
        provider_id = f"{provider}:{model}"
        results, summaries = self._benchmark(
            cases,
            {provider_id: selected},
            output_dir,
            requested_provider=provider,
            requested_model=model,
        )
        summary_dicts = [
            summary
            if isinstance(summary, dict)
            else asdict(summary)
            if is_dataclass(summary)
            else raise_type_error(summary)
            for summary in summaries
        ]
        count = len(results)
        first_summary = summary_dicts[0] if summary_dicts else {}
        return {
            "case_count": count,
            "schema_success": (
                sum(bool(result.schema_success) for result in results) / count
                if count else 0.0
            ),
            "fact_accuracy": (
                sum(result.fact_accuracy for result in results) / count
                if count else 0.0
            ),
            "required_section_success": (
                sum(result.required_section_coverage for result in results) / count
                if count else 0.0
            ),
            "p50_latency_ms": first_summary.get("p50_latency"),
            "p95_latency_ms": first_summary.get("p95_latency"),
            "models": summary_dicts,
        }


def raise_type_error(value: object) -> dict[str, Any]:
    raise TypeError(f"unsupported_benchmark_summary:{type(value).__name__}")
```

The result must contain only JSON-safe counts and summary dictionaries. The selected provider object is captured inside the test double and must never reach job JSON.

- [ ] **Step 8: Run runner and provider tests**

Run:

```powershell
& 'C:\Users\cygnu\Documents\Codex\qingpu-insight\.venv\Scripts\python.exe' -m pytest tests/test_web_benchmark_runner.py tests/test_provider_ops.py -q
```

Expected: all tests pass and no provider object appears in `ConfiguredWebBenchmarkRunner.run()` output.

- [ ] **Step 9: Commit exact-model Benchmark execution**

```powershell
git add src/qingpu_insight/provider_ops.py src/qingpu_insight/web_benchmark_runner.py tests/test_provider_ops.py tests/test_web_benchmark_runner.py
git commit -m "fix(llm): execute benchmarks with selected model"
```

---

### Task 3: Admin Catalog API and Fail-Closed Benchmark Submission

**Files:**
- Modify: `src/qingpu_insight/admin_web.py`
- Modify: `src/qingpu_insight/web.py`
- Modify: `tests/test_web.py`

**Interfaces:**
- Consumes: `LlmModelCatalog.public_catalog()`, `LlmModelCatalog.resolve(model_id)`, `ConfiguredWebBenchmarkRunner`, existing `JobService`, and `ProviderOpsService`.
- Produces: `GET /api/admin/llm-models` and the existing `POST /api/admin/llm-benchmark-runs` accepting exactly `{model_id: str}`, with a real tracked `llm_benchmark` job.

- [ ] **Step 1: Add admin HTTP contract tests**

In the existing admin test fixture section in `tests/test_web.py`, give `AdminRuntime` a fake catalog:

```python
class FakeLlmModelCatalog:
    def public_catalog(self):
        return {
            "items": [{
                "id": "ollama:gemma4:e2b",
                "provider": "ollama",
                "model": "gemma4:e2b",
                "label": "Ollama｜gemma4:e2b",
                "ready": True,
                "note": "本機已安裝",
            }],
            "warnings": [],
        }

    def resolve(self, model_id):
        if model_id != "ollama:gemma4:e2b":
            raise ValueError("unknown_model_id")
        from qingpu_insight.llm_model_catalog import BenchmarkModelOption
        return BenchmarkModelOption(
            id=model_id,
            provider="ollama",
            model="gemma4:e2b",
            label="Ollama｜gemma4:e2b",
            ready=True,
            note="本機已安裝",
        )
```

Update the existing `_StubBenchmarkRunner` in the same file to accept
`run(self, provider, model, cases, output_dir)` and assert the received
provider/model in `test_benchmark_submit_success`.

Add focused tests using the existing CSRF helper:

```python
def test_admin_llm_models_returns_only_safe_catalog(admin_client):
    response = admin_client.get("/api/admin/llm-models")

    assert response.status_code == 200
    assert response.get_json()["items"][0]["id"] == "ollama:gemma4:e2b"
    assert "api_key" not in response.get_data(as_text=True).lower()
    assert "digest" not in response.get_data(as_text=True).lower()


def test_admin_benchmark_accepts_only_catalog_model_id(admin_client, csrf_headers):
    response = admin_client.post(
        "/api/admin/llm-benchmark-runs",
        json={"model_id": "ollama:gemma4:e2b"},
        headers=csrf_headers,
    )

    assert response.status_code == 202
    assert response.get_json()["provider"] == "ollama"
    assert response.get_json()["model"] == "gemma4:e2b"


def test_admin_benchmark_rejects_unknown_or_spoofed_model(admin_client, csrf_headers):
    unknown = admin_client.post(
        "/api/admin/llm-benchmark-runs",
        json={"model_id": "ollama:not-installed"},
        headers=csrf_headers,
    )
    spoofed = admin_client.post(
        "/api/admin/llm-benchmark-runs",
        json={
            "model_id": "ollama:gemma4:e2b",
            "provider": "gemini",
            "model": "gemma-4-31b-it",
        },
        headers=csrf_headers,
    )

    assert unknown.status_code == 400
    assert unknown.get_json()["error"]["fields"] == {"model_id": "unsupported"}
    assert spoofed.status_code == 400
    assert set(spoofed.get_json()["error"]["fields"]) == {"provider", "model"}
```

- [ ] **Step 2: Run the focused admin tests**

Run:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
& 'C:\Users\cygnu\Documents\Codex\qingpu-insight\.venv\Scripts\python.exe' -m pytest tests/test_web.py -q -k "admin_llm_models or admin_benchmark"
```

Expected: fail because the catalog route/runtime field do not exist and the POST still expects provider/model.

- [ ] **Step 3: Extend `AdminRuntime` and add the safe catalog route**

Add to `AdminRuntime`:

```python
llm_model_catalog: object | None = None
```

Add before the Benchmark POST route:

```python
@bp.get("/api/admin/llm-models")
def admin_llm_models():
    rt = current_app.extensions.get("qingpu_admin_runtime")
    if rt is None or rt.llm_model_catalog is None:
        return jsonify({"error": {
            "code": "model_catalog_unavailable",
            "message": "模型清單暫時無法取得。",
        }}), 503
    return jsonify(rt.llm_model_catalog.public_catalog())
```

Do not catch and return raw exceptions; `public_catalog()` already converts Ollama failures to the safe `ollama_unavailable` warning.

- [ ] **Step 4: Replace the Benchmark POST parser**

At the start of the existing Benchmark submission route, enforce the exact JSON shape:

```python
payload = request.get_json(silent=True)
if not isinstance(payload, dict):
    return jsonify({"error": {
        "code": "invalid_request",
        "message": "Request validation failed.",
        "fields": {"model_id": "required"},
    }}), 400

extra = sorted(set(payload) - {"model_id"})
if extra:
    return jsonify({"error": {
        "code": "invalid_request",
        "message": "Request validation failed.",
        "fields": {field: "unsupported" for field in extra},
    }}), 400

model_id = payload.get("model_id")
if not isinstance(model_id, str) or not model_id:
    return jsonify({"error": {
        "code": "invalid_request",
        "message": "Request validation failed.",
        "fields": {"model_id": "required"},
    }}), 400
try:
    option = rt.llm_model_catalog.resolve(model_id)
except ValueError:
    return jsonify({"error": {
        "code": "invalid_request",
        "message": "Request validation failed.",
        "fields": {"model_id": "unsupported"},
    }}), 400
benchmark_request = BenchmarkRequest(
    provider=option.provider,
    model=option.model,
)
```

Delete the old provider readiness lookup and arbitrary `model` parsing.

Add a completion helper beside `_complete_provider_smoke_job` so the executor updates the same job that the UI polls:

```python
def _complete_llm_benchmark_job(
    runtime: AdminRuntime,
    run_id: str,
    benchmark_request: BenchmarkRequest,
) -> None:
    result = runtime.provider_ops_service.execute_benchmark(
        run_id,
        benchmark_request,
    )
    if result.get("status") == "succeeded":
        summary = {
            key: value
            for key, value in result.items()
            if key not in {"run_id", "status"}
        }
        runtime.job_service.succeed(
            run_id,
            f"{benchmark_request.provider}:{benchmark_request.model}",
            summary,
        )
        return
    runtime.job_service.fail(
        run_id,
        "llm_benchmark_failed",
        str(result.get("error") or "LLM benchmark failed"),
    )
```

Import `BenchmarkRequest` at module level and require `job_service`, `executor`, `provider_ops_service`, and `llm_model_catalog` before submission. Replace the untracked `provider_ops_service.submit_benchmark()` call with:

```python
submission = rt.job_service.create(
    "llm_benchmark",
    f"llm_benchmark:{option.id}:active",
    "web",
    input_version=option.id,
)
if submission.created:
    rt.executor.submit(
        submission.run.run_id,
        lambda sid=submission.run.run_id, req=benchmark_request: (
            _complete_llm_benchmark_job(rt, sid, req)
        ),
    )
body = _admin_public_job(submission.run)
body["created"] = submission.created
body["provider"] = option.provider
body["model"] = option.model
return jsonify(body), 202 if submission.created else 200
```

If executor submission raises, mark that run failed with stable code `enqueue_failed` and return the existing safe 503 response pattern used by provider smoke.
`LocalJobExecutor` performs the `pending → running` transition before invoking
the helper, so the helper must not call `job_service.start()` a second time.

- [ ] **Step 5: Run all admin web tests**

Run:

```powershell
& 'C:\Users\cygnu\Documents\Codex\qingpu-insight\.venv\Scripts\python.exe' -m pytest tests/test_web.py -q
```

Expected: all tests pass after converting old Benchmark request fixtures to `{"model_id": "ollama:gemma4:e2b"}` and supplying the fake catalog.

- [ ] **Step 6: Add a composition test before wiring production**

Add a test around the existing app factory fixture:

```python
def test_web_app_wires_catalog_and_benchmark_runner(app):
    runtime = app.extensions["qingpu_admin_runtime"]

    assert runtime.llm_model_catalog is not None
    assert runtime.provider_ops_service._benchmark_runner is not None
```

Run:

```powershell
& 'C:\Users\cygnu\Documents\Codex\qingpu-insight\.venv\Scripts\python.exe' -m pytest tests/test_web.py::test_web_app_wires_catalog_and_benchmark_runner -q
```

Expected: fail because production currently never installs the runner.

- [ ] **Step 7: Wire one shared catalog and the real runner in `web.py`**

Import:

```python
from qingpu_insight.llm_model_catalog import LlmModelCatalog
from qingpu_insight.web_benchmark_runner import ConfiguredWebBenchmarkRunner
```

Near the current dynamic secrets/provider construction, define getters that read current state on every call:

```python
def get_runtime_env(name: str, default: str = "") -> str:
    current = (
        secrets_store.merged_env(os.environ)
        if secrets_store is not None
        else dict(os.environ)
    )
    return current.get(name, default)


llm_model_catalog = LlmModelCatalog(
    ollama_base_url_getter=lambda: get_runtime_env(
        "QINGPU_OLLAMA_BASE_URL",
        "http://127.0.0.1:11434",
    ),
    gemini_configured_getter=lambda: bool(
        get_runtime_env("QINGPU_GEMINI_API_KEY")
    ),
)
if provider_ops_service is not None:
    provider_ops_service.set_benchmark_runner(ConfiguredWebBenchmarkRunner(
        ollama_base_url_getter=lambda: get_runtime_env(
            "QINGPU_OLLAMA_BASE_URL",
            "http://127.0.0.1:11434",
        ),
        gemini_api_key_getter=lambda: get_runtime_env(
            "QINGPU_GEMINI_API_KEY"
        ),
    ))
```

Pass `llm_model_catalog=llm_model_catalog` in every `AdminRuntime(...)` construction, including degraded/test composition branches where services are optional. Keep `provider_factory` unchanged for provider smoke tests.

- [ ] **Step 8: Run composition and full web tests**

Run:

```powershell
& 'C:\Users\cygnu\Documents\Codex\qingpu-insight\.venv\Scripts\python.exe' -m pytest tests/test_web.py tests/test_provider_ops.py tests/test_web_benchmark_runner.py -q
```

Expected: all tests pass.

- [ ] **Step 9: Commit the admin API and production runner wiring**

```powershell
git add src/qingpu_insight/admin_web.py src/qingpu_insight/web.py tests/test_web.py
git commit -m "feat(admin): expose safe benchmark model catalog"
```

---

### Task 4: Admin Benchmark Single-Select UI

**Files:**
- Modify: `src/qingpu_insight/templates/admin.html`
- Modify: `src/qingpu_insight/static/admin.js`
- Modify: `tests/js/admin_contract.cjs`

**Interfaces:**
- Consumes: `GET /api/admin/llm-models` and `POST /api/admin/llm-benchmark-runs` with `{model_id: string}`.
- Produces: exported `buildBenchmarkPayload(modelId)`, `benchmarkModelHelp(option)`, `canRunBenchmark(option)`, and one catalog-backed select.

- [ ] **Step 1: Add pure JavaScript contract assertions**

Before the asynchronous listing-sequence test in `tests/js/admin_contract.cjs`, add:

```javascript
assert.deepEqual(
  admin.buildBenchmarkPayload("ollama:gemma4:e2b"),
  { model_id: "ollama:gemma4:e2b" }
);
assert.equal(
  admin.benchmarkModelHelp({
    provider: "ollama",
    ready: true,
    note: "本機已安裝",
  }),
  "本機已安裝"
);
assert.equal(admin.canRunBenchmark({ ready: true }), true);
assert.equal(admin.canRunBenchmark({ ready: false }), false);
assert.equal(admin.canRunBenchmark(null), false);
```

- [ ] **Step 2: Run the contract and observe missing exports**

Run:

```powershell
node tests/js/admin_contract.cjs
```

Expected: fail because `buildBenchmarkPayload` is not exported.

- [ ] **Step 3: Implement the pure functions and export them**

In the pure-function section of `admin.js`:

```javascript
function buildBenchmarkPayload(modelId) {
  return { model_id: modelId };
}

function benchmarkModelHelp(option) {
  return option && typeof option.note === "string" ? option.note : "";
}

function canRunBenchmark(option) {
  return Boolean(option && option.ready === true);
}
```

Add all three to the CommonJS/browser API returned at the end of `admin.js`.

- [ ] **Step 4: Replace the admin template controls**

Replace `benchmark-provider-select` and `benchmark-model-input` with:

```html
<label for="benchmark-model-select">模型</label>
<select id="benchmark-model-select" class="admin-input" disabled>
  <option value="">正在載入模型清單…</option>
</select>
<button type="button" id="benchmark-model-refresh" class="admin-btn">
  重新整理模型清單
</button>
<button type="button" id="benchmark-run-btn" class="admin-btn" disabled>
  執行 Benchmark
</button>
<p id="benchmark-model-help" class="admin-help" aria-live="polite"></p>
<div id="benchmark-result" aria-live="polite"></div>
```

Confirm with:

```powershell
rg -n "benchmark-provider-select|benchmark-model-input" src/qingpu_insight
```

Expected: no matches.

- [ ] **Step 5: Implement safe catalog loading and rendering**

Add module state and functions in `admin.js`:

```javascript
var benchmarkModels = [];

function selectedBenchmarkModel() {
  var select = document.getElementById("benchmark-model-select");
  if (!select) return null;
  return benchmarkModels.find(function (item) {
    return item.id === select.value;
  }) || null;
}

function updateBenchmarkSelection() {
  var help = document.getElementById("benchmark-model-help");
  var runButton = document.getElementById("benchmark-run-btn");
  var selected = selectedBenchmarkModel();
  if (help) help.textContent = benchmarkModelHelp(selected);
  if (runButton) runButton.disabled = !canRunBenchmark(selected);
}

function renderBenchmarkModels(catalog) {
  var select = document.getElementById("benchmark-model-select");
  var help = document.getElementById("benchmark-model-help");
  if (!select) return;
  benchmarkModels = Array.isArray(catalog.items) ? catalog.items : [];
  select.replaceChildren();
  benchmarkModels.forEach(function (item) {
    var option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.label;
    option.disabled = item.ready !== true;
    select.appendChild(option);
  });
  select.disabled = benchmarkModels.length === 0;
  var firstReady = benchmarkModels.find(function (item) {
    return item.ready === true;
  });
  select.value = firstReady ? firstReady.id : "";
  if (help && Array.isArray(catalog.warnings)
      && catalog.warnings.includes("ollama_unavailable")) {
    help.textContent = "無法連線本機 Ollama；Gemini 模型仍可使用。";
  }
  updateBenchmarkSelection();
}

function loadBenchmarkModels() {
  var select = document.getElementById("benchmark-model-select");
  var runButton = document.getElementById("benchmark-run-btn");
  var help = document.getElementById("benchmark-model-help");
  if (select) select.disabled = true;
  if (runButton) runButton.disabled = true;
  if (help) help.textContent = "正在載入模型清單…";
  return fetch("/api/admin/llm-models")
    .then(function (response) {
      if (!response.ok) throw new Error("catalog_load_failed");
      return response.json();
    })
    .then(renderBenchmarkModels)
    .catch(function () {
      benchmarkModels = [];
      if (select) select.replaceChildren();
      if (help) help.textContent = "模型清單暫時無法載入，請稍後重試。";
    });
}
```

All upstream-derived strings are assigned through `textContent`; do not use `innerHTML`.

- [ ] **Step 6: Change Benchmark submission to model ID only**

Replace the beginning of `runBenchmark()`:

```javascript
var selected = selectedBenchmarkModel();
if (!canRunBenchmark(selected)) return;
var payload = buildBenchmarkPayload(selected.id);
```

Keep the existing CSRF, job creation, polling, and result rendering. Remove all reads of provider select/free model input.

In the successful polling branch, change `statusEl.innerHTML = ...` to
`statusEl.textContent = ...`; even numeric Benchmark summaries must use the
same text-only rendering rule as catalog notes and errors.

Wire on DOM ready:

```javascript
var benchmarkSelect = document.getElementById("benchmark-model-select");
if (benchmarkSelect) {
  benchmarkSelect.addEventListener("change", updateBenchmarkSelection);
}
var benchmarkRefresh = document.getElementById("benchmark-model-refresh");
if (benchmarkRefresh) {
  benchmarkRefresh.addEventListener("click", loadBenchmarkModels);
}
loadBenchmarkModels();
```

- [ ] **Step 7: Run the admin contract and inspect static references**

Run:

```powershell
node tests/js/admin_contract.cjs
rg -n "benchmark-provider-select|benchmark-model-input|provider.*model.*llm-benchmark-runs" src/qingpu_insight/static src/qingpu_insight/templates
```

Expected: contract passes; the removed control IDs have no matches.

- [ ] **Step 8: Commit the admin selector**

```powershell
git add src/qingpu_insight/templates/admin.html src/qingpu_insight/static/admin.js tests/js/admin_contract.cjs
git commit -m "feat(admin): add live benchmark model selector"
```

---

### Task 5: Homepage Ollama Readiness in the Fixed Selector

**Files:**
- Modify: `src/qingpu_insight/conversation_models.py`
- Modify: `src/qingpu_insight/web.py`
- Modify: `src/qingpu_insight/static/home_assistant.js`
- Modify: `tests/test_conversation_models.py`
- Modify: `tests/test_conversation_web.py`
- Modify: `tests/js/home_assistant_contract.cjs`

**Interfaces:**
- Consumes: `LlmModelCatalog.ollama_model_ready("gemma4:e2b")`.
- Produces: `public_model_catalog(*, gemini_configured: bool, ollama_ready: bool)` with top-level `ollama_ready`, while retaining the existing four fixed items.

- [ ] **Step 1: Update the Python catalog tests**

Change calls in `tests/test_conversation_models.py` to:

```python
catalog = public_model_catalog(
    gemini_configured=True,
    ollama_ready=False,
)
assert catalog["gemini_configured"] is True
assert catalog["ollama_ready"] is False
assert [item["id"] for item in catalog["items"]] == [
    "gemini-3.5-flash-lite",
    "gemma-4-31b-it",
    "gemma4:e2b",
    "rule",
]
```

Update the conversation route fixture expectation in `tests/test_conversation_web.py`:

```python
assert response.get_json()["ollama_ready"] is False
```

- [ ] **Step 2: Run the focused Python tests**

Run:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
& 'C:\Users\cygnu\Documents\Codex\qingpu-insight\.venv\Scripts\python.exe' -m pytest tests/test_conversation_models.py tests/test_conversation_web.py -q
```

Expected: fail because `public_model_catalog` does not accept or return `ollama_ready`.

- [ ] **Step 3: Add readiness to the public conversation catalog**

Change the function signature and response:

```python
def public_model_catalog(
    *,
    gemini_configured: bool,
    ollama_ready: bool,
) -> dict[str, object]:
    return {
        "default_model": DEFAULT_CONVERSATION_MODEL,
        "gemini_configured": gemini_configured,
        "ollama_ready": ollama_ready,
        "items": [asdict(model) for model in PUBLIC_CONVERSATION_MODELS],
    }
```

Update every call site. In production `web.py`, use the shared live catalog:

```python
catalog_getter=lambda: public_model_catalog(
    gemini_configured=bool(get_runtime_env("QINGPU_GEMINI_API_KEY")),
    ollama_ready=llm_model_catalog.ollama_model_ready("gemma4:e2b"),
)
```

- [ ] **Step 4: Run the focused Python tests**

Run the Step 2 command.

Expected: all tests pass.

- [ ] **Step 5: Add homepage status contract assertions**

Replace the Ollama assertion in `tests/js/home_assistant_contract.cjs` and add the ready case:

```javascript
assert.equal(
  ha.modelStatusText(
    { provider: "ollama", cloud: false },
    { geminiConfigured: true, ollamaReady: false }
  ),
  "本機 Gemma 4 尚未安裝；送出後可能改用 Rule 摘要"
);
assert.equal(
  ha.modelStatusText(
    { provider: "ollama", cloud: false },
    { geminiConfigured: true, ollamaReady: true }
  ),
  "本機模式，不使用 Google API"
);
```

Update the Gemini assertion to pass:

```javascript
{ geminiConfigured: false, ollamaReady: true }
```

- [ ] **Step 6: Run the homepage contract and observe the old signature**

Run:

```powershell
node tests/js/home_assistant_contract.cjs
```

Expected: fail because the old function treats the readiness object as truthy and does not inspect `ollamaReady`.

- [ ] **Step 7: Implement readiness-aware status text**

Change:

```javascript
function modelStatusText(item, readiness) {
  if (!item) return "";
  var state = readiness || {};
  if (item.cloud && !state.geminiConfigured) {
    return "尚未設定 Gemini API Key；送出後將自動使用本機模型";
  }
  if (item.provider === "ollama" && !state.ollamaReady) {
    return "本機 Gemma 4 尚未安裝；送出後可能改用 Rule 摘要";
  }
  if (item.provider === "ollama") {
    return "本機模式，不使用 Google API";
  }
  if (item.provider === "rule") {
    return "離線摘要，不使用 LLM";
  }
  return "雲端模型；失敗時會自動切換本機模型";
}
```

In `renderModelCatalog`, call it with:

```javascript
modelStatusText(selected, {
  geminiConfigured: Boolean(catalog.gemini_configured),
  ollamaReady: Boolean(catalog.ollama_ready),
})
```

Do not add a provider selector or text input.

- [ ] **Step 8: Run homepage tests and assert the DOM remains fixed**

Run:

```powershell
node tests/js/home_assistant_contract.cjs
rg -n "assistant-provider|assistant-model-input" src/qingpu_insight/templates src/qingpu_insight/static
& 'C:\Users\cygnu\Documents\Codex\qingpu-insight\.venv\Scripts\python.exe' -m pytest tests/test_conversation_models.py tests/test_conversation_web.py tests/test_web.py -q
```

Expected: JavaScript and Python tests pass; the forbidden free-form controls have no matches.

- [ ] **Step 9: Commit homepage readiness**

```powershell
git add src/qingpu_insight/conversation_models.py src/qingpu_insight/web.py src/qingpu_insight/static/home_assistant.js tests/test_conversation_models.py tests/test_conversation_web.py tests/js/home_assistant_contract.cjs
git commit -m "feat(assistant): show live local model readiness"
```

---

### Task 6: 404-Only Map Compatibility Mode

**Files:**
- Modify: `src/qingpu_insight/static/market_map.mjs`
- Modify: `src/qingpu_insight/static/app.js`
- Modify: `tests/js/market_map_contract.mjs`

**Interfaces:**
- Consumes: primary `GET /api/market/map-points?...viewport...` and fallback `GET /api/transactions?...filters...&limit=100`.
- Produces: `transactionItemsToMapPayload(items)`, validated grouped payloads, `mode: "compatibility"`, and the required compatibility status text.

- [ ] **Step 1: Replace the map contract with explicit normal and fallback cases**

Retain the current parameter/radius assertions and replace the existing import declaration with:

```javascript
import {
  createMapLoader,
  mapStatusText,
  markerRadius,
  transactionItemsToMapPayload,
  withMapView,
  withRecentLimit,
} from "../../src/qingpu_insight/static/market_map.mjs";
```

Then add:

```javascript
const converted = transactionItemsToMapPayload([
  {
    id: "valid",
    latitude: 25.01,
    longitude: 121.21,
    total_price: 1800,
    transaction_date: "2026-07-01",
  },
  {id: "nan", latitude: "not-a-number", longitude: 121.22},
  {id: "missing", latitude: null, longitude: null},
]);
assert.equal(converted.mode, "compatibility");
assert.equal(converted.total_records, 3);
assert.equal(converted.located_records, 1);
assert.equal(converted.unlocated_records, 2);
assert.equal(converted.group_count, 1);
assert.equal(converted.items[0].record_count, 1);

const calls = [];
let compatibilityPayload = null;
const fallbackLoader = createMapLoader({
  fetchImpl: async function (url) {
    calls.push(url);
    if (calls.length === 1) {
      return {ok: false, status: 404};
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({
        items: [{
          id: "tx-1",
          latitude: 25.01,
          longitude: 121.21,
          total_price: 1800,
          transaction_date: "2026-07-01",
        }],
      }),
    };
  },
  render: (payload) => { compatibilityPayload = payload; },
  showError: () => assert.fail("404 compatibility path must render"),
});
await fallbackLoader(base, {
  zoom: 14, south: 24.9, west: 121.0, north: 25.1, east: 121.3,
});
assert.match(calls[0], /^\/api\/market\/map-points\?/);
assert.equal(
  calls[1],
  "/api/transactions?transaction_type=resale&station=A17&station=A18&limit=100"
);
assert.equal(compatibilityPayload.mode, "compatibility");
assert.match(mapStatusText(compatibilityPayload), /^相容模式：/);
assert.match(mapStatusText(compatibilityPayload), /最近 100 筆/);
assert.match(mapStatusText(compatibilityPayload), /重新啟動 Web/);
```

Add non-fallback and malformed cases:

```javascript
for (const response of [
  {ok: false, status: 500},
  {ok: true, status: 200, json: async () => ({items: "invalid"})},
]) {
  let fetchCount = 0;
  let error = "";
  const loaderUnderTest = createMapLoader({
    fetchImpl: async () => { fetchCount += 1; return response; },
    render: () => assert.fail("invalid primary response must not render"),
    showError: (message) => { error = message; },
  });
  assert.equal(await loaderUnderTest(base, {
    zoom: 14, south: 24.9, west: 121.0, north: 25.1, east: 121.3,
  }), null);
  assert.equal(fetchCount, 1);
  assert.match(error, /^地圖資料載入失敗：/);
}
```

Keep the existing AbortError assertion or add one that verifies no error is shown after the preceding request is aborted.

- [ ] **Step 2: Run the map contract and observe missing compatibility conversion**

Run:

```powershell
node tests/js/market_map_contract.mjs
```

Expected: module import fails because `transactionItemsToMapPayload` is not exported.

- [ ] **Step 3: Add strict payload conversion and validation**

In `market_map.mjs`:

```javascript
const COMPATIBILITY_MESSAGE =
  "相容模式：後端版本較舊，目前顯示最近 100 筆；" +
  "重新啟動 Web 後可顯示完整群組地圖";

function finiteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function validateGroupedPayload(payload) {
  if (!payload || typeof payload !== "object"
      || !Array.isArray(payload.items)
      || !Number.isFinite(payload.total_records)
      || !Number.isFinite(payload.located_records)
      || !Number.isFinite(payload.unlocated_records)
      || !Number.isFinite(payload.group_count)) {
    throw new Error("map invalid response");
  }
  return payload;
}

export function transactionItemsToMapPayload(items) {
  if (!Array.isArray(items)) throw new Error("transactions invalid response");
  const located = items.filter(function (item) {
    return item && finiteNumber(item.latitude) && finiteNumber(item.longitude);
  }).map(function (item) {
    return {
      latitude: item.latitude,
      longitude: item.longitude,
      record_count: 1,
      median_total_price: item.total_price ?? null,
      latest_transaction_date: item.transaction_date ?? null,
    };
  });
  return {
    mode: "compatibility",
    total_records: items.length,
    located_records: located.length,
    unlocated_records: items.length - located.length,
    group_count: located.length,
    items: located,
  };
}
```

At the start of `mapStatusText`:

```javascript
if (payload && payload.mode === "compatibility") {
  return COMPATIBILITY_MESSAGE;
}
```

- [ ] **Step 4: Implement 404-only loader fallback**

Replace the fetch section in `createMapLoader` with:

```javascript
const primary = await fetchImpl(
  "/api/market/map-points?" + params.toString(),
  {signal: controller.signal}
);
let payload;
if (primary.status === 404) {
  const recentParams = withRecentLimit(baseParams);
  const fallback = await fetchImpl(
    "/api/transactions?" + recentParams.toString(),
    {signal: controller.signal}
  );
  if (!fallback.ok) throw new Error("transactions " + fallback.status);
  const recentPayload = await fallback.json();
  payload = transactionItemsToMapPayload(recentPayload.items);
} else {
  if (!primary.ok) throw new Error("map " + primary.status);
  payload = validateGroupedPayload(await primary.json());
}
render(payload);
return payload;
```

Keep the existing AbortError handling. JSON parsing failures must fall through to `showError` and must not trigger a second fallback.

- [ ] **Step 5: Clear stale markers and show compatibility status in `app.js`**

At the first executable line of `renderMap(payload)`, before iterating items:

```javascript
markerLayer.clearLayers();
```

Remove any later duplicate `clearLayers()` call. Continue to render `payload.items` through the current circle-marker logic and assign:

```javascript
mapStatus.textContent = marketMapUi.mapStatusText(payload);
```

In the loader `showError` callback, clear the layer before showing the error:

```javascript
markerLayer.clearLayers();
mapStatus.textContent = message;
```

This ensures an error never leaves markers from an older filter selection visible.

- [ ] **Step 6: Run the full map contract**

Run:

```powershell
node tests/js/market_map_contract.mjs
```

Expected: all normal, 404 compatibility, 500, malformed, and abort assertions pass.

- [ ] **Step 7: Commit the permanent compatibility behaviour**

```powershell
git add src/qingpu_insight/static/market_map.mjs src/qingpu_insight/static/app.js tests/js/market_map_contract.mjs
git commit -m "fix(map): add explicit old-backend compatibility mode"
```

---

### Task 7: Documentation, Security Checks, and Real Acceptance

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: all completed APIs and UI from Tasks 1–6.
- Produces: operator-facing startup/restart guidance and a verified release candidate.

- [ ] **Step 1: Document the visible workflows and version-tear explanation**

Add a concise README section containing these exact operational facts:

```markdown
### LLM 模型與 Benchmark

- 首頁物件助理固定提供兩個 Gemini 模型、本機 `gemma4:e2b` 與 Rule。
- 管理中心的 Benchmark 模型清單會即時讀取 Ollama `/api/tags`，並固定列出
  `gemini-3.5-flash-lite`、`gemma-4-31b-it`；不接受任意模型名稱。
- 安裝或刪除 Ollama 模型後，按「重新整理模型清單」即可，不必重啟 Web。
- Gemini API Key 由管理中心儲存；不得把 Key 寫進 README、命令列或 Git。

### 地圖相容模式

正式地圖使用 `/api/market/map-points` 顯示完整聚合資料。若頁面顯示
「相容模式」，表示瀏覽器已讀到新版 JavaScript，但執行中的 Flask process
仍是舊版；此時只顯示最近 100 筆有效座標。停止舊 process 並重新啟動
`qingpu-web` 後，即可恢復完整群組地圖。
```

- [ ] **Step 2: Run focused Python suites**

Run:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$python = 'C:\Users\cygnu\Documents\Codex\qingpu-insight\.venv\Scripts\python.exe'
& $python -m pytest tests/test_llm_model_catalog.py tests/test_web_benchmark_runner.py tests/test_provider_ops.py tests/test_conversation_models.py tests/test_conversation_web.py tests/test_web.py -q
```

Expected: all focused tests pass.

- [ ] **Step 3: Run every JavaScript contract**

Run:

```powershell
Get-ChildItem tests/js -File | Where-Object {
  $_.Extension -in '.cjs', '.mjs'
} | ForEach-Object {
  node $_.FullName
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
```

Expected: all contracts pass, including `market_map_contract.mjs`.

- [ ] **Step 4: Run the complete Python and lint gates**

Run:

```powershell
& $python -m pytest -q
& $python -m ruff check src/qingpu_insight tests
git diff --check
```

Expected: pytest reports zero failures; Ruff and `git diff --check` exit 0.

- [ ] **Step 5: Run a secret and unsafe-output scan**

Run:

```powershell
rg -n --hidden -g '!*.pyc' -g '!.git/**' -g '!instance/**' `
  'AQ\.[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{20,}|API_KEY\s*=\s*["''][^"'']+' `
  src tests docs README.md
```

Expected: no real credential value appears. Existing deliberate fake-key
redaction fixtures may match; inspect every match and confirm it is a fixed
test sentinel rather than a usable secret before proceeding.

- [ ] **Step 6: Perform a real Ollama catalog and Benchmark acceptance**

Start the Web app from this worktree using the project’s documented start command. In the local admin page:

1. Open the LLM section.
2. Confirm the model dropdown includes each model currently shown by `ollama list`; on this machine the previously observed examples were `gemma4:26b`, `qwen2.5:0.5b`, and `gemma4:e2b`.
3. Confirm it also lists `Gemini｜Gemini 3.5 Flash-Lite` and `Gemini｜Gemma 4 31B`.
4. Select `Ollama｜gemma4:e2b`, start Benchmark, and wait for the job to reach `succeeded` or show a safely redacted actionable failure.
5. Confirm the job result records provider `ollama` and model `gemma4:e2b`.

Do not download a missing model automatically.

- [ ] **Step 7: Perform homepage and map browser acceptance**

In the local homepage:

1. Confirm the assistant has one model dropdown with exactly four options and no provider/free-text model controls.
2. Confirm the map displays the complete grouped count when the new backend route is active.
3. In browser developer tools, temporarily block or override only `/api/market/map-points` to return 404.
4. Confirm the browser requests `/api/transactions` with the active filters and `limit=100`.
5. Confirm the fixed compatibility message is visible and the map shows only valid recent markers.
6. Change the simulated response to 500 and confirm no fallback request occurs and old markers are cleared.

- [ ] **Step 8: Record the Gemini acceptance state safely**

If a newly rotated Gemini key has been saved through the admin UI, run one Benchmark for each fixed Gemini model and confirm the job records the exact selected model. If no new key is configured, verify both Gemini options remain visible but disabled with `尚未設定 Gemini API Key`, and record Gemini live execution as `not run: no configured rotated key`; do not reuse any key from chat history.

- [ ] **Step 9: Commit documentation and any acceptance-only corrections**

```powershell
git add README.md
git commit -m "docs: explain model catalog and map compatibility"
```

- [ ] **Step 10: Verify final branch state**

Run:

```powershell
git status --short
git log --oneline --decorate -10
```

Expected: no uncommitted implementation files; the plan/spec commits and Tasks 1–7 commits are present on the feature branch.

---

## Completion Criteria

- Admin Benchmark offers one live model selector and submits only a catalog-owned `model_id`.
- Ollama models reflect `/api/tags` without a Web restart; Gemini offers only the two approved models.
- The selected provider and exact model reach a real production-wired Benchmark runner.
- Homepage retains exactly four fixed model choices and reports dynamic Gemini/Ollama readiness.
- A grouped-map 404 renders a labelled recent-100 compatibility view; non-404 and malformed responses never masquerade as success.
- Old markers are removed before an error is displayed.
- Python tests, all Node contracts, Ruff, diff checks, secret scan, and real local acceptance are complete.
