"""Opt-in, cost-bounded LIVE provider smoke checks. NEVER run in CI.

    # verify which models your key can use (and, for OpenRouter, which
    # support structured outputs) before putting one in PULSEOPS_LLM_MODEL:
    python -m pulseops_api.reasoning_smoke --provider gemini --list-models
    python -m pulseops_api.reasoning_smoke --provider openrouter --list-models

    # one bounded live diagnosis call against a tiny synthetic snapshot:
    python -m pulseops_api.reasoning_smoke --provider gemini --model <id> --diagnose
    python -m pulseops_api.reasoning_smoke --provider openrouter --model <slug> --diagnose

Keys come from GEMINI_API_KEY/GOOGLE_API_KEY, OPENROUTER_API_KEY, or
ANTHROPIC_API_KEY. Nothing is logged beyond the structured result and safe
metadata. A successful --diagnose run is the ONLY evidence that live calls
work — do not claim live verification without it.
"""

from __future__ import annotations

import argparse
import json
import sys

from .services.reasoning.hybrid import run_reasoning
from .services.reasoning.providers import PROVIDER_REGISTRY, read_secret
from .services.reasoning.snapshot import EvidenceItem, EvidenceSnapshot


def tiny_snapshot() -> EvidenceSnapshot:
    return EvidenceSnapshot(
        asset_label="APU Compressor (air_compressor) — SMOKE TEST",
        items=(
            EvidenceItem(
                ref="sensor_stats",
                source_type="sensor_stats",
                trust="trusted_system",
                facts={"signals": {"air_pressure_drop": 0.6, "compressor_duty_high": 0.5}},
            ),
            EvidenceItem(
                ref="inspections:1",
                source_type="inspection_note",
                trust="untrusted_evidence",
                facts={"summary": "walkdown"},
                text="audible hissing near the dryer towers",
            ),
        ),
        baseline={
            "hypotheses": [
                {"cause_id": "compressed-air leak", "confidence": 0.8, "refs": ["sensor_stats"]},
                {
                    "cause_id": "oil-system abnormality (synthetic scenario)",
                    "confidence": 0.2,
                    "refs": [],
                },
            ],
            "abstain": False,
            "abstain_reason": "",
            "rationale": "smoke baseline",
        },
        allowed_cause_ids=("compressed-air leak", "oil-system abnormality (synthetic scenario)"),
        allowed_tests=("ultrasonic_leak_survey", "oil_sample_analysis"),
    )


def list_models(provider: str) -> int:
    if provider == "gemini":
        key = read_secret("GEMINI_API_KEY", "GOOGLE_API_KEY")
        if not key:
            sys.exit("GEMINI_API_KEY / GOOGLE_API_KEY (or *_FILE) not set")
        from google import genai

        client = genai.Client(api_key=key)
        for m in client.models.list():
            actions = getattr(m, "supported_actions", None)
            if not actions or "generateContent" in actions:
                print(m.name)
        return 0
    if provider == "openrouter":
        key = read_secret("OPENROUTER_API_KEY")
        if not key:
            sys.exit("OPENROUTER_API_KEY (or *_FILE) not set")
        import httpx

        data = httpx.get(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=30,
        ).json()
        for m in data.get("data", []):
            params = m.get("supported_parameters") or []
            if "structured_outputs" in params or "response_format" in params:
                print(m["id"])
        print("\n(only models advertising structured-output support are listed)")
        return 0
    sys.exit(f"--list-models not implemented for '{provider}'")


def diagnose(provider_name: str, model: str) -> int:
    class SmokeSettings:
        reasoning_mode = "llm"
        llm_provider = provider_name
        llm_model = model
        llm_timeout_seconds = 30.0
        llm_max_output_tokens = 800  # cost bound
        llm_temperature = 0.1

    from .services.reasoning.providers import create_provider

    outcome = run_reasoning(tiny_snapshot(), SmokeSettings, provider=create_provider(SmokeSettings))
    print(json.dumps({"meta": outcome.meta, "result": outcome.result.model_dump()}, indent=2))
    if outcome.meta["fallback"]:
        print(
            "\nLIVE CALL DID NOT SUCCEED — fell back deterministically "
            f"({outcome.meta['fallback_reason']})."
        )
        return 1
    print("\nLIVE CALL SUCCEEDED — this provider/model pair is verified.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True, choices=sorted(PROVIDER_REGISTRY))
    parser.add_argument("--list-models", action="store_true")
    parser.add_argument("--model", default="")
    parser.add_argument("--diagnose", action="store_true")
    args = parser.parse_args()
    if args.list_models:
        return list_models(args.provider)
    if args.diagnose:
        if not args.model:
            sys.exit("--diagnose requires --model")
        return diagnose(args.provider, args.model)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
