# Repository structure

Use this map to locate ownership before making a change.

| Path | Responsibility |
| --- | --- |
| `src/backend/app.py` | FastAPI routes, public request/response models, HTTP error mapping, source selection |
| `src/backend/config.py` | Validated environment configuration, secrets, provider and PostgreSQL URI resolution |
| `src/backend/db.py` | PostgreSQL connection factory |
| `src/backend/llm.py` | Asynchronous provider-neutral Chat Completions loop, strict local tool execution, answer and citation policy |
| `src/backend/tools.py` | Tool schemas, adapters, citation IDs, and `TOOLS` registry |
| `src/backend/search.py` | ParadeDB BM25 queries and typed prose search results |
| `src/backend/xsd.py` | XSD discovery, parsing, caching, ranked search, exhaustive concept lookup, exact lookup, and source-line evidence |
| `src/frontend/api.py` | Typed HTTP client and frontend copy of backend boundary models |
| `src/frontend/app.py` | Gradio state/events and query concurrency, answer rendering, source cards, XSD viewer, frontend health route |
| `src/frontend/assets/styles.css` | Gradio and XSD viewer presentation |
| `scripts/sync_sources.py` | Official XSD and Confluence ingestion, validation, extraction, and replacement |
| `scripts/export_openapi.py` | Deterministic FastAPI OpenAPI export and freshness check |
| `scripts/run_eval.py` | Production-style question evaluation and report generation |
| `db/init.sql` | Current PostgreSQL bootstrap schema and ParadeDB index |
| `docker-compose.yaml` | Runtime topology, dependency ordering, health checks, ports, volumes, and secrets |
| `Dockerfile.backend` | Backend and source-sync image |
| `Dockerfile.frontend` | Frontend image |
| `Dockerfile.docs` | Static MkDocs build and unprivileged documentation server image |
| `.env.example` | Compose-oriented runtime configuration template |
| `pyproject.toml` | Python requirements, dependency groups, scripts, and tool configuration |
| `tests/` | Unit, boundary, container configuration, synchronization, and optional DB smoke tests |
| `tests/questions.yaml` | Evaluation question set |
| `docs/` | Material for MkDocs source pages |
| `docs/developer/reference/runtime-configuration.md` | Runtime and Compose environment-variable reference |
| `docs/developer/how-to/deploy.md` | Single-host deployment and upgrade procedure |
| `docs/developer/how-to/troubleshoot-operations.md` | Logging, diagnosis, and service recovery procedure |
| `docs/developer/reference/openapi.json` | Generated REST contract rendered by MkDocs |
| `docs/developer/database/` | Authored PostgreSQL table, column, constraint, and index reference |
| `mkdocs.yml` | Documentation theme, navigation, Markdown extensions, and link validation |
| `Makefile` | Documentation build, preview, and strict-check entry points |

## Generated and local-only paths

| Path | Meaning | Commit? |
| --- | --- | --- |
| `.env` | Local runtime values | No |
| `config/secrets/` | Database and model-provider secret files | No |
| `data/xsd/` | Locally synchronized schemas outside Compose | No |
| `data/obdschat_db/` | Bind-mounted PostgreSQL data and logs | No |
| `evaluation-results/` | Evaluation reports and figures | No |
| `site/` | Built documentation site | No |

## Dependency groups

| Group | Installed for |
| --- | --- |
| Base project dependencies | Shared FastAPI/HTTP/Pydantic runtime |
| `backend` | LLM, PostgreSQL, XML, and source parsing |
| `frontend` | Gradio UI |
| `dev` | Tests, static checks, evaluation plotting |
| `docs` | Material for MkDocs |

Backend and frontend container images intentionally install only their own
runtime group. Keep cross-component dependencies at the typed HTTP boundary.
