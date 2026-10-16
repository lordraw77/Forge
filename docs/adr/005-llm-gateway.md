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
- LiteLLM pinned to an exact version with hash-locked dependencies; upgrades go through the provider contract test suite (mock servers for each provider type).

## Consequences

- One place for limits, costs and tracing.
- LiteLLM can be replaced behind `ProviderAdapter`.
