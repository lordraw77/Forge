# Spike E0.5d – LiteLLM against provider wire formats (ADR-005)

_Date: 2026-10-16. Code: [backend/spikes/e05d/](../../backend/spikes/e05d/). Raw results: [e05d-litellm.json](e05d-litellm.json). Run: `make spike/e05d`._

## Question

Can LiteLLM, used as a library behind `ProviderAdapter`, serve every provider type in F1? The checks are:

- chat, streaming with token usage, tool calling, embeddings and rerank;
- error mapping, retries and timeouts that drive Forge's fallback logic;
- per-call overhead within the 50 ms p95 gateway budget;
- operation **air-gapped**.

**Result: yes, with one gap.** 35 of 36 matrix checks and 8 of 8 extra checks passed. The overhead is about 4 ms at p95. LiteLLM does not forward `stream_options` for Mistral, so Forge must not depend on providers reporting usage in streams.

## Setup

- **Versions:** LiteLLM 1.105.1, with `LITELLM_LOCAL_MODEL_COST_MAP=True` and telemetry off.
- **Mock server:** one server ([mock_providers.py](../../backend/spikes/e05d/mock_providers.py)) speaks each wire format and records every request:
  - OpenAI Chat Completions, embeddings and rerank;
  - Azure deployment paths;
  - the Ollama native API (`/api/chat`, `/api/embed`);
  - the Anthropic Messages API, including its SSE event stream.
- **Network isolation:** the whole run executes inside a Linux network namespace with only loopback (`unshare --net`). Any outbound call LiteLLM attempted would fail; the run verified that `pypi.org` was unreachable.

## Results

| Forge provider type | LiteLLM prefix | chat | stream + usage | tools | 429 → `RateLimitError` | 5xx retried | timeout |
| --- | --- | :-: | :-: | :-: | :-: | :-: | :-: |
| vLLM | `hosted_vllm/` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| OpenAI-compatible (llama.cpp, NIM, TGI, LM Studio, generic) | `openai/` + `api_base` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Ollama (native API) | `ollama_chat/` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Azure OpenAI | `azure/` + `api_version` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Anthropic | `anthropic/` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Mistral | `mistral/` | ✅ | ⚠️ text ok, **usage missing** | ✅ | ✅ | ✅ | ✅ |

| Extra check | Result |
| --- | --- |
| Embeddings: vLLM, OpenAI-compatible, Ollama (`/api/embed`) | ✅ ✅ ✅ |
| Rerank: vLLM (`hosted_vllm/`, `POST /rerank`, `top_n` honoured) | ✅ |
| Azure: deployment in path, `api-version` query, `api-key` header | ✅ |
| Anthropic: `x-api-key` and `anthropic-version: 2023-06-01` headers | ✅ |
| vLLM: `Authorization: Bearer` | ✅ |
| Ollama: native `/api/chat` used (not the OpenAI shim) | ✅ |

| Performance | Value |
| --- | --- |
| Raw httpx call to mock, p50 / p95 | 2.6 / 4.1 ms |
| Same call through LiteLLM, p50 / p95 | 5.5 / 8.3 ms |
| **LiteLLM overhead, p50 / p95** | **2.9 / 4.2 ms** (budget: 50 ms for the whole gateway) |
| `import litellm` | 3.1 s, RSS 293 MB after import |

## Findings and rules for the gateway (E1.5–E1.7)

1. **Do not rely on providers for stream usage.**
   - **What happened:** for `mistral/`, LiteLLM drops `stream_options` from the request; the recorded body had no `stream_options`. A provider that does not send usage unasked then reports none.
   - **Rule:** when a stream ends without usage, the gateway estimates tokens with a tokenizer (prompt and completion), records `usage_source = "estimated"` on the `llm_call`, and uses the estimate for quota reconciliation and cost.
   - **Why it matters:** this keeps AC9 (totals match) and AC10 (quotas) intact.
   - **Note:** whether the real Mistral API sends usage unasked was not verifiable without credentials. E1.6 must check it with a real key.
2. **Retries and fallback stay in Forge, not in LiteLLM.** `num_retries` works: a 5xx succeeded on the 3rd attempt. But ADR-005 puts retries, the fallback chain and per-attempt tracing in the gateway, so Forge calls LiteLLM with `num_retries=0` and uses the mapped exceptions to decide:
   - `RateLimitError`: next model, or 429 to the client;
   - `Timeout` and `APIConnectionError`: retry, then next model;
   - 4xx: fail.
3. **Air-gapped operation works** with `LITELLM_LOCAL_MODEL_COST_MAP=True` and telemetry disabled. Forge computes costs from its own model registry (F1.2 cost fields) and never from LiteLLM's cost map.
4. **Import cost.** Importing LiteLLM takes about 3 s and 290 MB RSS per process. It is acceptable, but only `forge.gateway` may import it, which is already enforced by import-linter. Import it lazily at gateway start-up, not at module import of unrelated commands such as `forge migrate`.
5. **Provider configuration mapping:** F1 provider type → LiteLLM `model` prefix plus `api_base`, `api_key` and `api_version`.
   - Generic OpenAI-compatible servers use `openai/` with a custom `api_base`.
   - NVIDIA NIM uses the same mapping, plus NeMo Retriever endpoints for embedding and rerank, to be checked in E1.5.

## Not covered (follow-ups)

| Gap | Where it gets covered |
| --- | --- |
| AWS Bedrock (SigV4), Google Gemini/Vertex: need real credentials or AWS/GCP emulators | E1.5, with test accounts |
| Real servers: vLLM, Ollama, llama.cpp, NIM; quirks the mocks do not reproduce | E1.5 contract tests against containers (vLLM CPU image, Ollama with a tiny model) |
| Tool-call streaming (incremental tool-call deltas) | E1.6 |
| Gateway overhead with guardrails and quota checks in the path (NFR: < 50 ms p95) | E1.7, E2.11 |
| Licence check on every LiteLLM upgrade (wheel 1.105.1 declares MIT and ships no `enterprise` module) | CI licence check, E1.5 |

## Decision

ADR-005 is **confirmed**. The usage-estimation rule and "no retries inside LiteLLM" are added to it.
