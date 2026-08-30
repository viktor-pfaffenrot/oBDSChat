# How to run oBDSChat locally

This guide starts a complete development stack and shows an optional split-process
workflow. It assumes familiarity with Python, Docker Compose, and environment
variables.

## Prerequisites

- Git
- Docker with Compose
- Python 3.13.9 or newer
- `uv`
- `make` for documentation commands
- an OpenAI or Requesty API key
- outbound HTTPS access to the selected model provider and official oBDS sources

## Run the complete Compose stack

1. Create local configuration:

   ```bash
   cp .env.example .env
   mkdir -p config/secrets
   ```

2. Open `config/secrets/obdschat_db_password.txt` in an editor and add one strong
   database password with no surrounding quotes.

3. Open `config/secrets/llm_api_key.txt` and add the key for the provider selected
   by `LLM_PROVIDER` in `.env`.

4. Review `.env` against the
   [runtime configuration reference](../reference/runtime-configuration.md).
   Keep container-internal values such as `OBDSCHAT_DB_HOST=db` and
   `/run/secrets/...` file paths for Compose.

5. Build and start the stack:

   ```bash
   docker compose up --build
   ```

   On a fresh or recreated source-sync container, startup downloads official oBDS
   3.x schemas and the public Umsetzungsleitfaden before starting the backend.

6. In another terminal, verify services:

   ```bash
   docker compose ps
   curl -fsS http://localhost:18000/health
   curl -fsS http://localhost:17860/health
   curl -fsS http://localhost:8000/
   ```

7. Open <http://localhost:17860>. The documentation site is at
   <http://localhost:8000>, and FastAPI's interactive backend schema is at
   <http://localhost:18000/docs>.

Expected result: database, backend, frontend, and documentation service report
healthy; source-sync has exited successfully; the browser can complete a
grounded question.

## Rebuild after code changes

Container images copy source code at build time. Rebuild the affected image:

```bash
docker compose up -d --build backend
docker compose up -d --build frontend
```

Changes to shared packaging or backend code used by `source-sync` may require its
container to be recreated and completed before rebuilding the backend.

## Run Python processes outside Compose

Use this mode for reload-driven development while keeping ParadeDB in Compose.

1. Install all development groups:

   ```bash
   uv sync --group backend --group frontend --group dev
   ```

2. Start only the database:

   ```bash
   docker compose up -d obdschat-db
   ```

3. Synchronize sources using host-reachable settings:

   ```bash
   OBDSCHAT_BASE_DIR=. \
   OBDSCHAT_DB_HOST=localhost \
   OBDSCHAT_DB_PORT=55434 \
   OBDSCHAT_DB_PASSWORD_FILE=config/secrets/obdschat_db_password.txt \
   uv run obdschat-sync-sources
   ```

4. Start the backend with the same database overrides and a host-readable model
   key:

   ```bash
   OBDSCHAT_BASE_DIR=. \
   OBDSCHAT_DB_HOST=localhost \
   OBDSCHAT_DB_PORT=55434 \
   OBDSCHAT_DB_PASSWORD_FILE=config/secrets/obdschat_db_password.txt \
   LLM_API_KEY_FILE=config/secrets/llm_api_key.txt \
   uv run uvicorn backend.app:app --reload --port 9000
   ```

5. Start the frontend in another terminal:

   ```bash
   BACKEND_URL=http://localhost:9000 \
   uv run uvicorn frontend.app:app --reload --port 7860
   ```

6. Open <http://localhost:7860>.

Process environment variables override `.env`, which is why these commands can
reuse the Compose-oriented file without changing container paths.

## Preview documentation

```bash
make docs-serve
```

The command exports OpenAPI and starts MkDocs. Open <http://127.0.0.1:8000>.
