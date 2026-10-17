# ADR-005 – Single LLM gateway over LiteLLM

- Status: Accepted
- Date: 2026-10-16

## Context

Every model is external (F1). All LLM, embedding and rerank calls must be traced, quota-limited, guarded and costed. Many provider types are required.

## Decision

- The `gateway` module is the only code allowed to talk to model endpoints. Enforced by `import-linter` (only `gateway` may import `litellm`).
- Internal interface:

  ```python
  class ProviderAdapter(Protocol):
      async def chat(self, req: ChatRequest) -> ChatResponse | AsyncIterator[ChatChunk]: ...
      async def embed(self, req: EmbedRequest) -> EmbedResponse: ...
      async def rerank(self, req: RerankRequest) -> RerankResponse: ...
      async def test_connection(self, model: ModelConfig) -> TestResult: ...
  ```

  `LiteLLMAdapter` implements it; a dedicated adapter is added only where LiteLLM lacks a feature (e.g. a rerank endpoint shape).
- Call pipeline: resolve alias → check model enabled for project → quota reserve (Redis) → input guardrails → cache lookup → call with timeout/retries/fallback chain → output guardrails → reconcile quota → record `llm_call` (tokens, TTFT, latency, cost, outcome) → OTel span.
- Same pipeline serves `/v1/chat/completions`, `/v1/embeddings`, internal calls from prompts, RAG and agents.
- Model config is read from the runtime config cache (ADR-007): changes apply without restart.
- Cache: exact match on hash of (model, messages, params), TTL per project, off by default; never for `temperature > 0` unless enabled explicitly.
- Retries and fallback are done by the gateway, not LiteLLM: calls use `num_retries=0`, and LiteLLM's mapped exceptions (`RateLimitError`, `Timeout`, `APIConnectionError`, 4xx) decide retry, next model or failure (spike E0.5d).
- Token usage is never assumed to be present in streams (LiteLLM drops `stream_options` for some providers, e.g. Mistral): when a stream ends without usage, the gateway estimates tokens with a tokenizer and records `usage_source = "estimated"` for quotas and cost.
- Air-gapped: `LITELLM_LOCAL_MODEL_COST_MAP=True`, telemetry off; costs come only from Forge's model registry. Validated by spike E0.5d inside a network namespace with loopback only: 35/36 provider checks, overhead 4 ms p95 ([report](../spikes/e05d-litellm-providers.md)).
- LiteLLM pinned to an exact version with hash-locked dependencies; upgrades go through the provider contract test suite (mock servers for each provider type).

## Notes from M1.2 (2026-10-17)

These notes clarify the decision; they do not change it ([design](../architecture/m1.2-llm-gateway.md)).

- **Stream usage.** LiteLLM 1.105.1 already fills missing stream usage with its own tokenizer, and the result cannot be told apart from provider usage. Forge records LiteLLM-reported stream usage as `provider`, and records `estimated` only when no usage arrives at all. Quotas and cost are correct either way.
- **More error kinds.** Billing errors (402) and retired models (410) go to the next model in the chain, like credential errors (401/403).
- **Secret scrubbing.** Provider error text is scrubbed of the provider's key and of key-shaped strings before it is returned, logged or stored.

## Consequences

- One place for limits, costs and tracing.
- LiteLLM can be replaced behind `ProviderAdapter`.
