# oBDSChat documentation

oBDSChat answers questions about the German oncological basic data set (oBDS).
It grounds each answer in official oBDS XML schemas and the public
Umsetzungsleitfaden, then exposes the evidence used for the answer.

## Choose your path

- **Using the application:** start with [Get started](user/tutorials/get-started.md),
  then use the [workflow guides](user/how-to/use-obdschat.md).
- **Changing the application:** read [System architecture](developer/explanation/system-architecture.md)
  and [Local development](developer/how-to/local-development.md).
- **Changing backend behavior:** read [Backend architecture](developer/explanation/backend-architecture.md)
  and [Extend the backend](developer/how-to/extend-backend.md).
- **Changing PostgreSQL or synchronized sources:** read [Data storage](developer/explanation/data-storage.md)
  and [Change stored data](developer/how-to/change-stored-data.md), then inspect the
  generated [database reference](developer/database/README.md).
- **Using the REST API:** open the generated [REST API reference](developer/reference/rest-api.md).
- **Operating a deployment:** start with [Runtime configuration](developer/reference/runtime-configuration.md),
  then use [Deploy and upgrade](developer/how-to/deploy.md) and
  [Operational troubleshooting](developer/how-to/troubleshoot-operations.md).

## Documentation scope

This documentation currently covers user workflows, developer-facing
architecture, setup, testing, common change paths, and embedded REST API
reference. The PostgreSQL table reference and Mermaid ER diagram are generated
from the bootstrap schema. Compose deployment, configuration, logging, and
troubleshooting are covered for operators. FastAPI's generated OpenAPI schema
remains the canonical description of the HTTP contract.

## Build these docs

From the repository root:

```bash
make docs
```

The static site is written to `site/`. Docker is required for the generated
database reference. Use `make docs-serve` for live preview and `make docs-check`
for the strict CI-oriented build.
