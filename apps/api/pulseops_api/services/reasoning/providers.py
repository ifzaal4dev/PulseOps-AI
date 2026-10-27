"""Reasoning providers: deterministic, Gemini, OpenRouter, Anthropic, fakes.

COMMON CONTRACT — every provider:
  receives: the immutable EvidenceSnapshot (allowed causes/tests/baseline
  embedded) + bounded config; returns: a RAW dict candidate result plus a
  `_meta` dict {provider, requested_model, actual_model, request_id,
  latency_ms, input_tokens, output_tokens, finish_reason}.

The shared validator (schema.validate_result, called by hybrid.py) — not the
provider — decides whether output is trusted enough to use. Providers have NO
access to the database session, tools, approvals, incidents, work orders, or
memory validation; their only input is the snapshot.

Secrets come from environment variables only and are never logged or echoed
in errors. Live calls are UNVERIFIED unless a real key was used in an
explicit smoke run (`python -m pulseops_api.reasoning_smoke`); CI uses
fakes only.
"""

from __future__ import annotations

import json
import os
import re
import time

from .schema import DiagnosisResult
from .snapshot import SYSTEM_INSTRUCTIONS, EvidenceSnapshot

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class ReasoningConfigError(Exception):
    """Invalid reasoning configuration (unknown provider/mode, missing model)."""


class ProviderUnavailableError(Exception):
    """Missing key / network / timeout / rate limit / provider-side refusal."""


def _safe_provider_error(error: Exception) -> str:
    """Preserve useful provider status without ever persisting credentials."""
    status = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    status = status or getattr(response, "status_code", None)
    payload = getattr(error, "response_json", None)
    message = getattr(error, "message", None)
    provider_status = None
    if isinstance(payload, dict):
        detail = payload.get("error") or payload
        if isinstance(detail, dict):
            provider_status = detail.get("status")
            message = detail.get("message") or message
    message = " ".join(str(message or "").split())[:240]
    message = re.sub(
        r"(?i)(api[_-]?key|authorization|bearer|key)\s*[=:]\s*\S+", r"\1=[REDACTED]", message
    )
    parts = [type(error).__name__]
    if status:
        parts.append(f"HTTP {status}")
    if provider_status:
        parts.append(str(provider_status))
    if message:
        parts.append(message)
    return " | ".join(parts)


def _schema_hint() -> str:
    return json.dumps(DiagnosisResult.model_json_schema())


_GEMINI_JSON_SCHEMA_KEYS = {
    "$id",
    "$defs",
    "$ref",
    "$anchor",
    "type",
    "format",
    "title",
    "description",
    "enum",
    "items",
    "prefixItems",
    "minItems",
    "maxItems",
    "minimum",
    "maximum",
    "anyOf",
    "oneOf",
    "properties",
    "additionalProperties",
    "required",
}


def _gemini_json_schema() -> dict:
    """Return the strict diagnosis contract in Gemini's supported subset.

    Pydantic emits useful local-validation keywords such as ``default`` and
    ``maxLength`` that are not in Gemini's documented ``response_json_schema``
    subset.  Strip only unsupported transport keywords; the original
    ``DiagnosisResult`` remains the authoritative validator after generation.
    """

    def clean(schema: dict) -> dict:
        result = {}
        for key, value in schema.items():
            if key not in _GEMINI_JSON_SCHEMA_KEYS:
                continue
            if key in ("properties", "$defs"):
                result[key] = {name: clean(child) for name, child in value.items()}
            elif key in ("anyOf", "oneOf", "prefixItems"):
                result[key] = [clean(child) for child in value]
            elif key in ("items", "additionalProperties") and isinstance(value, dict):
                result[key] = clean(value)
            else:
                result[key] = value
        return result

    return clean(DiagnosisResult.model_json_schema())


def read_secret(*names: str) -> str | None:
    """Resolve a provider key from the environment. For each NAME, checks
    NAME itself, then NAME_FILE (path to a mounted secret file — Docker/K8s
    style). Values are returned to the caller only; never logged."""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value.strip()
        path = os.environ.get(f"{name}_FILE")
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    content = f.read().strip()
                if content:
                    return content
            except OSError:
                continue  # unreadable secret file → treated as absent
    return None


