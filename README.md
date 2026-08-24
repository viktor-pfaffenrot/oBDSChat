# oBDSChat

Local, source-grounded chat for the German oBDS.

## Docker Compose

Copy `.env.example` to `.env`, configure one model provider, and write the
database and selected provider keys to these gitignored files:

```text
config/secrets/obdschat_db_password.txt
config/secrets/llm_api_key.txt
```

Then start the stack:

```bash
docker compose up --build
```

Compose starts ParadeDB, synchronizes official sources once, then starts the
backend and frontend. The UI is available at `http://localhost:17860`; the
backend API is available at `http://localhost:18000`.

## Model provider

Set `LLM_PROVIDER` to `openai` or `requesty` and place its key in the configured
`LLM_API_KEY_SOURCE` file. Requesty uses the stable `policy/obdschat` route;
concrete models and routing behavior stay in Requesty. Direct OpenAI testing
uses `OPENAI_MODEL`. Both providers use the OpenAI-compatible Chat Completions
API at `/v1/chat/completions`.

## Source synchronization

For local development outside Compose, set the database values and run:

```bash
uv run obdschat-sync-sources
```

The command downloads all official oBDS 3.x schemas into `data/xsd/<version>/`
and atomically replaces Umsetzungsleitfaden sections in PostgreSQL.
`--database-url` remains available as an explicit override.

Run the optional database smoke test against a disposable PostgreSQL database:

```bash
TEST_DATABASE_URL=postgresql://... uv run pytest -m db_smoke
```

## Evaluation

With production configuration, PostgreSQL, and source data available, run:

```bash
uv run python -m scripts.run_eval
```

Detailed results and a PNG summary are written to `evaluation-results/`. In a
notebook, `run_production_evaluation()` returns the report and matplotlib figure
for inline inspection. Use `--no-show` in headless environments.
