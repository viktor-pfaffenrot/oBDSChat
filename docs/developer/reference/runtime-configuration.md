# Runtime configuration

This reference lists configuration read by the application and the supplied
Docker Compose deployment. Values in `.env.example` are Compose-oriented
examples, not universal application defaults.

## Loading and precedence

The backend reads process environment variables first and `.env` as a fallback.
Empty values are ignored. Unknown variables do not change backend settings.

For model credentials, `LLM_API_KEY_FILE` takes precedence over the selected
provider's direct key. For the database, a non-empty `OBDSCHAT_DB_PASSWORD`
takes precedence over `OBDSCHAT_DB_PASSWORD_FILE`.

Compose uses `.env` twice: for `${...}` interpolation in `docker-compose.yaml`
and as `env_file` input for the database, source synchronizer, and backend.
The frontend receives the explicit `BACKEND_URL` and
`OBDSCHAT_QUERY_CONCURRENCY` values declared by Compose.

## Model provider

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `LLM_PROVIDER` | `openai` | No | Selected provider: `openai` or `requesty`. The example selects `requesty`. |
| `OPENAI_MODEL` | `gpt-5.6-luna` | For direct OpenAI only | Model route used when `LLM_PROVIDER=openai`. |
| `LLM_API_KEY_FILE` | Unset | Preferred | Backend-readable file containing the selected provider key. Compose uses `/run/secrets/llm_api_key`. |
| `OPENAI_API_KEY` | Unset | Alternative | Direct OpenAI key used only when no key file is configured. |
| `REQUESTY_API_KEY` | Unset | Alternative | Direct Requesty key used only when no key file is configured. Requesty always uses `policy/obdschat`. |
| `LLM_API_KEY_SOURCE` | `./config/secrets/llm_api_key.txt` | Compose only | Host file mounted as the `llm_api_key` Compose secret. |

Changing `OPENAI_MODEL` has no effect when Requesty is selected. Concrete
Requesty routing belongs to the provider-side `policy/obdschat` policy.

## Application paths and PostgreSQL connection

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `OBDSCHAT_BASE_DIR` | Current working directory | No | Base for backend XSD data and relative secret paths. Compose uses `/app`. |
| `OBDSCHAT_DB_HOST` | `localhost` | No | PostgreSQL hostname. Compose uses the `db` network alias. |
| `OBDSCHAT_DB_PORT` | `5432` | No | PostgreSQL target port, from 1 through 65535. Compose uses the container-side port `5432`. |
| `OBDSCHAT_DB_NAME` | Unset | Yes | Database name used by the backend and synchronizer. Must match `POSTGRES_DB` in Compose. |
| `OBDSCHAT_DB_USER` | Unset | Yes | Database user used by the backend and synchronizer. Must match `POSTGRES_USER` in Compose. |
| `OBDSCHAT_DB_PASSWORD_FILE` | `<base>/config/secrets/obdschat_db_password.txt` | Preferred | Backend-readable password file. Compose uses `/run/secrets/obdschat_db_password`. |
| `OBDSCHAT_DB_PASSWORD` | Unset | Alternative | Direct database password. Prefer a mounted file outside local development. |

The backend reads XSD files below `<OBDSCHAT_BASE_DIR>/data/xsd`. Relative key
and password file paths are also resolved below `OBDSCHAT_BASE_DIR`.

## Compose host paths and published ports

| Variable | Default | Description |
| --- | --- | --- |
| `OBDSCHAT_INIT_DATA` | `./db` | Host directory mounted read-only into PostgreSQL initialization. Scripts run automatically only for an empty database directory. |
| `OBDSCHAT_DB_DATA_DIR` | `./data/obdschat_db/data` | Host path containing persistent PostgreSQL data. Changing it points Compose at a different database directory. |
| `OBDSCHAT_DB_LOG_DIR` | `./data/obdschat_db/logs` | Host path mounted for PostgreSQL file logs. Container output remains available through Compose logs. |
| `OBDSCHAT_DB_PUBLISHED_PORT` | `55434` | Host PostgreSQL port, bound to `127.0.0.1` only. Application containers continue to use port `5432`. |
| `OBDSCHAT_BACKEND_PORT` | `18000` | IPv4 loopback port mapped to backend port `8000`. |
| `OBDSCHAT_FRONTEND_PORT` | `17860` | IPv4 loopback port mapped to frontend port `7860`. |
| `OBDSCHAT_DOCS_PORT` | `8000` | IPv4 loopback port mapped to documentation server port `8080`. |
| `OBDSCHAT_DB_PASSWORD_SOURCE` | `./config/secrets/obdschat_db_password.txt` | Host file mounted as the database-password Compose secret. |
| `TZ` | Image or host default | Time zone supplied to services using `.env`; the example uses `Europe/Berlin`. |

## PostgreSQL container initialization

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `POSTGRES_USER` | Example: `obdschat` | Yes in this deployment | User created by the ParadeDB image. Keep equal to `OBDSCHAT_DB_USER`. |
| `POSTGRES_DB` | Example: `obdschat` | Yes in this deployment | Database created by the ParadeDB image. Keep equal to `OBDSCHAT_DB_NAME`. |
| `POSTGRES_PASSWORD_FILE` | Example: `/run/secrets/obdschat_db_password` | Yes in this deployment | Container path read by the image. It must match the mounted database-password secret. |

These values initialize an empty data directory. Changing them later does not
rename an existing PostgreSQL user or database.

## Frontend connection

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `BACKEND_URL` | `http://localhost:8000` | No | Absolute HTTP(S) backend base URL used by the frontend process. Compose fixes it to `http://backend:8000`. |
| `OBDSCHAT_QUERY_CONCURRENCY` | `8` | No | Positive integer limiting simultaneous query-completion events in one frontend process. Compose interpolates the value from `.env` and also defaults to `8`. |

`BACKEND_URL` is not currently interpolated from `.env` in the supplied Compose
file. Change the Compose service or use an override file when the frontend must
reach a different backend.

The query concurrency limit is read when the frontend module starts; zero,
negative, and non-integer values fail startup. It covers both submission paths ('Prüfen' and hitting 'Enter') through their shared Gradio concurrency group. It does not cap direct backend API traffic or set the number of model tool rounds within one request.

## Security invariants

- Keep provider and database secrets out of `.env`, shell history, logs, and
  version control.
- Keep `OBDSCHAT_DB_USER`/`POSTGRES_USER` and `OBDSCHAT_DB_NAME`/`POSTGRES_DB` aligned.
- The application provides no authentication or authorization. Control network
  access outside the application before exposing frontend, backend, or
  documentation ports.
