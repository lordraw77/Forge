# ADR-018 – Export/import bundles: a versioned JSON format

- Status: Accepted (PO request, 2026-10-19; format to be confirmed)
- Date: 2026-10-19

## Context

The PO asked to export and import configurations and pieces of a project, with support for multiple versions. Typical uses: move a prompt or a RAG pipeline from a test project to production, share assets between installations, back up configuration, and keep it in Git. A bundle is a contract: a file exported today must import into later Forge versions.

## Decision

- **One JSON document**, `format: "forge.bundle"`, with an integer `schema_version`, `forge_version`, `exported_at`, `source.project` and a list of `items`.
- **Items** (each has `kind` and `name`):

  | Kind | Content | Notes |
  | --- | --- | --- |
  | `prompt` | Versions chosen at export (all, published, latest) with their fields, and the `staging`/`prod` tags | |
  | `rag_pipeline` | Versions with their configuration, tags | Collection and prompt are referenced **by name** |
  | `data_collection` | Name and description | Definition only: **files, chunks and embeddings are never exported** |
  | `model_alias` | Project aliases as lists of model names | Skipped when a model is missing here |
  | `gateway_config` | Project limits and cache settings | |
  | `user_group` | Name and description | No members |

  Agents, tools and use cases join the same format when they exist (E1.18, E2.x); a bundle with kinds this Forge does not know imports the rest and reports the unknown ones as skipped.
- **Nothing sensitive.** No secrets, API keys, tokens, documents, run history or user data. Provider credentials and model definitions are installation configuration and are not part of a project bundle.
- **References by name, not by id.** Ids differ between installations. Import resolves names in the target project and reports what is missing, instead of creating broken references.
- **Versions.**
  - *Object versions:* every version is exported and imported as a new immutable version; the original number goes in the version note. Tags `staging`/`prod` follow their versions only for items created by the import; they are never moved on an existing item (publishing stays a deliberate act).
  - *Format versions:* `schema_version` is migrated forward on import by chained functions (`MIGRATIONS[n]` upgrades n → n+1). A bundle from a **newer** schema is refused with an upgrade message. Unknown fields are ignored (forward tolerance).
- **Conflicts**, chosen per import: `new_version` (append to the existing item), `rename` (import as "<name> (imported)"), `skip`.
- **Dry run first.** Import defaults to a dry run that returns the report (created, new versions, renamed, reused, updated, skipped, forbidden, error, with reasons). The UI always shows the preview before applying.
- **Permissions.** Prompts, pipelines and collections: `asset.write`. Aliases, limits and groups: also `project.manage`. Every import is audited with its source.
- **API.** `POST /api/v1/projects/{id}/export` (download) and `POST /api/v1/projects/{id}/import`. Bundles are limited to 20 MB.

## Consequences

- Removing or renaming a field of an item needs a migration step and a new `schema_version`; additive changes do not.
- `tests/unit/test_bundles.py` checks that every supported version has a migration.
- Project-to-project moves inside one installation and installation-to-installation moves use the same path, so there is one thing to test and document.
- Git-based GitOps (E3.x, SDK/CLI) can build on the same format.
