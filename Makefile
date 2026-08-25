DOCS_COMPOSE = docker compose -f docker-compose.docs.yaml

.PHONY: docs docs-api docs-db docs-serve docs-check docs-db-check

docs: docs-api docs-db
	uv run --group docs mkdocs build --clean

docs-api:
	uv run --group backend python -m scripts.export_openapi

docs-db:
	@set -eu; \
	cleanup() { $(DOCS_COMPOSE) down --volumes --remove-orphans; }; \
	trap cleanup EXIT INT TERM; \
	$(DOCS_COMPOSE) up --detach --wait database; \
	$(DOCS_COMPOSE) run --rm tbls doc --rm-dist

docs-serve: docs-api docs-db
	uv run --group docs mkdocs serve

docs-db-check:
	@set -eu; \
	cleanup() { $(DOCS_COMPOSE) down --volumes --remove-orphans; }; \
	trap cleanup EXIT INT TERM; \
	$(DOCS_COMPOSE) up --detach --wait database; \
	$(DOCS_COMPOSE) run --rm tbls diff; \
	$(DOCS_COMPOSE) run --rm tbls lint

docs-check: docs-db-check
	uv run --group backend python -m scripts.export_openapi --check
	uv run --group docs mkdocs build --strict --clean --site-dir /tmp/obdschat-docs
