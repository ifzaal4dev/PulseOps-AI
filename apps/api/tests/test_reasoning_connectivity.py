"""The manual 'Test configured LLM' check must be honest, bounded, and
side-effect free — no network and no secrets required by any of these tests.
"""

from types import SimpleNamespace

from sqlalchemy import select

from pulseops_api.services.reasoning import connectivity
from pulseops_api.services.reasoning.connectivity import (
    STATUS_ACCEPTED,
    STATUS_FALLBACK,
    run_connectivity_test,
)
from pulseops_api.services.reasoning.providers import (
    FakeProvider,
    ProviderUnavailableError,
    ReasoningConfigError,
)


def hybrid_settings(**overrides):
    values = dict(
        reasoning_mode="hybrid",
        llm_provider="fake",
        llm_model="fake-model-1",
        llm_timeout_seconds=5.0,
        llm_max_output_tokens=800,
        llm_temperature=0.1,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class ScriptedProvider:
    """Raises the scripted errors in order, then answers like FakeProvider."""

    name = "scripted"
    model = "scripted-model"

    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0

    def diagnose(self, snapshot, config):
        self.calls += 1
        if self.errors:
            raise ProviderUnavailableError(self.errors.pop(0))
        return FakeProvider().diagnose(snapshot, config)


def run_with(provider):
    return run_connectivity_test(hybrid_settings(), provider_factory=lambda s: provider)


# --------------------------------------------------------------- happy path


def test_fake_provider_success_is_unmistakably_labeled():
    report = run_with(FakeProvider())
    assert report["status"] == STATUS_ACCEPTED
    assert report["accepted"] is True
    assert report["attempts"] == 1
    assert report["actual_provider"] == "fake"
    assert report["actual_model"] == "fake-model-1"
    assert report["latency_ms"] is not None
    assert report["input_tokens"] == 100 and report["output_tokens"] == 50
    assert report["prompt_version"] == "prompt_v3"
    assert report["schema_version"]
    assert report["fallback_reason"] is None
    # The validated structured result is complete.
    result = report["result"]
    assert result["hypotheses"][0]["cause_id"] == "compressed-air leak"
    assert 0 <= result["hypotheses"][0]["confidence"] <= 1
    assert result["disposition"] == "rank_hypotheses"
    assert "public_rationale" in result and "missing_evidence" in result
    # Loudly synthetic; manifest carries provenance but never raw text.
    assert "[SYNTHETIC/DEMO]" in report["snapshot_label"]
    assert all("text" not in item for item in report["evidence_manifest"]["evidence"])


# ------------------------------------------------------------ failure paths


def test_malformed_response_falls_back_without_retry():
    report = run_with(FakeProvider(behavior="malformed"))
    assert report["status"] == STATUS_FALLBACK
    assert report["accepted"] is False
    assert report["attempts"] == 1
    assert report["fallback_reason"].startswith("invalid output:")
    # Fallback output is never presented as an LLM response.
    assert report["actual_provider"] is None and report["actual_model"] is None
    assert report["latency_ms"] is None and report["input_tokens"] is None
    assert report["attempted_provider"] == "fake"
    # But the authoritative deterministic diagnosis is still shown.
    assert report["result"]["hypotheses"]


def test_timeout_falls_back_without_retry():
    provider = ScriptedProvider(errors=["timeout"] * 5)
    report = run_with(provider)
    assert report["status"] == STATUS_FALLBACK
    assert provider.calls == 1  # not transient → no second attempt
    assert "timeout" in report["fallback_reason"]


def test_auth_failures_fall_back_without_retry_and_without_secrets():
    for detail in ("401 unauthorized (check OPENROUTER_API_KEY)", "HTTP 403 PERMISSION_DENIED"):
        provider = ScriptedProvider(errors=[detail] * 5)
        report = run_with(provider)
        assert report["status"] == STATUS_FALLBACK
        assert provider.calls == 1
        assert detail.split()[0] in report["fallback_reason"]
        as_text = str(report)
        assert "Bearer" not in as_text and "api_key=" not in as_text


def test_transient_429_and_503_retry_exactly_once():
    for detail in ("429 rate limited", "503 upstream error"):
        provider = ScriptedProvider(errors=[detail] * 5)
        report = run_with(provider)
        assert report["status"] == STATUS_FALLBACK
        assert provider.calls == 2  # exactly two HTTP attempts, never more
        assert report["attempts"] == 2


def test_transient_failure_then_success_is_accepted_on_second_attempt():
    provider = ScriptedProvider(errors=["503 upstream error"])
    report = run_with(provider)
    assert report["status"] == STATUS_ACCEPTED
    assert provider.calls == 2
    assert report["attempts"] == 2
    assert report["actual_provider"] == "scripted"


# ----------------------------------------------------- configuration states


def test_deterministic_mode_reports_no_attempt():
    settings = SimpleNamespace(
        reasoning_mode="deterministic",
        llm_provider="gemini",
        llm_model="",
        llm_timeout_seconds=5.0,
        llm_max_output_tokens=800,
        llm_temperature=0.1,
    )
    report = run_connectivity_test(settings)
    assert report["status"] == STATUS_FALLBACK
    assert report["attempts"] == 0
    assert report["configured_mode"] == "deterministic"
    assert report["configured_provider"] is None and report["configured_model"] is None
    assert "no request was attempted" in report["fallback_reason"]
    assert report["result"]["hypotheses"]  # deterministic diagnosis still shown


def test_configuration_error_becomes_explicit_fallback():
    def broken_factory(settings):
        raise ReasoningConfigError("llm_provider 'nope' unknown")

    report = run_connectivity_test(hybrid_settings(), provider_factory=broken_factory)
    assert report["status"] == STATUS_FALLBACK
    assert report["attempts"] == 0
    assert report["fallback_reason"].startswith("configuration error:")


# ----------------------------------------------------------- HTTP + no side effects


def test_endpoint_creates_no_incident_action_or_run(seeded, monkeypatch):
    session, ids = seeded
    from fastapi.testclient import TestClient

    from pulseops_api import config
    from pulseops_api.db import get_session
    from pulseops_api.main import app
    from pulseops_api.models import AgentAction, AgentRun, Incident, WorkOrder

    # Independent of the developer's local .env: force hybrid mode and a fake
    # provider so no network or secret is ever touched over HTTP either.
    monkeypatch.setattr(config.settings, "reasoning_mode", "hybrid")
    monkeypatch.setattr(config.settings, "llm_provider", "gemini")
    monkeypatch.setattr(config.settings, "llm_model", "irrelevant-fake")
    monkeypatch.setattr(connectivity, "create_provider", lambda s: FakeProvider())

    def _override():
        yield session

    app.dependency_overrides[get_session] = _override
    try:
        with TestClient(app, headers={"Content-Type": "application/json"}) as client:
            before = {
                model: len(session.scalars(select(model)).all())
                for model in (Incident, AgentRun, AgentAction, WorkOrder)
            }
            first = client.post("/api/agent/reasoning-test")
            assert first.status_code == 200
            body = first.json()
            assert body["status"] == STATUS_ACCEPTED
            assert body["note"].startswith("Synthetic/demo")
            after = {
                model: len(session.scalars(select(model)).all())
                for model in (Incident, AgentRun, AgentAction, WorkOrder)
            }
            assert before == after
    finally:
        app.dependency_overrides.clear()


def test_endpoint_deterministic_default_uses_exact_fallback_line(seeded, monkeypatch):
    session, ids = seeded
    from fastapi.testclient import TestClient

    from pulseops_api import config
    from pulseops_api.db import get_session
    from pulseops_api.main import app

    monkeypatch.setattr(config.settings, "reasoning_mode", "deterministic")

    def _override():
        yield session

    app.dependency_overrides[get_session] = _override
    try:
        with TestClient(app, headers={"Content-Type": "application/json"}) as client:
            body = client.post("/api/agent/reasoning-test").json()
            assert body["status"] == STATUS_FALLBACK
            assert body["status"] == "LLM NOT USED — DETERMINISTIC FALLBACK"
            assert body["attempts"] == 0
    finally:
        app.dependency_overrides.clear()