class DeterministicProvider:
    """Wraps the deterministic baseline as an always-valid result."""

    name = "deterministic"
    model = None

    def diagnose(self, snapshot: EvidenceSnapshot, config: dict) -> dict:
        hypotheses = [
            {
                "cause_id": h["cause_id"],
                "label": h.get("label", h["cause_id"]),
                "confidence": h["confidence"],
                "supporting_refs": [r for r in h.get("refs", []) if r in snapshot.refs][:6],
                "contradicting_refs": [],
            }
            for h in snapshot.baseline.get("hypotheses", [])
        ]
        return {
            "hypotheses": hypotheses,
            "disposition": (
                "request_evidence"
                if snapshot.baseline.get("requested_test")
                else "abstain"
                if snapshot.baseline.get("abstain", False)
                else "rank_hypotheses"
            ),
            "abstain_reason": snapshot.baseline.get("abstain_reason", "")[:300],
            "requested_test": snapshot.baseline.get("requested_test"),
            "public_rationale": snapshot.baseline.get(
                "rationale", "deterministic signature scoring"
            )[:600],
            "_meta": {
                "provider": self.name,
                "requested_model": None,
                "actual_model": None,
                "request_id": None,
                "latency_ms": 0,
                "input_tokens": None,
                "output_tokens": None,
                "finish_reason": "deterministic",
            },
        }


class GeminiProvider:
    """Google Gemini via the official `google-genai` SDK.

    Key: GEMINI_API_KEY or GOOGLE_API_KEY. `client_factory` is injectable so
    tests run against a fake client without the SDK or network.
    """

    name = "gemini"

    def __init__(
        self,
        model: str,
        timeout_s: float,
        max_output_tokens: int,
        temperature: float,
        client_factory=None,
    ):
        self.model = model
        self.timeout_s = timeout_s
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        self._client_factory = client_factory

    def _default_client(self):
        api_key = read_secret("GEMINI_API_KEY", "GOOGLE_API_KEY")
        if not api_key:
            raise ProviderUnavailableError("GEMINI_API_KEY / GOOGLE_API_KEY (or *_FILE) not set")
        try:
            from google import genai
        except ImportError as e:
            raise ProviderUnavailableError(f"google-genai SDK not installed: {e}") from e
        return genai.Client(api_key=api_key, http_options={"timeout": int(self.timeout_s * 1000)})

    def diagnose(self, snapshot: EvidenceSnapshot, config: dict) -> dict:
        client = (self._client_factory or self._default_client)()
        t0 = time.monotonic()
        try:
            response = client.models.generate_content(
                model=self.model,
                contents=snapshot.render(),
                config={
                    "system_instruction": SYSTEM_INSTRUCTIONS,
                    # Use the SDK's JSON-Schema channel instead of
                    # `response_schema=DiagnosisResult`.  The latter converts
                    # Pydantic's `additionalProperties` constraints into the
                    # OpenAPI Schema transport and, with some Gemini Developer
                    # API/SDK combinations, serializes them as the unsupported
                    # `additional_properties` field (HTTP 400).  The official
                    # SDK explicitly recommends `response_json_schema` when
                    # `response_schema` cannot process a schema correctly.
                    #
                    # The exact same strict Pydantic model still validates the
                    # returned data locally in hybrid.py; this adapter changes
                    # only the provider transport, never PulseOps policy.
                    "response_mime_type": "application/json",
                    "response_json_schema": _gemini_json_schema(),
                    "temperature": self.temperature,
                    "max_output_tokens": self.max_output_tokens,
                },
            )
        except ProviderUnavailableError:
            raise
        except Exception as e:  # timeout / rate limit / transport / safety refusal
            raise ProviderUnavailableError(_safe_provider_error(e)) from e
        latency_ms = int((time.monotonic() - t0) * 1000)

        parsed = getattr(response, "parsed", None)
        if parsed is not None:  # SDK already validated against the schema
            raw = parsed.model_dump() if hasattr(parsed, "model_dump") else dict(parsed)
        else:
            text = getattr(response, "text", None)
            if not text:
                raise ProviderUnavailableError("empty response (no candidates / safety block)")
            raw = json.loads(text)  # malformed JSON → ValueError → hybrid falls back
        if not isinstance(raw, dict):
            raise ValueError("provider returned non-object JSON")
        usage = getattr(response, "usage_metadata", None)
        raw["_meta"] = {
            "provider": self.name,
            "requested_model": self.model,
            "actual_model": getattr(response, "model_version", None) or self.model,
            "request_id": getattr(response, "response_id", None),
            "latency_ms": latency_ms,
            "input_tokens": getattr(usage, "prompt_token_count", None),
            "output_tokens": getattr(usage, "candidates_token_count", None),
            "finish_reason": "stop",
        }
        return raw


