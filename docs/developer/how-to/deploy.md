# How to deploy and upgrade oBDSChat

This guide deploys oBDSChat on one Docker Compose host and upgrades an existing
installation. It assumes the host has Git, Docker with Compose, persistent local
storage, and outbound HTTPS access to the model provider and official oBDS
sources.

The repository does not define a clustered, zero-downtime, or orchestrated
deployment.

## Protect the network boundary

The application has no authentication or authorization. The database is
published on loopback only, but the supplied backend and frontend port mappings
bind all host interfaces.

Before deployment, choose one of these boundaries:

- keep the host and published ports on a trusted private network; or
- place an authenticated TLS-terminating gateway in front of the frontend and
  restrict direct access to the published application ports.

Do not expose the supplied Compose ports directly to an untrusted network.

## Deploy a reviewed revision

1. Check out the reviewed application revision on the target host.

2. Create deployment configuration:

   ```bash
   cp .env.example .env
   mkdir -p config/secrets
   chmod 700 config/secrets
   ```

3. Put the database password in
   `config/secrets/obdschat_db_password.txt` and the selected model-provider key
   in `config/secrets/llm_api_key.txt`. Restrict both files:

   ```bash
   chmod 600 config/secrets/obdschat_db_password.txt
   chmod 600 config/secrets/llm_api_key.txt
   ```

4. Review `.env` against the
   [runtime configuration reference](../reference/runtime-configuration.md).
   On a managed host, use stable absolute paths for PostgreSQL data and logs.

5. Validate the resolved Compose model:

   ```bash
   docker compose config --quiet
   ```

   This validates Compose syntax and interpolation. It does not prove that a
   secret file contains the correct value or that remote dependencies are
   reachable.

6. Build and start the deployment:

   ```bash
   docker compose up -d --build
   ```

7. Follow initial source synchronization:

   ```bash
   docker compose ps -a
   docker compose logs --tail=200 --timestamps source-sync
   ```

   Wait until `source-sync` exits successfully. The backend starts only after
   that checkpoint; the frontend starts only after the backend becomes healthy.

8. Verify processes and health routes:

   ```bash
   docker compose ps -a
   curl -fsS http://localhost:18000/health
   curl -fsS http://localhost:17860/health
   ```

9. Open `http://localhost:17860` from an allowed client and complete one
   source-grounded question. Confirm that its evidence opens.

Expected result: the database, backend, and frontend are healthy;
`source-sync` has exited with status zero; and the UI completes a request using
the selected provider.

## Upgrade an installation

1. Record the currently deployed revision and inspect local changes:

   ```bash
   git rev-parse HEAD
   git status --short
   ```

2. Fetch and check out the reviewed target revision using your release process.

3. Compare `.env.example`, `docker-compose.yaml`, and the container files with
   the deployed revision. Apply required configuration changes to `.env` and the
   secret files.

4. If `db/init.sql` changed, review
   [How to change stored source data](change-stored-data.md) before continuing.
   Bootstrap SQL alone does not update existing database objects.

5. Validate, rebuild, and reconcile services:

   ```bash
   docker compose config --quiet
   docker compose build --pull
   docker compose up -d --remove-orphans
   ```

6. Monitor the dependency chain and recent logs:

   ```bash
   docker compose ps -a
   docker compose logs --since=15m --timestamps obdschat-db source-sync backend frontend
   ```

7. Repeat the health-route and grounded-question checks from the initial
   deployment.

Expected result: containers use the target revision, source synchronization has
completed, and health and functional checks pass.

## Roll back an application-only release

Use this procedure only when the release did not make persistent schema or data
changes.

1. Check out the previously deployed revision.
2. Apply the configuration expected by that revision.
3. Rebuild and reconcile services:

   ```bash
   docker compose up -d --build --remove-orphans
   ```

4. Repeat the deployment verification steps.

An application rollback does not reverse changes to persistent PostgreSQL
objects or synchronized source data.
