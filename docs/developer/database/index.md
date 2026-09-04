# PostgreSQL schema reference

`db/init.sql` is the canonical definition of application-owned PostgreSQL
objects. This reference summarizes its table, columns, constraints, and search
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
