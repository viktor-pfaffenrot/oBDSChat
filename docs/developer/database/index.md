# PostgreSQL schema reference

`db/init.sql` is the canonical definition of application-owned PostgreSQL
objects. This reference summarizes its tables, constraints, and search
index for quick lookup.

For storage rationale and synchronization behavior, read
[Data Storage](../explanation/data-storage.md).

## `public.documents`

One row represents one searchable section extracted from official oBDS
documentation.

### Columns

| Column | Type | Nullable | Default | Description |
| --- | --- | --- | --- | --- |
| `id` | `bigint` | No | Sequence-backed `BIGSERIAL` | Request-local row identifier, primary key, and ParadeDB BM25 key field. IDs can change when sources are synchronized. |
| `source_type` | `text` | No | None | Source family used to scope synchronization and search. The current persisted value is `umsetzungsleitfaden`. |
| `title` | `text` | No | None | Title of the source page or document. Title matches receive the strongest query-time boost. |
| `section` | `text` | Yes | `NULL` | Optional heading identifying the extracted section. Section matches rank below title matches and above body-only matches. |
| `content` | `text` | No | None | Plain-text section content searched when producing grounded answers. |
| `url` | `text` | No | None | Public URL of the canonical source page. |
| `obds_version` | `text` | Yes | `NULL` | Optional oBDS version. `NULL` marks version-independent content that remains eligible during version-filtered search. |

### Constraints

- `documents_pkey` enforces uniqueness on `id`.
- `id`, `source_type`, `title`, `content`, and `url` are required.
- `section` and `obds_version` are optional.

### Full-text index

`documents_full_text_idx` is a ParadeDB BM25 index supplied by the `pg_search`
extension.

| Field | Index treatment | Search role |
| --- | --- | --- |
| `id` | Key field | Identifies the row returned by a search result. |
| `source_type` | Literal | Supports exact source-family filtering. |
| `title` | German-stemmed text | Highest query-time text boost. |
| `section` | German-stemmed text | Medium query-time text boost. |
| `content` | German-stemmed text | Base full-text match. |
| `obds_version` | Literal | Supports exact version filtering. |

Change `db/init.sql` first, keep this reference aligned, account for existing
installations as described in
[How to change stored source data](../how-to/change-stored-data.md), then run
`make docs-check`.

## Current evidence tables

The guide synchronizer still uses `documents`. These tables provide persistence
for future ingestion; bootstrap does not publish a Manual Plus corpus.

| Table | Contents | Constraints |
| --- | --- | --- |
| `sources` | UUID; source family, origin, native kind/ID; current original bytes and SHA-256; media type, title, URL, source revision label, observation/modification timestamps, edition, decision/applicability dates, JSON metadata | Unique native key; supported family/kind/media values; hash checked against bytes; metadata object. |
| `evidence_blocks` | UUID; source ID; extraction version, local key, kind, text, locator, structured payload | Source foreign key with cascading deletion; unique source/local key; JSON objects. |

`backend.evidence.SourceEvidence` validates HTML/PDF locators and unique block keys.
`replace_source` replaces current bytes, metadata, and blocks in one transaction,
keeping the source UUID. Evidence UUIDs are newly assigned on every replacement;
expired IDs cannot resolve to replacement text. `get_current_source` reads bytes
and blocks in one statement. Source UUIDs identify mutable sources, not citations.

No revision/archive/snapshot-history tables or immutability triggers are needed.
Extraction versions describe current processing, without retaining older results.
