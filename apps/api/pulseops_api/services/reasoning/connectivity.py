"""Manually triggered LLM connectivity check for the Agent Control Room.

This is NOT a toy ping. One click exercises the exact production reasoning
path — provider registry, evidence snapshot rendering, prompt/schema
versions, structured-output validation, and the fail-closed deterministic
fallback — against a loudly labeled SYNTHETIC/DEMO snapshot.

Guarantees:
  * no database writes: no incident, work order, approval, action, or run;
  * one logical request per invocation, with at most TWO HTTP attempts total,
    the second only after a transient 429/503 provider failure;
  * secrets and environment values are never returned — failure details pass
    through the provider layer's credential redaction;
  * the caller receives one of two exact, unambiguous status lines
    (STATUS_ACCEPTED / STATUS_FALLBACK) so fallback output can never be
    presented as LLM reasoning.
"""

from __future__ import annotations

import re

from .hybrid import run_reasoning
from .providers import ReasoningConfigError, create_provider
from .schema import SCHEMA_VERSION
from .snapshot import EvidenceItem, EvidenceSnapshot

STATUS_ACCEPTED = "LIVE LLM RESPONSE ACCEPTED"
STATUS_FALLBACK = "LLM NOT USED — DETERMINISTIC FALLBACK"

# Retry only genuinely transient provider failures (rate limit / upstream
# unavailable). Auth, config, validation, and malformed-output failures are
# deterministic — retrying them would just burn quota.
_TRANSIENT = re.compile(r"\b(429|503)\b")

SNAPSHOT_LABEL = "[SYNTHETIC/DEMO] APU compressor — LLM connectivity check"


def demo_snapshot(prompt_version: str = "prompt_v3") -> EvidenceSnapshot:
    """A tiny, clearly synthetic diagnostic snapshot.

    Same shape and rendering as a real investigation snapshot (trusted facts,
    untrusted JSON-escaped text, baseline, allow-lists) so an accepted response
    proves the full structured-output contract, not just reachability.
    """
    return EvidenceSnapshot(
        asset_label=SNAPSHOT_LABEL,
        items=(
            EvidenceItem(
                ref="sensor_stats",
                source_type="sensor_stats",
                trust="trusted_system",
                facts={
                    "signals": {"air_pressure_drop": 0.6, "compressor_duty_high": 0.5},
                    "provenance": "synthetic connectivity-check data",
                },
            ),
            EvidenceItem(
                ref="inspections:demo",
                source_type="inspection_note",
                trust="untrusted_evidence",
                facts={"summary": "synthetic walkdown note"},
                text="audible hissing near the dryer towers",
            ),
        ),
        baseline={
            "hypotheses": [
                {
                    "cause_id": "compressed-air leak",
                    "label": "compressed-air leak",
                    "confidence": 0.8,
                    "refs": ["sensor_stats"],
                },
                {
                    "cause_id": "oil-system abnormality (synthetic scenario)",
                    "label": "oil-system abnormality (synthetic scenario)",
                    "confidence": 0.2,
                    "refs": [],
                },
            ],
            "abstain": False,
            "abstain_reason": "",
            "rationale": "deterministic baseline for the synthetic connectivity check",
        },
        allowed_cause_ids=("compressed-air leak", "oil-system abnormality (synthetic scenario)"),
        allowed_tests=("ultrasonic_leak_survey", "oil_sample_analysis"),
        prompt_version=prompt_version,
    )


def _is_transient(meta: dict) -> bool:
    reason = meta.get("fallback_reason") or ""
    return (
        bool(meta.get("fallback"))
        and reason.startswith("provider unavailable:")
        and bool(_TRANSIENT.search(reason))
    )


def run_connectivity_test(settings, provider_factory=None) -> dict:
    """Execute the check and return a JSON-safe report. Never raises for
    provider/config problems — they become the explicit fallback status."""
    if provider_factory is None:  # late-bound so tests can patch create_provider
        provider_factory = create_provider
    snapshot = demo_snapshot(getattr(settings, "reasoning_prompt_version", "prompt_v3"))
    configured_mode = getattr(settings, "reasoning_mode", "deterministic")
    llm_configured = configured_mode in ("llm", "hybrid")
    report = {
        "configured_mode": configured_mode,
        "configured_provider": settings.llm_provider if llm_configured else None,
        "configured_model": (settings.llm_model or None) if llm_configured else None,
        "prompt_version": snapshot.prompt_version,
        "schema_version": SCHEMA_VERSION,
        "snapshot_label": snapshot.asset_label,
        "evidence_manifest": snapshot.public_manifest(),
        "note": (
            "Synthetic/demo diagnostic snapshot only. This check creates no "
            "incident, work order, approval, action, or agent run."
        ),
    }

    config_error: str | None = None
    provider = None
    if llm_configured:
        try:
            provider = provider_factory(settings)
        except ReasoningConfigError as e:
            config_error = f"configuration error: {e}"

    if provider is None:
        # Deterministic mode or invalid configuration: show exactly what the
        # authoritative fallback produces, and why no request was attempted.
        from .providers import DeterministicProvider

        outcome = run_reasoning(snapshot, settings, provider=DeterministicProvider())
        report.update(
            {
                "status": STATUS_FALLBACK,
                "accepted": False,
                "attempts": 0,
                "attempted_provider": report["configured_provider"],
                "attempted_model": report["configured_model"],
                "actual_provider": None,
                "actual_model": None,
                "request_id": None,
                "latency_ms": None,
                "input_tokens": None,
                "output_tokens": None,
                "finish_reason": None,
                "fallback_reason": config_error
                or (
                    "reasoning_mode is 'deterministic' — no external provider is "
                    "configured, so no request was attempted"
                ),
                "result": outcome.result.model_dump(),
            }
        )
        return report

    attempts = 0
    outcome = None
    for _ in range(2):
        attempts += 1
        outcome = run_reasoning(snapshot, settings, provider=provider)
        if not _is_transient(outcome.meta):
            break

    meta = outcome.meta
    accepted = not meta.get("fallback") and meta.get("provider") not in (None, "deterministic")
    report.update(
        {
            "status": STATUS_ACCEPTED if accepted else STATUS_FALLBACK,
            "accepted": accepted,
            "attempts": attempts,
            "attempted_provider": (
                meta.get("provider") if accepted else meta.get("attempted_provider")
            ),
            "attempted_model": (
                meta.get("requested_model") if accepted else meta.get("attempted_model")
            ),
            "actual_provider": meta.get("provider") if accepted else None,
            "actual_model": meta.get("actual_model") if accepted else None,
            "request_id": meta.get("request_id") if accepted else None,
            "latency_ms": meta.get("latency_ms") if accepted else None,
            "input_tokens": meta.get("input_tokens") if accepted else None,
            "output_tokens": meta.get("output_tokens") if accepted else None,
            "finish_reason": meta.get("finish_reason") if accepted else None,
            "fallback_reason": meta.get("fallback_reason"),
            "result": outcome.result.model_dump(),
        }
    )
    return report
