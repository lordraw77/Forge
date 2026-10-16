"""SQL helpers for migrations that create project-scoped tables (ADR-002)."""

from __future__ import annotations

import re

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def enable_project_rls(table: str, *, nullable_project: bool = False) -> str:
    """Return SQL that enables and forces RLS on `table` with the tenant policy.

    `nullable_project=True` is for dual-scope tables, where `project_id IS NULL` rows are
    platform-wide: readable by every project, writable only through `forge_admin`.
    """
    if not _IDENT.match(table):
        raise ValueError(f"invalid table name: {table!r}")
    read = "project_id = app_project_id()"
    if nullable_project:
        read = f"(project_id IS NULL OR {read})"
    return f"""
        ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
        CREATE POLICY {table}_tenant_read ON {table} FOR SELECT USING ({read});
        CREATE POLICY {table}_tenant_write ON {table} FOR ALL
            USING (project_id = app_project_id())
            WITH CHECK (project_id = app_project_id());
    """
