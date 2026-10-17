"""M1.2 LLM gateway: providers, models, enablement, aliases, project limits, call log
(docs/architecture/m1.2-llm-gateway.md, ADR-005).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-17
"""

from __future__ import annotations

from alembic import op

from forge.core.rls import enable_project_rls
from forge.core.sqlscript import statements

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

PROVIDER_TYPES = (
    "vllm",
    "llamacpp",
    "nim",
    "ollama",
    "openai",
    "azure",
    "anthropic",
    "bedrock",
    "gemini",
    "vertex",
    "mistral",
    "openai_compatible",
)
MODEL_TYPES = ("chat", "embedding", "reranker", "speech", "guard")
ZERO = "'00000000-0000-0000-0000-000000000000'"


def run(sql: str) -> None:
    for stmt in statements(sql):
        op.execute(stmt)


def upgrade() -> None:
    provider_types = ", ".join(f"'{t}'" for t in PROVIDER_TYPES)
    model_types = ", ".join(f"'{t}'" for t in MODEL_TYPES)
    run(
        f"""
        CREATE TABLE model_provider (
            id             uuid PRIMARY KEY,
            project_id     uuid REFERENCES project(id) ON DELETE CASCADE,
            name           text NOT NULL,
            provider_type  text NOT NULL CHECK (provider_type IN ({provider_types})),
            base_url       text,
            secret_id      uuid REFERENCES secret(id),
            config         jsonb NOT NULL DEFAULT '{{}}',
            status         text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
            created_at     timestamptz NOT NULL DEFAULT now(),
            created_by     uuid REFERENCES app_user(id),
            updated_at     timestamptz NOT NULL DEFAULT now(),
            updated_by     uuid REFERENCES app_user(id)
        );
        CREATE UNIQUE INDEX model_provider_name_key
            ON model_provider (coalesce(project_id, {ZERO}), lower(name));

        CREATE TABLE model (
            id                 uuid PRIMARY KEY,
            project_id         uuid REFERENCES project(id) ON DELETE CASCADE,
            provider_id        uuid NOT NULL REFERENCES model_provider(id),
            name               text NOT NULL,
            display_name       text NOT NULL,
            model_type         text NOT NULL CHECK (model_type IN ({model_types})),
            model_id           text NOT NULL,
            params             jsonb NOT NULL DEFAULT '{{}}',
            context_window     int CHECK (context_window > 0),
            max_output_tokens  int CHECK (max_output_tokens > 0),
            embedding_dims     int CHECK (embedding_dims BETWEEN 1 AND 4000),
            cost_input         numeric(14, 6) NOT NULL DEFAULT 0 CHECK (cost_input >= 0),
            cost_output        numeric(14, 6) NOT NULL DEFAULT 0 CHECK (cost_output >= 0),
            capabilities       text[] NOT NULL DEFAULT '{{}}',
            timeout_s          int NOT NULL DEFAULT 60 CHECK (timeout_s BETWEEN 1 AND 3600),
            max_retries        int NOT NULL DEFAULT 2 CHECK (max_retries BETWEEN 0 AND 10),
            rpm_limit          int CHECK (rpm_limit > 0),
            all_projects       boolean NOT NULL DEFAULT false,
            status             text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
            created_at         timestamptz NOT NULL DEFAULT now(),
            created_by         uuid REFERENCES app_user(id),
            updated_at         timestamptz NOT NULL DEFAULT now(),
            updated_by         uuid REFERENCES app_user(id),
            CHECK (model_type <> 'embedding' OR embedding_dims IS NOT NULL),
            CHECK (project_id IS NULL OR NOT all_projects)
        );
        CREATE UNIQUE INDEX model_name_key ON model (coalesce(project_id, {ZERO}), lower(name));
        CREATE INDEX model_provider_idx ON model (provider_id);

        -- Which projects may use a platform model (F1.2 "Enabled projects").
        CREATE TABLE model_enablement (
            model_id    uuid NOT NULL REFERENCES model(id) ON DELETE CASCADE,
            project_id  uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            created_at  timestamptz NOT NULL DEFAULT now(),
            created_by  uuid REFERENCES app_user(id),
            PRIMARY KEY (model_id, project_id)
        );
        CREATE INDEX model_enablement_project ON model_enablement (project_id);

        -- Alias -> ordered fallback chain; project rows override platform defaults (F1.4).
        CREATE TABLE model_alias (
            id          uuid PRIMARY KEY,
            project_id  uuid REFERENCES project(id) ON DELETE CASCADE,
            name        text NOT NULL,
            model_ids   uuid[] NOT NULL CHECK (cardinality(model_ids) BETWEEN 1 AND 10),
            updated_at  timestamptz NOT NULL DEFAULT now(),
            updated_by  uuid REFERENCES app_user(id)
        );
        CREATE UNIQUE INDEX model_alias_name_key
            ON model_alias (coalesce(project_id, {ZERO}), lower(name));

        -- Project limits within the platform ceilings (setting gateway.limits); NULL = inherit.
        CREATE TABLE gateway_project_config (
            project_id                 uuid PRIMARY KEY REFERENCES project(id) ON DELETE CASCADE,
            tokens_per_day             bigint CHECK (tokens_per_day >= 0),
            requests_per_minute        int CHECK (requests_per_minute >= 0),
            user_tokens_per_day        bigint CHECK (user_tokens_per_day >= 0),
            user_requests_per_minute   int CHECK (user_requests_per_minute >= 0),
            max_tokens_per_call        int CHECK (max_tokens_per_call >= 0),
            max_tokens_policy          text CHECK (max_tokens_policy IN ('clamp', 'reject')),
            cache_enabled              boolean NOT NULL DEFAULT false,
            cache_ttl_s                int NOT NULL DEFAULT 3600 CHECK (cache_ttl_s BETWEEN 1 AND 2592000),
            cache_nonzero_temperature  boolean NOT NULL DEFAULT false,
            updated_at                 timestamptz NOT NULL DEFAULT now(),
            updated_by                 uuid REFERENCES app_user(id)
        );

        -- One row per gateway request (ADR-009: Forge's own telemetry, source of cost totals).
        CREATE TABLE llm_call (
            id            uuid PRIMARY KEY,
            project_id    uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            user_id       uuid,
            api_key_id    uuid,
            endpoint      text NOT NULL,
            requested     text NOT NULL,
            model_id      uuid,
            model_name    text,
            status        text NOT NULL,
            http_status   int NOT NULL,
            error_code    text,
            attempts      jsonb NOT NULL DEFAULT '[]',
            stream        boolean NOT NULL DEFAULT false,
            cache_hit     boolean NOT NULL DEFAULT false,
            tokens_in     int NOT NULL DEFAULT 0,
            tokens_out    int NOT NULL DEFAULT 0,
            usage_source  text NOT NULL DEFAULT 'provider'
                          CHECK (usage_source IN ('provider', 'estimated', 'cache', 'none')),
            cost          numeric(18, 8) NOT NULL DEFAULT 0,
            ttft_ms       int,
            latency_ms    int NOT NULL,
            trace_id      text,
            created_at    timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX llm_call_project_time ON llm_call (project_id, created_at DESC);
        CREATE INDEX llm_call_model_time ON llm_call (model_id, created_at DESC);
        """
    )
    for table in ("model_provider", "model", "model_alias"):
        run(enable_project_rls(table, nullable_project=True))
    for table in ("model_enablement", "gateway_project_config", "llm_call"):
        run(enable_project_rls(table))

    # Calling models is a builder/admin capability, and part of the default project token.
    op.execute(
        "UPDATE role_type SET permissions = array_append(permissions, 'llm.invoke') "
        "WHERE key IN ('project_admin', 'builder') AND NOT 'llm.invoke' = ANY(permissions)"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE role_type SET permissions = array_remove(permissions, 'llm.invoke') "
        "WHERE key IN ('project_admin', 'builder')"
    )
    run(
        """
        DROP TABLE IF EXISTS llm_call, gateway_project_config, model_alias, model_enablement,
            model, model_provider CASCADE;
        """
    )
