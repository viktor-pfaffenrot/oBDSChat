# How to diagnose an oBDSChat deployment

This guide diagnoses the supplied Docker Compose deployment. It assumes shell
access to the deployment host and permission to use Docker. For browser-only
problems, start with [How to troubleshoot user problems](../../user/how-to/troubleshoot.md).

## Capture current state

Before restarting anything, capture service state and recent logs:

```bash
date -Is
docker compose ps -a
docker compose logs --since=15m --timestamps obdschat-db source-sync backend frontend docs
docker compose config --quiet
df -h
```

Record the deployed revision with `git rev-parse HEAD`. Redact credentials,
provider payloads, user questions, and sensitive filesystem paths before sharing
diagnostics.

## Understand log sources

| Service | Primary diagnostic output | Additional location |
| --- | --- | --- |
| `obdschat-db` | `docker compose logs obdschat-db` | Host path configured by `OBDSCHAT_DB_LOG_DIR` when PostgreSQL writes file logs |
| `source-sync` | `docker compose logs source-sync` | None; completion and failure are written to standard output/error |
| `backend` | `docker compose logs backend` | Uvicorn access, startup, and application errors on standard output/error |
| `frontend` | `docker compose logs frontend` | Uvicorn access, startup, and application errors on standard output/error |
| `docs` | `docker compose logs docs` | Nginx access and server errors on standard output/error |

Follow one service while reproducing a problem:

```bash
docker compose logs -f --tail=100 backend
```

The Compose file does not configure a logging driver or retention limit. The
host Docker configuration therefore controls container-log storage and
rotation. Establish host-level retention before logs can exhaust disk space.

## Check the startup chain

The required order is database, source synchronization, backend, then frontend.
Start with the earliest unhealthy or failed service.

| State | Likely area | First checks |
| --- | --- | --- |
| `obdschat-db` unhealthy or restarting | PostgreSQL startup, data-directory permissions, disk space, password/user/database settings, ParadeDB extension | Database logs, `df -h`, bind-path ownership, matching PostgreSQL and application variables |
| `source-sync` exits non-zero | Database access, official-source network access, changed upstream content, XSD validation | Synchronizer logs, database health, outbound DNS/HTTPS |
| Backend is not created or remains pending | Synchronizer has not completed successfully | `docker compose ps -a` and synchronizer exit status |
| Backend unhealthy or restarting | Startup/import failure or unreadable XSD/configuration | Backend logs, mounted XSD volume, resolved Compose configuration |
| Frontend unhealthy or restarting | Frontend startup failure | Frontend logs and backend health |
| Health routes pass but questions fail | Health routes test liveness only; database, XSD, or model provider may still fail per request | Backend logs during one request, provider selection/key, database connectivity, synchronized sources |
| Health routes pass but questions wait before backend access | Frontend query concurrency is saturated | `OBDSCHAT_QUERY_CONCURRENCY`, frontend access logs, provider latency, active user load |

Check PostgreSQL readiness inside its container:

```bash
docker compose exec obdschat-db sh -lc 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Check application liveness from the host:

```bash
curl -v http://localhost:18000/health
curl -v http://localhost:17860/health
curl -v http://localhost:8000/
```

`OBDSCHAT_QUERY_CONCURRENCY` limits simultaneous query-completion events in each
frontend process and defaults to eight. Increase it only after checking model
provider capacity, database load, and backend latency. The setting does not limit
clients that call the backend directly.

## Retry failed source synchronization

Use this after correcting database, credential, network, or upstream-input
problems:

```bash
docker compose up -d --force-recreate source-sync
docker compose logs -f --tail=100 source-sync
docker compose up -d backend frontend
```

Confirm that the recreated `source-sync` container exits with status zero before
starting dependent services.

For a deliberate source refresh on an otherwise running deployment:

```bash
docker compose run --rm source-sync
docker compose restart backend
```

The backend restart clears its in-process XSD catalog cache so newly synchronized
schemas become visible.

## Apply the right recovery action

- Use `docker compose restart <service>` only for a transient process problem
  when configuration and images are unchanged.
- Use `docker compose up -d --build <service>` after source-code or image changes.
- Recreate `source-sync` after fixing a failed initial synchronization.
- Do not use `docker compose down -v` during routine diagnosis; it removes named
  volumes, including synchronized XSD data.
- Do not delete or replace the PostgreSQL data path as a troubleshooting shortcut.

## Escalation information

Provide these facts when the documented checks do not isolate the problem:

- deployment revision and approximate failure time;
- `docker compose ps -a` output;
- relevant timestamped service logs with sensitive values removed;
- failing health route or user action;
- whether the problem began after code, configuration, credential, source, or
  host changes;
- available disk space and the earliest failed service in the startup chain.

Expected result: the earliest failing component is identified, the evidence is
safe to share, and recovery uses the narrowest action appropriate to the change.