class OpenRouterProvider:
    """OpenRouter chat-completions with strict JSON-schema response format.

    Key: OPENROUTER_API_KEY (+ optional OPENROUTER_HTTP_REFERER /
    OPENROUTER_APP_TITLE attribution headers). `http_client` is injectable
    (httpx.Client with MockTransport in tests). The configured model slug
    must support structured outputs — `provider.require_parameters` makes
    routing fail loudly instead of silently dropping the schema; the actual
    serving model is recorded from the response.
    """

    name = "openrouter"

    def __init__(
        self,
        model: str,
        timeout_s: float,
        max_output_tokens: int,
        temperature: float,
        http_client=None,
    ):
        self.model = model
        self.timeout_s = timeout_s
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        self._http_client = http_client

    def diagnose(self, snapshot: EvidenceSnapshot, config: dict) -> dict:
        api_key = read_secret("OPENROUTER_API_KEY")
        if not api_key:
            raise ProviderUnavailableError("OPENROUTER_API_KEY (or *_FILE) not set")
        import httpx

        headers = {"Authorization": f"Bearer {api_key}"}
        if os.environ.get("OPENROUTER_HTTP_REFERER"):
            headers["HTTP-Referer"] = os.environ["OPENROUTER_HTTP_REFERER"]
        if os.environ.get("OPENROUTER_APP_TITLE"):
            headers["X-Title"] = os.environ["OPENROUTER_APP_TITLE"]

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_INSTRUCTIONS},
                {"role": "user", "content": snapshot.render()},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "diagnosis_result",
                    "strict": True,
                    "schema": DiagnosisResult.model_json_schema(),
                },
            },
            "provider": {"require_parameters": True},
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "stream": False,
        }
        client = self._http_client or httpx.Client(timeout=self.timeout_s)
        t0 = time.monotonic()
        try:
            resp = client.post(
                f"{OPENROUTER_BASE_URL}/chat/completions", headers=headers, json=body
            )
        except httpx.TimeoutException as e:
            raise ProviderUnavailableError("timeout") from e
        except Exception as e:
            raise ProviderUnavailableError(f"transport: {type(e).__name__}") from e
        finally:
            if self._http_client is None:
                client.close()
        latency_ms = int((time.monotonic() - t0) * 1000)

        if resp.status_code == 401:
            raise ProviderUnavailableError("401 unauthorized (check OPENROUTER_API_KEY)")
        if resp.status_code == 402:
            raise ProviderUnavailableError("402 insufficient credits")
        if resp.status_code == 429:
            raise ProviderUnavailableError("429 rate limited")
        if resp.status_code >= 500:
            raise ProviderUnavailableError(f"{resp.status_code} upstream error")
        if resp.status_code != 200:
            raise ProviderUnavailableError(f"HTTP {resp.status_code}")

        payload = resp.json()
        choices = payload.get("choices") or []
        if not choices:
            raise ProviderUnavailableError(
                f"no choices returned ({json.dumps(payload.get('error', {}))[:200]})"
            )
        content = choices[0].get("message", {}).get("content") or ""
        raw = json.loads(content)  # malformed → ValueError → fallback
        if not isinstance(raw, dict):
            raise ValueError("provider returned non-object JSON")
        usage = payload.get("usage") or {}
        raw["_meta"] = {
            "provider": self.name,
            "requested_model": self.model,
            "actual_model": payload.get("model") or self.model,  # actual serving model
            "request_id": payload.get("id"),
            "latency_ms": latency_ms,
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "finish_reason": choices[0].get("finish_reason"),
        }
        return raw


class AnthropicProvider:
    """Optional Anthropic provider (official SDK), same contract/validation."""

    name = "anthropic"

    def __init__(
        self,
        model: str,
        timeout_s: float,
        max_output_tokens: int,
        temperature: float,
        client_factory=None,
    ):
        self.model = model
        self.timeout_s = timeout_s
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        self._client_factory = client_factory

    def _default_client(self):
        api_key = read_secret("ANTHROPIC_API_KEY")
        if not api_key:
            raise ProviderUnavailableError("ANTHROPIC_API_KEY (or *_FILE) not set")
        try:
            import anthropic
        except ImportError as e:
            raise ProviderUnavailableError(f"anthropic SDK not installed: {e}") from e
        return anthropic.Anthropic(api_key=api_key, timeout=self.timeout_s, max_retries=2)

    def diagnose(self, snapshot: EvidenceSnapshot, config: dict) -> dict:
        client = (self._client_factory or self._default_client)()
        t0 = time.monotonic()
        try:
            message = client.messages.create(
                model=self.model,
                max_tokens=self.max_output_tokens,
                temperature=self.temperature,
                system=SYSTEM_INSTRUCTIONS + f"\nOutput JSON schema: {_schema_hint()}",
                messages=[{"role": "user", "content": snapshot.render()}],
            )
        except ProviderUnavailableError:
            raise
        except Exception as e:
            raise ProviderUnavailableError(_safe_provider_error(e)) from e
        latency_ms = int((time.monotonic() - t0) * 1000)
        text = "".join(b.text for b in message.content if getattr(b, "type", "") == "text")
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ProviderUnavailableError("no JSON object in response")
        raw = json.loads(text[start : end + 1])
        if not isinstance(raw, dict):
            raise ValueError("provider returned non-object JSON")
        usage = getattr(message, "usage", None)
        raw["_meta"] = {
            "provider": self.name,
            "requested_model": self.model,
            "actual_model": getattr(message, "model", None) or self.model,
            "request_id": getattr(message, "id", None),
            "latency_ms": latency_ms,
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            "finish_reason": getattr(message, "stop_reason", None),
        }
        return raw


