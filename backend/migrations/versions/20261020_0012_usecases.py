"""M2.3a Use cases: catalogs, categories, access by Role Name, use cases with published versions
(docs/requirements/phase-2.md, M2.3).

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-20
"""

from __future__ import annotations

from alembic import op

from forge.core.rls import enable_project_rls
from forge.core.sqlscript import statements

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

TABLES = (
    "catalog",
    "category",
    "catalog_access",
    "category_exclusion",
    "use_case",
    "use_case_version",
)


def run(sql: str) -> None:
    for stmt in statements(sql):
        op.execute(stmt)


def upgrade() -> None:
    run(
        """
        CREATE TABLE catalog (
            id           uuid PRIMARY KEY,
            project_id   uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            name         text NOT NULL CHECK (name ~ '^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}$'),
            description  text NOT NULL DEFAULT '',
            status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
            builtin      boolean NOT NULL DEFAULT false,
            created_at   timestamp with time zone NOT NULL DEFAULT now(),
            created_by   uuid REFERENCES app_user(id)
        );
        CREATE UNIQUE INDEX catalog_name_key ON catalog (project_id, lower(name));

        CREATE TABLE category (
            id           uuid PRIMARY KEY,
            project_id   uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            catalog_id   uuid NOT NULL REFERENCES catalog(id) ON DELETE CASCADE,
            name         text NOT NULL CHECK (name ~ '^[A-Za-z0-9][A-Za-z0-9 _.&-]{0,79}$'),
            description  text NOT NULL DEFAULT '',
            created_at   timestamp with time zone NOT NULL DEFAULT now(),
            created_by   uuid REFERENCES app_user(id)
        );
        CREATE UNIQUE INDEX category_name_key ON category (catalog_id, lower(name));

        -- Enabling a catalog for a Role Name enables all its categories; exclusions switch
        -- individual categories off again (US-2.4.2).
        CREATE TABLE catalog_access (
            project_id    uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            role_name_id  uuid NOT NULL REFERENCES role_name(id) ON DELETE CASCADE,
            catalog_id    uuid NOT NULL REFERENCES catalog(id) ON DELETE CASCADE,
            PRIMARY KEY (role_name_id, catalog_id)
        );
        CREATE TABLE category_exclusion (
            project_id    uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            role_name_id  uuid NOT NULL REFERENCES role_name(id) ON DELETE CASCADE,
            category_id   uuid NOT NULL REFERENCES category(id) ON DELETE CASCADE,
            PRIMARY KEY (role_name_id, category_id)
        );

        CREATE TABLE use_case (
            id                 uuid PRIMARY KEY,
            project_id         uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            name               text NOT NULL CHECK (name ~ '^[A-Za-z0-9][A-Za-z0-9 _.()-]{0,79}$'),
            description        text NOT NULL DEFAULT '',
            icon               text NOT NULL DEFAULT '',
            tags               text[] NOT NULL DEFAULT '{}',
            catalog_id         uuid REFERENCES catalog(id),
            category_id        uuid REFERENCES category(id),
            pattern            text NOT NULL CHECK (pattern IN ('supervisor', 'graph')),
            status             text NOT NULL DEFAULT 'draft'
                               CHECK (status IN ('draft', 'testing', 'published', 'deprecated')),
            input_fields       jsonb NOT NULL DEFAULT '[]',
            output_type        text NOT NULL DEFAULT 'markdown'
                               CHECK (output_type IN
                                      ('markdown', 'json', 'docx', 'xlsx', 'pdf', 'chat')),
            output_schema      jsonb,
            defaults           jsonb NOT NULL DEFAULT '{}',
            definition         jsonb NOT NULL DEFAULT '{}',
            latest_version     int NOT NULL DEFAULT 0,
            published_version  int,
            dirty              boolean NOT NULL DEFAULT true,
            created_at         timestamp with time zone NOT NULL DEFAULT now(),
            created_by         uuid REFERENCES app_user(id),
            updated_at         timestamp with time zone NOT NULL DEFAULT now(),
            updated_by         uuid REFERENCES app_user(id)
        );
        CREATE UNIQUE INDEX use_case_name_key ON use_case (project_id, lower(name));
        CREATE INDEX use_case_category ON use_case (category_id);

        -- Insert-only: a published version never changes (US-2.5.2).
        CREATE TABLE use_case_version (
            id              uuid PRIMARY KEY,
            project_id      uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            use_case_id     uuid NOT NULL REFERENCES use_case(id) ON DELETE CASCADE,
            version         int NOT NULL CHECK (version > 0),
            name            text NOT NULL,
            description     text NOT NULL,
            pattern         text NOT NULL,
            input_fields    jsonb NOT NULL,
            input_schema    jsonb NOT NULL,
            output_type     text NOT NULL,
            output_schema   jsonb,
            defaults        jsonb NOT NULL,
            definition      jsonb NOT NULL,
            note            text NOT NULL DEFAULT '',
            published_at    timestamp with time zone NOT NULL DEFAULT now(),
            published_by    uuid REFERENCES app_user(id),
            UNIQUE (use_case_id, version)
        );
        """
    )
    for table in TABLES:
        run(enable_project_rls(table))


def downgrade() -> None:
    run(
        """
        DROP TABLE IF EXISTS use_case_version, use_case, category_exclusion, catalog_access,
            category, catalog;
        """
    )
