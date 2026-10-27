"""Structured reasoning output schema + deterministic validation.

The provider's output is DATA. It is validated against:
  * the allowed-cause registry (knowledge.py failure modes for the asset),
  * the evidence snapshot's stable refs (citations must resolve),
  * the independent diagnostic-evidence allow-list,
  * bounds on counts, confidence, and text lengths.

Providers can never emit trusted database ids, approval decisions, execution
commands, SQL, incident state, or memory-validation decisions — the schema
has no fields for them and unknown fields are forbidden.
Invalid output raises ProviderOutputError → deterministic fallback.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

SCHEMA_VERSION = "reasoning_v2"

MAX_HYPOTHESES = 5
MAX_LIST_ITEMS = 6
MAX_TEXT = 300
MAX_RATIONALE = 600


class ProviderOutputError(Exception):
    """Schema/citation/policy violation in provider output → fail closed."""


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cause_id: str = Field(max_length=120)  # must exist in the allowed-cause registry
    label: str = Field(max_length=MAX_TEXT)
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_refs: list[str] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    contradicting_refs: list[str] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)


class DiagnosisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypotheses: list[Hypothesis] = Field(default_factory=list, max_length=MAX_HYPOTHESES)
    missing_evidence: list[str] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    disposition: Literal["rank_hypotheses", "request_evidence", "abstain"] = Field(
        description=(
            "Diagnostic recommendation only. The deterministic orchestrator owns "
            "planning, escalation, approval, and execution."
        )
    )
    abstain_reason: str = Field(default="", max_length=MAX_TEXT)
    requested_test: str | None = Field(
        default=None,
        description=(
            "At most one independent evidence type from the supplied allow-list. "
            "Required only for disposition=request_evidence; otherwise null."
        ),
    )
    public_rationale: str = Field(default="", max_length=MAX_RATIONALE)


def validate_result(
    raw: dict, *, allowed_cause_ids: set[str], evidence_refs: set[str], allowed_tests: set[str]
) -> DiagnosisResult:
    """Parse + policy-validate a provider response. Raises ProviderOutputError."""
    if not isinstance(raw, dict):
        raise ProviderOutputError(f"provider output is {type(raw).__name__}, not an object")
    try:
        result = DiagnosisResult.model_validate(raw)
    except ValidationError as e:
        raise ProviderOutputError(f"schema validation failed: {e.errors()[:3]}") from e

    for h in result.hypotheses:
        if h.cause_id not in allowed_cause_ids:
            raise ProviderOutputError(
                f"hypothesis cause '{h.cause_id}' is not in the allowed registry "
                f"{sorted(allowed_cause_ids)}"
            )
        for ref in h.supporting_refs + h.contradicting_refs:
            if ref not in evidence_refs:
                raise ProviderOutputError(
                    f"citation '{ref}' does not resolve to any snapshot evidence "
                    f"(hallucinated citation)"
                )
    if result.requested_test is not None and result.requested_test not in allowed_tests:
        raise ProviderOutputError(
            f"requested evidence '{result.requested_test}' is not on the "
            f"allow-list {sorted(allowed_tests)}"
        )
    if result.disposition in ("rank_hypotheses", "request_evidence") and not result.hypotheses:
        raise ProviderOutputError(
            f"{result.disposition} requires at least one supported hypothesis"
        )
    if result.disposition == "request_evidence" and result.requested_test is None:
        raise ProviderOutputError("request_evidence requires one allowed requested_test")
    if result.disposition != "request_evidence" and result.requested_test is not None:
        raise ProviderOutputError(
            "requested_test is allowed only with disposition=request_evidence"
        )
    if result.disposition == "abstain" and not result.abstain_reason:
        raise ProviderOutputError("abstain requires abstain_reason")
    if result.hypotheses:
        # ranked = descending confidence; enforce rather than trust
        confs = [h.confidence for h in result.hypotheses]
        if confs != sorted(confs, reverse=True):
            raise ProviderOutputError("hypotheses are not ranked by confidence")
    return result
