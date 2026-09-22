.PHONY: dev test

dev:
	.venv/bin/python scripts/dev.py

test:
	.venv/bin/ruff check .
	.venv/bin/pytest
	cd web && npm run lint
	cd web && npm run build
