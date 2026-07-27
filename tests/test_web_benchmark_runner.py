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
                    success=True,
                    schema_success=True,
                    fact_accuracy=1.0,
                    required_section_coverage=0.8,
                ),
                SimpleNamespace(
                    success=True,
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
    assert result["provider_success_count"] == 2
    assert result["provider_failure_count"] == 0
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
        benchmark=lambda *_args, **_kwargs: (
            [type("Result", (), {
                "success": True,
                "schema_success": True,
                "fact_accuracy": 1.0,
                "required_section_coverage": 1.0,
            })()],
            [{"p50_latency": 1.0, "p95_latency": 1.0}],
        ),
    )

    runner.run("gemini", "gemini-3.5-flash-lite", [], tmp_path)
    runner.run("gemini", "gemma-4-31b-it", [], tmp_path)

    assert constructed == [
        ("first-key", "gemini-3.5-flash-lite"),
        ("rotated-key", "gemma-4-31b-it"),
    ]
    with pytest.raises(ValueError, match="unsupported_benchmark_provider"):
        runner.run("rule", "rule", [], tmp_path)


def test_runner_fails_when_every_provider_call_failed(tmp_path):
    from types import SimpleNamespace

    runner = ConfiguredWebBenchmarkRunner(
        ollama_base_url_getter=lambda: "http://127.0.0.1:11434",
        gemini_api_key_getter=lambda: "",
        ollama_factory=lambda _base, _model: object(),
        gemini_factory=lambda _key, _model: object(),
        benchmark=lambda *_args, **_kwargs: (
            [
                SimpleNamespace(
                    success=False,
                    schema_success=False,
                    fact_accuracy=0.0,
                    required_section_coverage=0.0,
                ),
            ],
            [{"p50_latency": 0.0, "p95_latency": 0.0}],
        ),
    )

    with pytest.raises(RuntimeError, match="benchmark_all_provider_calls_failed"):
        runner.run("ollama", "gemma4:e2b", [], tmp_path)


def test_runner_fails_when_benchmark_has_no_cases(tmp_path):
    runner = ConfiguredWebBenchmarkRunner(
        ollama_base_url_getter=lambda: "http://127.0.0.1:11434",
        gemini_api_key_getter=lambda: "",
        ollama_factory=lambda _base, _model: object(),
        gemini_factory=lambda _key, _model: object(),
        benchmark=lambda *_args, **_kwargs: ([], []),
    )

    with pytest.raises(RuntimeError, match="benchmark_has_no_cases"):
        runner.run("ollama", "gemma4:e2b", [], tmp_path)
