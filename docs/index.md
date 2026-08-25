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
  and [Change stored data](developer/how-to/change-stored-data.md).
- **Using the REST API:** open the generated [REST API reference](developer/reference/rest-api.md).

## Documentation scope

This documentation currently covers user workflows, developer-facing
architecture, setup, testing, common change paths, and embedded REST API
reference. Generated database reference and operations runbooks are planned
separately. FastAPI's generated OpenAPI schema remains the canonical description
of the HTTP contract.

## Build these docs

From the repository root:

```bash
make docs
```

The static site is written to `site/`. Use `make docs-serve` for live preview and
`make docs-check` for the strict CI-oriented build.
