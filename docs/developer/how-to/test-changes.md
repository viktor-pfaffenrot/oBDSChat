# How to test changes

Run checks from the repository root. Install development dependencies first:

```bash
uv sync --group backend --group frontend --group dev
```

## Run the required suite

```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
```

Expected result: all four commands exit successfully. Without
`TEST_DATABASE_URL`, the optional database smoke test is skipped.

## Run focused tests while developing

Choose the closest boundary first:

| Change | Focused test |
| --- | --- |
| FastAPI route or response shaping | `uv run pytest tests/test_app.py` |
| Settings or secrets | `uv run pytest tests/test_config.py` |
| Compose/Dockerfile contract | `uv run pytest tests/test_container_config.py` |
| LLM loop or citation policy | `uv run pytest tests/test_llm.py` |
| Prose SQL and ranking | `uv run pytest tests/test_search.py` |
| Local tool schemas/adapters | `uv run pytest tests/test_tools.py` |
| XSD parsing and lookup | `uv run pytest tests/test_xsd.py` |
| Frontend HTTP client | `uv run pytest tests/test_frontend_api.py` |
| Gradio state/rendering/viewer | `uv run pytest tests/test_frontend_app.py` |
| Source synchronization | `uv run pytest tests/test_sync_sources.py` |

After focused tests pass, run the complete required suite.

## Test real ParadeDB behavior

The smoke test requires a disposable ParadeDB/PostgreSQL database with
`pg_search`; a stock PostgreSQL server is insufficient.

```bash
TEST_DATABASE_URL=postgresql://user:password@host:port/database \
uv run pytest -m db_smoke
```

The test creates an isolated schema, verifies German BM25 search, checks version
filtering and exact content lookup, confirms the ParadeDB index definition, then
rolls back. Never point it at a database where the supplied role cannot safely
create temporary schemas.

Expected result: the marker test passes instead of skipping.

## Check documentation

```bash
make docs-check
```

This first verifies that committed OpenAPI JSON matches the FastAPI application,
then performs a clean strict Material for MkDocs build. A stale schema or
warnings—including broken internal links, invalid anchors, omitted documentation
files, and unrecognized links—fail the command.

## Review after tests

Classify findings before handoff:

- **Must fix:** failing required check, contract mismatch, unhandled expected
  error, unsafe SQL/source handling, missing forward schema path, or stale docs.
- **Optional:** non-blocking cleanup, performance measurement, broader test data,
  or a future design improvement that does not affect current correctness.
