# Architecture Decision Records

Format: one file per decision, `NNN-title.md`, with Status / Context / Decision / Consequences / Alternatives. An accepted ADR is changed only by a new ADR that supersedes it.

| ADR | Title | Status |
| --- | --- | --- |
| [001](001-modular-monolith.md) | Modular monolith with Temporal workers | Accepted |
| [002](002-tenancy-rls.md) | Tenant isolation with PostgreSQL RLS | Accepted |
| [003](003-role-model.md) | Two-level role model | Accepted |
| [004](004-temporal-and-langgraph.md) | Temporal for durability, LangGraph for agent reasoning | Accepted |
| [005](005-llm-gateway.md) | Single LLM gateway over LiteLLM | Accepted |
| [006](006-secrets-encryption.md) | Envelope encryption for secrets | Accepted |
| [007](007-runtime-configuration.md) | Runtime configuration in the database | Accepted |
| [008](008-vector-and-fulltext.md) | Vector and full-text search on PostgreSQL | Accepted; BM25 part superseded by 016 |
| [009](009-observability.md) | OpenTelemetry with traces stored in Forge; Langfuse optional | Accepted |
| [010](010-use-case-graph-schema.md) | Use case graph JSON schema v1 | Accepted |
| [011](011-api-conventions.md) | API conventions | Accepted |
| [012](012-frontend-architecture.md) | Frontend architecture | Accepted |
| [013](013-guardrail-models.md) | Guardrail models as external endpoints | Accepted |
| [014](014-frontend-licensing.md) | Angular 21 + PrimeNG 21 (MIT) instead of PrimeNG 22 | Superseded by 017 |
| [015](015-object-storage.md) | S3 API for object storage; SeaweedFS in Compose | Accepted (PO, 2026-10-19) |
| [016](016-external-bm25-opensearch.md) | BM25 full-text search in OpenSearch | Accepted |
| [017](017-ng-zorro-angular-22.md) | NG-ZORRO on the latest Angular instead of PrimeNG | Accepted |
| [018](018-bundle-format.md) | Export/import bundles: a versioned JSON format | Accepted, format to be confirmed |

PO decisions behind 003, 004, 007, 009, 013 and the Phase 1 schedule were confirmed on 2026-10-16 (see [analysis §6](../planning/01-spec-analysis.md#6-questions-for-the-product-owner)). UI languages confirmed: Italian and English. On 2026-10-18 the PO replaced pg_search with an external engine (016) and PrimeNG with NG-ZORRO (017). On 2026-10-19 the PO accepted SeaweedFS instead of MinIO and both external S3 and SeaweedFS in the Helm chart (015). On the same day the PO asked for export/import with multi-version support (018), a dark theme, corporate proxy support, exact tokenizers and groups-and-roles document access. Open: confirm the bundle format (018).
