#!/usr/bin/env bash
# Runs once, on first initialisation of the PostgreSQL data volume (superuser).
# Creates the roles and databases described in ADR-002 and ADR-004.
set -euo pipefail

: "${FORGE_DB_NAME:?}" "${FORGE_DB_USER:?}" "${FORGE_DB_PASSWORD:?}" "${TEMPORAL_DB_PASSWORD:?}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v forge_db="$FORGE_DB_NAME" -v forge_user="$FORGE_DB_USER" \
  -v forge_password="$FORGE_DB_PASSWORD" -v temporal_password="$TEMPORAL_DB_PASSWORD" <<'SQL'
-- Owner: runs migrations, connects for the app and switches to forge_app.
CREATE ROLE :"forge_user" LOGIN PASSWORD :'forge_password' NOBYPASSRLS;
-- Runtime role, bound by row-level security.
CREATE ROLE forge_app NOLOGIN NOBYPASSRLS;
-- Cross-project jobs only (purge, platform dashboards).
CREATE ROLE forge_admin NOLOGIN BYPASSRLS;
GRANT forge_app, forge_admin TO :"forge_user";

CREATE DATABASE :"forge_db" OWNER :"forge_user";

-- Temporal persistence (ADR-004): its schema is applied by the temporal container.
CREATE ROLE temporal LOGIN PASSWORD :'temporal_password';
CREATE DATABASE temporal OWNER temporal;
CREATE DATABASE temporal_visibility OWNER temporal;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$FORGE_DB_NAME" <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_search;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
