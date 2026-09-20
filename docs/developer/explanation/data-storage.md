# How source data is stored

oBDSChat uses two read models rather than one general-purpose source store. The
choice follows retrieval behavior: prose needs ranked full-text search, while XSD
needs deterministic structural traversal and exact source locations.

## PostgreSQL and ParadeDB

The `documents` table stores sections extracted from the public
[Umsetzungsleitfaden](https://plattform65c.atlassian.net/wiki/spaces/UMK/overview). One row contains source type, page title, optional heading, section content, public URL, and optional oBDS version. A bigint identity is both
the primary key and the ParadeDB BM25 key field.

`documents_full_text_idx` indexes:

- `source_type` and `obds_version` as literal fields;
- `title`, `section`, and `content` with German stemming;
- `id` as the required key field.

Query-time boosts make title matches stronger than section matches and section
matches stronger than body-only matches. Version filtering retains generic rows
where `obds_version` is null alongside rows for the requested version.

PostgreSQL is supplied by ParadeDB rather than a stock PostgreSQL image because
the application depends on `pg_search`, the `bm25` index access method, `|||`
matching, `pdb.boost`, and `pdb.score`.

## Versioned XSD files

Downloaded files use this layout:

```text
data/xsd/
└── 3.x.y/
    └── oBDS_v3.x.y.xsd
```

Compose stores them in the named `xsd-data` volume. The synchronizer mounts it
read-write; the backend mounts it read-only. Outside Compose, the default is
`data/xsd` below the working directory.

## Current source evidence

`sources` keeps one current original per source family, origin, native kind, and
native ID. Titles and URLs can change without changing source identity. Original
storage HTML (UTF-8) and PDF bytes are stored once per source, with a
database-checked SHA-256 hash. Source revision labels and dates remain metadata.
Identical bytes from different sources keep separate provenance.

`evidence_blocks` stores current parsed text, structural HTML or physical PDF
locators, and structured payloads. `backend.evidence.replace_source` replaces
original, metadata, and all blocks atomically. Old originals and extraction
results are removed; each replacement assigns fresh evidence UUIDs.
`get_current_source` returns a consistent current original/evidence view.
Old chats may keep displayed excerpts and public links, but those links open the
current website. There is no historical-original lookup. Evaluation fixtures
remain separate from runtime storage.

The caller owns the outer transaction, allowing future refresh to group validated
candidate replacements and withdrawn-source deletions before commit. Deleting a
source cascades to its evidence. Full staging, completeness validation,
publication, and readiness orchestration belong to step four.

Existing synchronization still fills `documents`; it does not fabricate original
HTML or revision metadata. No Manual Plus corpus is added to active search.
Registry assessments and qualifications follow in step two. Deterministic XSD
storage and public query behavior remain unchanged.

## Fresh bootstrap

`db/init.sql` is bootstrap SQL. The database container runs it only when
initializing an empty PostgreSQL data directory. Source synchronization also
executes it before replacing documents, but `CREATE TABLE IF NOT EXISTS` and
`CREATE INDEX IF NOT EXISTS` do not alter an existing object.

Editing a column or index definition therefore does not migrate an existing
database. For Manual Plus step one, the owner explicitly chose a fresh rebuild:
the current development database is disposable and has no production users.
There is no migration runner, schema history, or legacy-data backfill. The entire
schema is defined in `db/init.sql` and can be initialized repeatedly on the same
schema version. This is not an upgrade mechanism for future populated deployments.

The [database reference](../database/index.md) summarizes the current table,
column, constraint, and index definitions. `db/init.sql` remains canonical; keep
the reference aligned and run `make docs-check` after changing the bootstrap
definition.

## Synchronization consistency

The synchronizer downloads and validates every remote input before changing
local stores. Schema files are replaced one at a time, so readers never see a
partially written file. Guide rows are deleted and inserted inside one psycopg connection transaction; exceptions roll back the replacement.

PostgreSQL row IDs can change after synchronization because guide rows are
recreated. Treat them as request-local citation identifiers, not durable external
IDs. Stable source identity is carried by source type, URL, version, section, and
XML path where applicable.

## Runtime ownership

Only source synchronization writes application source data. Backend request paths
read PostgreSQL and XSD files. The frontend accesses neither store. This ownership
keeps writes out of user requests and makes source refresh failure independent of
an already-running backend until deployment chooses to restart or refresh it.
