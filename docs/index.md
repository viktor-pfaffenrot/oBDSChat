# oBDSChat documentation

oBDSChat answers questions about the German oncological basic data set (oBDS).
It grounds each answer in official oBDS XML schemas and the public
[Umsetzungsleitfaden](https://plattform65c.atlassian.net/wiki/spaces/UMK/overview), then shows the evidence used for the answer.

## Choose your path

- **Using the application:** Checkout the [workflow guides](user/how-to/use-obdschat.md).
- **Changing the application:** read [System architecture](developer/explanation/system-architecture.md)
  and the [architecture decision records](developer/explanation/ADR.md) before
  using [Local development](developer/how-to/local-development.md).
- **Changing backend behavior:** read [Backend architecture](developer/explanation/backend-architecture.md)
  and [Extend the backend](developer/how-to/extend-backend.md).
- **Changing PostgreSQL or synchronized sources:** read [Data storage](developer/explanation/data-storage.md)
  and [Change stored data](developer/how-to/change-stored-data.md), then inspect the
  [database reference](developer/database/index.md).
- **Using the REST API:** open the generated [REST API reference](developer/reference/rest-api.md).
- **Operating a deployment:** start with [Runtime configuration](developer/reference/runtime-configuration.md),
  then use [Deploy and upgrade](developer/how-to/deploy.md) and
  [Operational troubleshooting](developer/how-to/troubleshoot-operations.md).

## Build these docs

From the repository root:

```bash
make docs
```

The static site is written to `site/`. Use `make docs-check` for the strict CI-oriented build.
