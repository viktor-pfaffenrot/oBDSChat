# oBDSChat

Local, source-grounded chat for the German oBDS.

## Source synchronization

Copy `.env.example` to `.env`, set the database values, and place the password
in `config/secrets/obdschat_db_password.txt`. Then run:

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
