.PHONY: docs docs-api docs-serve docs-check

docs: docs-api
	uv run --group docs mkdocs build --clean

docs-api:
	uv run --group backend python -m scripts.export_openapi

docs-serve: docs-api
	uv run --group docs mkdocs serve

docs-check:
	uv run --group backend python -m scripts.export_openapi --check
	uv run --group docs mkdocs build --strict --clean --site-dir /tmp/obdschat-docs
