# How source data is stored

oBDSChat uses two read models rather than one general-purpose source store. The
choice follows retrieval behavior: prose needs ranked full-text search, while XSD
needs deterministic structural traversal and exact source locations.

## PostgreSQL and ParadeDB

The `documents` table stores sections extracted from the public
Umsetzungsleitfaden. One row contains source type, page title, optional heading,
section content, public URL, and optional oBDS version. A bigint identity is both
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

The backend verifies that a directory version matches the schema's declared
version. It derives element facts and source locations at runtime, avoiding a
second persisted representation that could drift from the official file.

## Bootstrap versus migration

`db/init.sql` is bootstrap SQL. The database container runs it only when
initializing an empty PostgreSQL data directory. Source synchronization also
executes it before replacing documents, but `CREATE TABLE IF NOT EXISTS` and
`CREATE INDEX IF NOT EXISTS` do not alter an existing object.

Therefore, editing a column or index definition in `db/init.sql` does not migrate
an existing database. The repository currently has no migration framework or
schema-version table. Any schema-changing feature must define both:

- desired bootstrap state for new databases;
- an explicit forward migration for existing databases.

Generated table/column documentation and ER diagrams are intentionally deferred
to the database-reference phase. Until then, `db/init.sql` is the canonical
bootstrap definition and this page explains its role rather than duplicating a
full generated schema reference.

## Synchronization consistency

The synchronizer downloads and validates every remote input before changing
local stores. Schema files are atomically replaced one file at a time. Guide rows
are deleted and inserted inside one psycopg connection transaction; exceptions
roll back the replacement.

PostgreSQL row IDs can change after synchronization because guide rows are
recreated. Treat them as request-local citation identifiers, not durable external
IDs. Stable source identity is carried by source type, URL, version, section, and
XML path where applicable.

## Runtime ownership

Only source synchronization writes application source data. Backend request paths
read PostgreSQL and XSD files. The frontend accesses neither store. This ownership
keeps writes out of user requests and makes source refresh failure independent of
an already-running backend until deployment chooses to restart or refresh it.
