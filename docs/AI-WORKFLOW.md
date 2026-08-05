# AI incident explanation workflow

Propel can translate one selected finding into three short operator-facing
sections: what happened, why the probable cause was chosen, and what happens
next. The localization result, confidence score, ticket state, and restoration
state are decided before this feature runs and remain authoritative.

## Runtime path

1. The browser posts the selected incident ID to
   `/api/incidents/{id}/explanation`.
2. The API loads the current incident and ticket from PostgreSQL.
3. An allowlist projects classification, asset, precision, affected count,
   confidence evidence, contradictions or gaps, suppression, ticket status, and
   restoration counts. Raw telemetry, location data, operator text, crew names,
   and simulator ground truth are excluded.
4. If all provider settings exist, the API sends the bounded projection to an
   OpenAI-compatible `/chat/completions` endpoint using a strict JSON schema.
5. Missing configuration, timeout, provider error, refusal, or invalid output
   immediately produces the same three sections from deterministic templates.
6. The browser labels the source and caches the response by incident and ticket
   update timestamps. A state change creates a new cache key.

Generated prose is not persisted, audited, or used as input to any command.
Provider failure does not affect health, localization, ticket actions, or
telemetry-verified closure.

## Configuration

Set `AI_EXPLAINER_BASE_URL`, `AI_EXPLAINER_API_KEY`, and
`AI_EXPLAINER_MODEL` on `backend-api` to enable model calls. The base URL must
include the compatible API prefix, commonly `/v1`. Timeout, input bytes, and
output tokens have bounded settings documented in `.env.example` and
`docs/DEPLOYMENT.md`. Leave the required settings empty to exercise the normal
deterministic fallback.

Provider logs contain model, latency, available token counts, and fallback
category. They never contain prompts, projected evidence, generated prose, or
credentials.