class FakeProvider:
    """Deterministic scripted provider for tests/eval — CI needs no API key.

    behavior: valid | malformed | hallucinated_citation | forbidden_tool |
              unavailable | oversize | injection_obedient | abstain |
              request_test | conflicting
    """

    name = "fake"
    model = "fake-model-1"

    def __init__(self, behavior: str = "valid", canned: dict | None = None):
        self.behavior = behavior
        self.canned = canned

    def diagnose(self, snapshot: EvidenceSnapshot, config: dict) -> dict:
        meta = {
            "provider": self.name,
            "requested_model": self.model,
            "actual_model": self.model,
            "request_id": "fake-req",
            "latency_ms": 1,
            "input_tokens": 100,
            "output_tokens": 50,
            "finish_reason": "stop",
        }
        if self.behavior == "unavailable":
            raise ProviderUnavailableError("fake timeout")
        if self.behavior == "malformed":
            return {"hypotheses": "not-a-list", "surprise": True, "_meta": meta}
        if self.canned is not None:
            return {**self.canned, "_meta": meta}
        top = snapshot.baseline.get("hypotheses", [])
        first_cause = top[0]["cause_id"] if top else next(iter(snapshot.allowed_cause_ids), "x")
        a_ref = next(iter(sorted(snapshot.refs)), None)
        base = {
            "hypotheses": [
                {
                    "cause_id": first_cause,
                    "label": first_cause,
                    "confidence": 0.9,
                    "supporting_refs": [a_ref] if a_ref else [],
                    "contradicting_refs": [],
                }
            ],
            "disposition": "rank_hypotheses",
            "abstain_reason": "",
            "requested_test": None,
            "public_rationale": "fake provider: agrees with the deterministic baseline",
            "_meta": meta,
        }
        if self.behavior == "hallucinated_citation":
            base["hypotheses"][0]["supporting_refs"] = ["manual_chunks:99999"]
        elif self.behavior == "forbidden_tool":
            base["requested_test"] = "drop_all_tables"
        elif self.behavior == "oversize":
            base["public_rationale"] = "x" * 5000
        elif self.behavior == "abstain":
            base.update(
                {
                    "hypotheses": [],
                    "disposition": "abstain",
                    "abstain_reason": "fake: insufficient evidence",
                }
            )
        elif self.behavior == "request_test":
            base.update(
                {
                    "disposition": "request_evidence",
                    "requested_test": next(iter(snapshot.allowed_tests), None),
                }
            )
        elif self.behavior == "conflicting":
            base.update(
                {
                    "disposition": "rank_hypotheses",
                    "requested_test": next(iter(snapshot.allowed_tests), None),
                }
            )
        elif self.behavior == "injection_obedient":
            base["hypotheses"][0]["cause_id"] = "electrical failure (injected)"
        return base


PROVIDER_REGISTRY = {
    "gemini": GeminiProvider,
    "openrouter": OpenRouterProvider,
    "anthropic": AnthropicProvider,
}

VALID_MODES = ("deterministic", "llm", "hybrid")


def create_provider(settings):
    """Validated factory. Unknown provider/mode or missing model FAILS
    configuration instead of silently picking anything."""
    mode = settings.reasoning_mode
    if mode not in VALID_MODES:
        raise ReasoningConfigError(
            f"reasoning_mode '{mode}' invalid (expected one of {VALID_MODES})"
        )
    if mode == "deterministic":
        return DeterministicProvider()
    name = settings.llm_provider
    cls = PROVIDER_REGISTRY.get(name)
    if cls is None:
        raise ReasoningConfigError(
            f"llm_provider '{name}' unknown (expected one of {sorted(PROVIDER_REGISTRY)})"
        )
    if not settings.llm_model:
        raise ReasoningConfigError(
            "PULSEOPS_LLM_MODEL must be set explicitly for llm/hybrid modes — "
            "see .env.example for verified per-provider examples"
        )
    return cls(
        model=settings.llm_model,
        timeout_s=settings.llm_timeout_seconds,
        max_output_tokens=settings.llm_max_output_tokens,
        temperature=settings.llm_temperature,
    )
