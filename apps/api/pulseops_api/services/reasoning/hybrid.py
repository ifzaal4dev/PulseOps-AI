"""Hybrid reasoning orchestration: provider call + validation + fallback.

Deterministic policy owns everything; a provider may only rerank allowed
causes, cite snapshot refs, abstain, or request one allow-listed independent
evidence type. ANY failure — unavailable provider, timeout, rate limit, malformed
output, unknown cause, hallucinated citation, forbidden test, conflicting
flags, oversize text, or any exception — fails closed to the deterministic
result with the reason persisted. Only the validated structured result +
concise public rationale + safe metadata are stored, never hidden
chain-of-thought and never key material.

Callers must not hold DB write locks across run_reasoning (agent_loop
commits its evidence transaction before calling).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .providers import DeterministicProvider, ProviderUnavailableError, create_provider
from .schema import SCHEMA_VERSION, DiagnosisResult, ProviderOutputError, validate_result
from .snapshot import EvidenceSnapshot


@dataclass
class ReasoningOutcome:
    result: DiagnosisResult
    meta: dict


def _validate(raw: dict, snapshot: EvidenceSnapshot) -> DiagnosisResult:
    return validate_result(
        raw,
        allowed_cause_ids=set(snapshot.allowed_cause_ids),
        evidence_refs=snapshot.refs,
        allowed_tests=set(snapshot.allowed_tests),
    )


def _deterministic_outcome(
    snapshot: EvidenceSnapshot,
    mode: str,
    fallback_reason: str | None = None,
    attempted: dict | None = None,
) -> ReasoningOutcome:
    raw = DeterministicProvider().diagnose(snapshot, {})
    provider_meta = raw.pop("_meta")
    result = _validate(raw, snapshot)
    return ReasoningOutcome(
        result,
        {
            **provider_meta,
            "mode": mode,
            "prompt_version": snapshot.prompt_version,
            "schema_version": SCHEMA_VERSION,
            "fallback": fallback_reason is not None,
            "fallback_reason": fallback_reason,
            "attempted_provider": (attempted or {}).get("provider"),
            "attempted_model": (attempted or {}).get("model"),
        },
    )


def run_reasoning(snapshot: EvidenceSnapshot, settings, provider=None) -> ReasoningOutcome:
    """`provider` override is for tests/eval (FakeProvider or fake transports).
    Configuration errors (unknown provider/mode, missing model) raise
    ReasoningConfigError — they are operator mistakes, not runtime fallbacks."""
    mode = getattr(settings, "reasoning_mode", "deterministic")
    if provider is None:
        provider = create_provider(settings)  # validates mode/provider/model
    if provider.name == "deterministic":
        return _deterministic_outcome(snapshot, mode)

    attempted = {"provider": provider.name, "model": getattr(provider, "model", None)}
    t0 = time.monotonic()
    try:
        raw = provider.diagnose(snapshot, {"mode": mode})
    except ProviderUnavailableError as e:
        return _deterministic_outcome(snapshot, mode, f"provider unavailable: {e}", attempted)
    except Exception as e:  # malformed JSON, transport bugs — fail closed
        return _deterministic_outcome(
            snapshot, mode, f"provider error: {type(e).__name__}: {e}", attempted
        )

    provider_meta = raw.pop("_meta", {}) if isinstance(raw, dict) else {}
    try:
        result = _validate(raw, snapshot)
    except ProviderOutputError as e:
        return _deterministic_outcome(snapshot, mode, f"invalid output: {e}", attempted)

    return ReasoningOutcome(
        result,
        {
            "provider": provider.name,
            "requested_model": provider_meta.get(
                "requested_model", getattr(provider, "model", None)
            ),
            "actual_model": provider_meta.get("actual_model"),
            "request_id": provider_meta.get("request_id"),
            "latency_ms": provider_meta.get("latency_ms", int((time.monotonic() - t0) * 1000)),
            "input_tokens": provider_meta.get("input_tokens"),
            "output_tokens": provider_meta.get("output_tokens"),
            "finish_reason": provider_meta.get("finish_reason"),
            "mode": mode,
            "prompt_version": snapshot.prompt_version,
            "schema_version": SCHEMA_VERSION,
            "fallback": False,
            "fallback_reason": None,
            "attempted_provider": None,
            "attempted_model": None,
        },
    )
