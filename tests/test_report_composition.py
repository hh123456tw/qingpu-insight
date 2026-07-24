from __future__ import annotations

from types import SimpleNamespace

from qingpu_insight.report_composition import create_report_runtime


def fake_connection_factory():
    return None  # never called during composition test


SAFE_TEST_DATABASE_URL = "mysql+pymysql://test:test@localhost:3306/test_db"


def test_runtime_contains_configured_ollama_and_rule(monkeypatch, tmp_path):
    env = {
        "QINGPU_OLLAMA_MODEL": "gemma3:4b",
        "QINGPU_OLLAMA_BASE_URL": "http://127.0.0.1:11434",
    }
    runtime = create_report_runtime(fake_connection_factory, tmp_path, env)
    assert runtime.service is not None
    assert runtime.repository is not None
    assert set(runtime.providers) == {"rule", "ollama"}


def fake_report_runtime():
    from qingpu_insight.report_contracts import SavedBuyerReport

    class _FakeService:
        def generate(self, request):
            return SavedBuyerReport(
                report_id="test-id",
                request_hash="hash",
                dataset_version="v1",
                evidence_pack_id="pack-1",
                provider=request.provider,
                model="rule",
                content={
                    "summary": {"text": "test", "fact_ids": ["f1"], "numeric_fact_ids": []},
                    "advantages": [],
                    "risks": [],
                    "negotiation": [],
                    "limitations": [],
                },
                fallback_reason=None,
                validation_codes=(),
                latency_ms=0.0,
                created_at="2026-07-23T00:00:00Z",
            )

    class _FakeRepository:
        def get(self, report_id):
            return None

        def create(self, report):
            return report

    return SimpleNamespace(service=_FakeService(), repository=_FakeRepository())


def test_root_web_app_composes_report_services(monkeypatch, tmp_path):
    import qingpu_insight.report_composition as rc
    from qingpu_insight.web import create_app

    monkeypatch.setenv("QINGPU_DATABASE_URL", SAFE_TEST_DATABASE_URL)
    monkeypatch.setattr(rc, "create_report_runtime", lambda *args, **kwargs: fake_report_runtime())
    app = create_app(root=tmp_path)
    assert app.extensions["qingpu_report_services"] is not None


class _RecordingService:
    requested_provider = None

    def generate(self, request):
        from qingpu_insight.report_contracts import SavedBuyerReport

        self.requested_provider = request.provider
        return SavedBuyerReport(
            report_id="test-id",
            request_hash="hash",
            dataset_version="v1",
            evidence_pack_id="pack-1",
            provider=request.provider,
            model="rule",
            content={
                "summary": {"text": "test", "fact_ids": ["f1"], "numeric_fact_ids": []},
                "advantages": [],
                "risks": [],
                "negotiation": [],
                "limitations": [],
            },
            fallback_reason=None,
            validation_codes=(),
            latency_ms=0.0,
            created_at="2026-07-23T00:00:00Z",
        )


class _RecordingRuntime:
    def __init__(self):
        self.service = _RecordingService()
        self.repository = object()

    @property
    def requested_provider(self):
        return self.service.requested_provider


def recording_report_runtime():
    return _RecordingRuntime()


def ollama_args():
    return SimpleNamespace(
        candidate=["id-1"], provider="ollama", budget=None, intended_use="self_use",
    )


def test_cli_report_uses_configured_provider_registry(monkeypatch, tmp_path):
    import qingpu_insight.cli as cli

    monkeypatch.setenv("QINGPU_DATABASE_URL", SAFE_TEST_DATABASE_URL)
    runtime = recording_report_runtime()
    monkeypatch.setattr(cli, "create_report_runtime", lambda *args, **kwargs: runtime)
    assert cli.report_generate(tmp_path, ollama_args()) == 0
    assert runtime.requested_provider == "ollama"
