# How to change stored source data

This guide covers changes to PostgreSQL-backed guide sections and XSD-backed
schema facts. It assumes you understand [How source data is stored](../explanation/data-storage.md).

## Change the `documents` data model

1. Update the desired bootstrap state in `db/init.sql`.
2. Decide how an existing installation reaches that state. Do not assume
   `CREATE TABLE IF NOT EXISTS` changes existing columns or constraints.
3. Update `Document` in `scripts/sync_sources.py` so external data is validated
   before persistence.
4. Update row construction and the parameterized `INSERT` in
   `replace_documents`.
5. Update result models and fixed SQL in `src/backend/search.py`.
6. Revisit the ParadeDB index definition when adding searchable or filterable
   fields. Choose literal versus stemmed text behavior explicitly.
7. Update `tests/test_sync_sources.py`, `tests/test_search.py`, and
   `tests/test_db_smoke.py`.
8. Run `make docs-check` and review the rendered
   [database reference](../database/index.md).
9. Test the forward migration on a disposable copy of an existing database, then
   test fresh bootstrap separately.

Expected result: new and upgraded databases expose the same schema, synchronized
rows validate before insertion, and search returns typed results.

The repository does not yet provide a migration runner. Add one as part of any
schema change that must work on persisted installations; do not encode an
unreviewed destructive fallback in application startup.

## Change guide extraction

1. Modify focused extraction helpers in `scripts/sync_sources.py`.
2. Preserve the rule that all remote content is fetched and validated before
   writes begin.
3. Keep allowed-host validation on discovery, pagination, details, and redirects.
4. Ensure extraction cannot produce an empty corpus silently.
5. Keep replacement scoped to `source_type = 'umsetzungsleitfaden'`.
6. Add HTML fixtures for headings, tables, ignored nodes, whitespace, malformed
   payloads, and pagination behavior.
7. Run source synchronization against a disposable database before using it with
   retained data.

Expected result: each row remains a useful heading-sized search unit with public
URL and version metadata.

## Change prose ranking

1. Change `_SEARCH_QUERY` in `src/backend/search.py`.
2. Keep all user/model values as SQL parameters.
3. Keep result ordering deterministic after score ordering.
4. Verify title, section, content, generic-version, requested-version, and
   unrelated rows in unit tests.
5. Run the optional DB smoke test against ParadeDB; mocked psycopg tests cannot
   prove index/operator compatibility.

Expected result: ranking behavior is stated in tests and executes with the real
ParadeDB extension.

## Change synchronized XSD handling

1. Keep downloaded schemas grouped by exact version and filename.
2. Validate official host, filename/version agreement, XML syntax, and XSD root
   before writing.
3. Write changes atomically; never expose a partial XSD file to the backend.
4. Update schema discovery and catalog tests if naming rules change.
5. Recheck cache behavior. A running process does not see new catalog contents
   until `clear_schema_cache` runs or the process restarts.
6. Verify exact path lookup, duplicate path detection, declared version, source
   location, and excerpt bounds.

Expected result: each discoverable version has one validated canonical file and
produces a deterministic in-memory index.

## Review checklist

- Fresh bootstrap and forward migration both exist where needed.
- No production data reset is required merely to apply a schema change.
- Writes remain outside request handling.
- Remote content is validated before persistence.
- SQL stays parameterized.
- Search semantics are proven with real ParadeDB.
- Database reference matches the current `db/init.sql`.
