"""M1.1 platform core: organization, users, sessions, projects, roles, memberships, API keys,
secrets, settings, audit (docs/architecture/m1.1-platform-core.md).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-16
"""

from __future__ import annotations

from alembic import op

from forge.core.rls import enable_project_rls
from forge.core.sqlscript import statements

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

ORG_ID = "00000000-0000-7000-8000-000000000001"

# ADR-003 default permission matrix.
ROLE_TYPES = {
    "project_admin": (
        "Project Admin",
        [
            "project.manage",
            "asset.write",
            "usecase.build",
            "usecase.publish",
            "approval.decide",
            "usecase.run",
            "run.read",
        ],
    ),
    "builder": (
        "Builder",
        [
            "asset.write",
            "usecase.build",
            "usecase.publish",
            "approval.decide",
            "usecase.run",
            "run.read",
        ],
    ),
    "operator": ("Operator", ["approval.decide", "usecase.run", "run.read"]),
    "end_user": ("User", ["usecase.run", "run.read.own"]),
    "viewer": ("Viewer", ["run.read"]),
}


def run(sql: str) -> None:
    for stmt in statements(sql):
        op.execute(stmt)


def upgrade() -> None:
    run(
        """
        CREATE TABLE organization (
            id          uuid PRIMARY KEY,
            name        text NOT NULL,
            settings    jsonb NOT NULL DEFAULT '{}',
            created_at  timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE app_user (
            id                    uuid PRIMARY KEY,
            org_id                uuid NOT NULL REFERENCES organization(id),
            username              text NOT NULL,
            email                 text NOT NULL,
            full_name             text NOT NULL DEFAULT '',
            auth_source           text NOT NULL DEFAULT 'local'
                                  CHECK (auth_source IN ('local', 'ldap', 'oidc')),
            password_hash         text,
            is_super_admin        boolean NOT NULL DEFAULT false,
            must_change_password  boolean NOT NULL DEFAULT false,
            totp_secret_id        uuid,
            totp_enabled          boolean NOT NULL DEFAULT false,
            totp_last_step        bigint NOT NULL DEFAULT 0,
            failed_logins         int NOT NULL DEFAULT 0,
            first_failed_at       timestamptz,
            locked_until          timestamptz,
            eula_version          text,
            status                text NOT NULL DEFAULT 'active'
                                  CHECK (status IN ('active', 'disabled')),
            last_active_at        timestamptz,
            created_at            timestamptz NOT NULL DEFAULT now(),
            created_by            uuid,
            updated_at            timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX app_user_username_key ON app_user (lower(username));
        CREATE UNIQUE INDEX app_user_email_key ON app_user (lower(email));

        CREATE TABLE password_history (
            id          uuid PRIMARY KEY,
            user_id     uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
            hash        text NOT NULL,
            created_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX password_history_user ON password_history (user_id, created_at DESC);

        CREATE TABLE recovery_code (
            id          uuid PRIMARY KEY,
            user_id     uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
            code_hash   bytea NOT NULL,
            used_at     timestamptz
        );
        CREATE INDEX recovery_code_user ON recovery_code (user_id);

        CREATE TABLE auth_session (
            id               uuid PRIMARY KEY,
            user_id          uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
            family_id        uuid NOT NULL,
            refresh_hash     bytea NOT NULL UNIQUE,
            created_at       timestamptz NOT NULL DEFAULT now(),
            last_used_at     timestamptz NOT NULL DEFAULT now(),
            idle_expires_at  timestamptz NOT NULL,
            expires_at       timestamptz NOT NULL,
            rotated_at       timestamptz,
            revoked_at       timestamptz,
            ip               text,
            user_agent       text
        );
        CREATE INDEX auth_session_family ON auth_session (family_id);
        CREATE INDEX auth_session_user ON auth_session (user_id);

        CREATE TABLE role_type (
            id           uuid PRIMARY KEY,
            key          text NOT NULL UNIQUE,
            name         text NOT NULL,
            permissions  text[] NOT NULL,
            builtin      boolean NOT NULL DEFAULT true
        );

        CREATE TABLE project (
            id             uuid PRIMARY KEY,
            org_id         uuid NOT NULL REFERENCES organization(id),
            name           text NOT NULL,
            description    text NOT NULL DEFAULT '',
            status         text NOT NULL DEFAULT 'active'
                           CHECK (status IN ('active', 'archived', 'deleted')),
            delete_reason  text,
            deleted_at     timestamptz,
            created_at     timestamptz NOT NULL DEFAULT now(),
            created_by     uuid REFERENCES app_user(id)
        );
        CREATE UNIQUE INDEX project_name_key ON project (org_id, lower(name)) WHERE status <> 'deleted';

        CREATE TABLE role_name (
            id            uuid PRIMARY KEY,
            project_id    uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            name          text NOT NULL,
            role_type_id  uuid NOT NULL REFERENCES role_type(id),
            status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
            created_at    timestamptz NOT NULL DEFAULT now(),
            created_by    uuid REFERENCES app_user(id)
        );
        CREATE UNIQUE INDEX role_name_key ON role_name (project_id, lower(name));

        CREATE TABLE membership (
            id            uuid PRIMARY KEY,
            project_id    uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            user_id       uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
            role_name_id  uuid NOT NULL REFERENCES role_name(id),
            created_at    timestamptz NOT NULL DEFAULT now(),
            created_by    uuid REFERENCES app_user(id),
            UNIQUE (project_id, user_id)
        );
        CREATE INDEX membership_user ON membership (user_id);

        CREATE TABLE api_key (
            id            uuid PRIMARY KEY,
            project_id    uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            name          text NOT NULL,
            prefix        text NOT NULL UNIQUE,
            hash          bytea NOT NULL,
            scopes        text[] NOT NULL,
            expires_at    timestamptz,
            last_used_at  timestamptz,
            revoked_at    timestamptz,
            created_at    timestamptz NOT NULL DEFAULT now(),
            created_by    uuid REFERENCES app_user(id)
        );

        CREATE TABLE secret (
            id           uuid PRIMARY KEY,
            project_id   uuid REFERENCES project(id) ON DELETE CASCADE,
            kind         text NOT NULL,
            kek_id       text NOT NULL,
            wrapped_dek  bytea NOT NULL,
            nonce        bytea NOT NULL,
            ciphertext   bytea NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now(),
            rotated_at   timestamptz
        );
        ALTER TABLE app_user
            ADD CONSTRAINT app_user_totp_secret_fk FOREIGN KEY (totp_secret_id) REFERENCES secret(id);

        CREATE TABLE setting (
            id          uuid PRIMARY KEY,
            key         text NOT NULL,
            project_id  uuid REFERENCES project(id) ON DELETE CASCADE,
            value       jsonb NOT NULL,
            version     int NOT NULL,
            updated_by  uuid REFERENCES app_user(id),
            updated_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX setting_key ON setting (key, coalesce(project_id, '00000000-0000-0000-0000-000000000000'));

        CREATE TABLE setting_history (
            id          uuid PRIMARY KEY,
            key         text NOT NULL,
            project_id  uuid REFERENCES project(id) ON DELETE CASCADE,
            value       jsonb NOT NULL,
            version     int NOT NULL,
            updated_by  uuid REFERENCES app_user(id),
            updated_at  timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE audit_event (
            id           uuid PRIMARY KEY,
            project_id   uuid,
            actor_id     uuid,
            actor_label  text NOT NULL,
            action       text NOT NULL,
            target_type  text NOT NULL,
            target_id    text,
            diff         jsonb NOT NULL DEFAULT '{}',
            ip           text,
            user_agent   text,
            created_at   timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX audit_event_time ON audit_event (created_at DESC);
        CREATE INDEX audit_event_project_time ON audit_event (project_id, created_at DESC);
        CREATE INDEX audit_event_actor ON audit_event (actor_id, created_at DESC);
        """
    )

    # Tenant isolation (ADR-002).
    for table in ("role_name", "api_key"):
        run(enable_project_rls(table))
    for table in ("secret", "setting", "setting_history"):
        run(enable_project_rls(table, nullable_project=True))
    run(
        """
        ALTER TABLE membership ENABLE ROW LEVEL SECURITY;
        ALTER TABLE membership FORCE ROW LEVEL SECURITY;
        -- A user may list their own memberships across projects (project switcher).
        CREATE POLICY membership_read ON membership FOR SELECT
            USING (project_id = app_project_id() OR user_id = app_user_id());
        CREATE POLICY membership_write ON membership FOR ALL
            USING (project_id = app_project_id()) WITH CHECK (project_id = app_project_id());

        ALTER TABLE audit_event ENABLE ROW LEVEL SECURITY;
        ALTER TABLE audit_event FORCE ROW LEVEL SECURITY;
        CREATE POLICY audit_event_read ON audit_event FOR SELECT
            USING (project_id = app_project_id());
        CREATE POLICY audit_event_insert ON audit_event FOR INSERT
            WITH CHECK (project_id IS NULL OR project_id = app_project_id());
        -- Append-only: no UPDATE/DELETE for the application roles.
        REVOKE UPDATE, DELETE, TRUNCATE ON audit_event FROM forge_app, forge_admin;
        """
    )

    # API key lookup happens before the project is known: resolve by prefix with BYPASSRLS.
    run(
        """
        CREATE FUNCTION resolve_api_key(p_prefix text)
        RETURNS TABLE (id uuid, project_id uuid, hash bytea, scopes text[],
                       expires_at timestamptz, revoked_at timestamptz, created_by uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
        $$ SELECT id, project_id, hash, scopes, expires_at, revoked_at, created_by
           FROM api_key WHERE prefix = p_prefix $$;
        -- The new owner needs CREATE on the schema only for the ownership change.
        GRANT CREATE ON SCHEMA public TO forge_admin;
        ALTER FUNCTION resolve_api_key(text) OWNER TO forge_admin;
        REVOKE CREATE ON SCHEMA public FROM forge_admin;
        REVOKE ALL ON FUNCTION resolve_api_key(text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION resolve_api_key(text) TO forge_app;
        """
    )

    op.execute(f"INSERT INTO organization (id, name) VALUES ('{ORG_ID}', 'Default organization')")
    for i, (key, (name, perms)) in enumerate(ROLE_TYPES.items(), start=1):
        array = "ARRAY[" + ",".join(f"'{p}'" for p in perms) + "]::text[]"
        op.execute(
            f"INSERT INTO role_type (id, key, name, permissions) "
            f"VALUES ('00000000-0000-7000-8000-0000000001{i:02d}', '{key}', '{name}', {array})"
        )


def downgrade() -> None:
    run(
        """
        DROP FUNCTION IF EXISTS resolve_api_key(text);
        DROP TABLE IF EXISTS audit_event, setting_history, setting, api_key, membership,
            role_name, project, role_type, auth_session, recovery_code, password_history CASCADE;
        ALTER TABLE IF EXISTS app_user DROP CONSTRAINT IF EXISTS app_user_totp_secret_fk;
        DROP TABLE IF EXISTS secret, app_user, organization CASCADE;
        """
    )
