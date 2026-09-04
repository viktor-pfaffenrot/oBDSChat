# How to change stored source data

This guide covers changes to PostgreSQL-backed sections of the Umsetzungsleitfaden and XSD-backed schema facts. Check the section on ["Data storage"](../explanation/data-storage.md) for background on how the data are stored.

## Change the `documents` data model

1. Update the desired bootstrap state in `db/init.sql`.
2. Decide how an existing installation reaches that state. Do not assume
   `CREATE TABLE IF NOT EXISTS` changes existing columns or constraints.
3. Update the `Document` pydantic data model in `scripts/sync_sources.py` so external data is validated before persistence.
4. Update row construction and the parameterized `INSERT` in
   `replace_documents`.
5. Update result models and fixed SQL in `src/backend/search.py`.
6. Revisit the ParadeDB index definition when adding searchable or filterable
   fields. Choose literal versus stemmed text behavior explicitly.
7. Update `tests/test_sync_sources.py`, `tests/test_search.py`, and
   `tests/test_db_smoke.py`.
8. Test the forward migration on a disposable copy of an existing database, then
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
3. Reject any source URL outside the approved hosts. Apply this check to the
   initial discovery request, every pagination and page-detail URL, and the final
   destination of every redirect.
4. Ensure extraction cannot produce an empty corpus silently.
5. Keep replacement scoped to `source_type = 'umsetzungsleitfaden'`.
6. Add test fixtures for HTML extraction cases, including headings, tables, ignored elements, and whitespace. Also test malformed API responses and pagination behavior.
7. Run source synchronization against a disposable database before using it with
   retained data.

Expected result: each row remains a useful heading-sized search unit with public
URL and version metadata.

## Change prose ranking

1. Change `_SEARCH_QUERY` in `src/backend/search.py`.
2. Keep all user/model values as SQL parameters. Prevents SQL injection and quoting bugs.
3. Keep result ordering deterministic after score ordering. (`ORDER BY score DESC, id`)
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
3. Write complete download to temporary file, then replace destination in one operation. Backend sees either old complete file or new complete file, never half-written file.
4. If the version-directory or XSD filename convention changes, update both the
   synchronization discovery logic and the backend catalog discovery logic. Add
   or update tests for both components.
5. After adding or replacing synchronized XSD files, call `clear_schema_cache()`
   in each running backend process or restart it. Otherwise, that process keeps
   using the catalog and parsed schema indexes it previously cached.
6. Verify exact path lookup, duplicate path detection, declared version, source
   location, and excerpt bounds.

Expected result: each discoverable version has one validated canonical file and
produces a deterministic in-memory index.

## Review checklist

- Fresh bootstrap and forward migration both exist where needed.
- Upgrade must preserve stored database. Operators should not need to delete PostgreSQL volume and rebuild everything.
- Backend requests should only read source data. Synchronization process owns downloads, XSD updates, and guide-row replacement. User request must not trigger data mutation.
- Remote content is validated before persistence. Bad download must not replace valid data.
- SQL stays parameterized.
- Database reference in documentation matches the current `db/init.sql`.
