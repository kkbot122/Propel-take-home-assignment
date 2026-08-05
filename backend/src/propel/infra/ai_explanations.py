import json
import logging
from time import monotonic
from typing import Any

import httpx

from propel.incidents.explanations import (
    MAX_SECTION_LENGTH,
    ExplanationFallbackReason,
    ExplanationInput,
    ExplanationProviderError,
    GeneratedExplanation,
)

logger = logging.getLogger(__name__)

EXPLANATION_SCHEMA = {
    "name": "incident_explanation",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "what_happened": {"type": "string", "minLength": 1, "maxLength": 320},
            "why_this_cause": {"type": "string", "minLength": 1, "maxLength": 320},
            "what_happens_next": {"type": "string", "minLength": 1, "maxLength": 320},
        },
        "required": ["what_happened", "why_this_cause", "what_happens_next"],
        "additionalProperties": False,
    },
}

SYSTEM_PROMPT = """You explain an already-decided electricity outage finding to a
non-technical operator. Use only the supplied structured evidence. Do not infer a different fault,
asset, score, ticket state, or restoration result. Do not mention simulator ground truth. Be concise
and concrete. Return exactly the three requested JSON fields. Explain uncertainty honestly and
distinguish operator actions from automatic telemetry verification and closure."""


class OpenAICompatibleExplanationGateway:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        max_input_bytes: int,
        max_output_tokens: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._model = model
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._max_input_bytes = max_input_bytes
        self._max_output_tokens = max_output_tokens
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
        )

    async def generate(self, explanation_input: ExplanationInput) -> GeneratedExplanation:
        input_json = explanation_input.as_json()
        if len(input_json.encode("utf-8")) > self._max_input_bytes:
            raise ExplanationProviderError(ExplanationFallbackReason.INPUT_TOO_LARGE)

        started_at = monotonic()
        fallback_reason: ExplanationFallbackReason | None = None
        usage: dict[str, Any] | None = None
        try:
            response = await self._client.post(
                "/chat/completions",
                headers=self._headers,
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": input_json},
                    ],
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": EXPLANATION_SCHEMA,
                    },
                    "max_tokens": self._max_output_tokens,
                    "temperature": 0,
                },
            )
            response.raise_for_status()
            response_body = response.json()
            if not isinstance(response_body, dict):
                raise ExplanationProviderError(ExplanationFallbackReason.INVALID_RESPONSE)
            usage_value = response_body.get("usage")
            usage = usage_value if isinstance(usage_value, dict) else None
            message = response_body["choices"][0]["message"]
            if not isinstance(message, dict):
                raise ExplanationProviderError(ExplanationFallbackReason.INVALID_RESPONSE)
            if message.get("refusal"):
                raise ExplanationProviderError(ExplanationFallbackReason.REFUSAL)
            content = message["content"]
            if not isinstance(content, str):
                raise ExplanationProviderError(ExplanationFallbackReason.INVALID_RESPONSE)
            return _parse_generated_explanation(content)
        except httpx.TimeoutException as error:
            fallback_reason = ExplanationFallbackReason.TIMEOUT
            raise ExplanationProviderError(fallback_reason) from error
        except ExplanationProviderError as error:
            fallback_reason = error.reason
            raise
        except (
            httpx.HTTPError,
            json.JSONDecodeError,
            KeyError,
            IndexError,
            TypeError,
        ) as error:
            fallback_reason = ExplanationFallbackReason.PROVIDER_ERROR
            raise ExplanationProviderError(fallback_reason) from error
        finally:
            logger.info(
                json.dumps(
                    {
                        "event": "ai_explanation_provider_call",
                        "model": self._model,
                        "latency_ms": round((monotonic() - started_at) * 1_000, 2),
                        "fallback_reason": fallback_reason.value if fallback_reason else None,
                        "prompt_tokens": usage.get("prompt_tokens") if usage else None,
                        "completion_tokens": usage.get("completion_tokens") if usage else None,
                    }
                )
            )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _parse_generated_explanation(content: str) -> GeneratedExplanation:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as error:
        raise ExplanationProviderError(ExplanationFallbackReason.INVALID_RESPONSE) from error
    if not isinstance(value, dict) or set(value) != {
        "what_happened",
        "why_this_cause",
        "what_happens_next",
    }:
        raise ExplanationProviderError(ExplanationFallbackReason.INVALID_RESPONSE)
    sections: dict[str, str] = {}
    for name, section in value.items():
        if not isinstance(section, str):
            raise ExplanationProviderError(ExplanationFallbackReason.INVALID_RESPONSE)
        normalized = " ".join(section.split())
        if not normalized or len(normalized) > MAX_SECTION_LENGTH:
            raise ExplanationProviderError(ExplanationFallbackReason.INVALID_RESPONSE)
        sections[name] = normalized
    return GeneratedExplanation(**sections)
