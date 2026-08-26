.PHONY: install test lint format typecheck api worker backtest migrate check

UV_CACHE_DIR ?= /tmp/goldflow-uv-cache

install:
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv sync --extra dev

test:
	.venv/bin/pytest --cov=app --cov-report=term-missing --cov-report=html --cov-fail-under=80

lint:
	.venv/bin/ruff check .
	.venv/bin/black --check .

format:
	.venv/bin/ruff check --fix .
	.venv/bin/black .

typecheck:
	.venv/bin/mypy app scripts

api:
	.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log

worker:
	.venv/bin/python -m app.workers.runner

backtest:
	.venv/bin/python scripts/run_backtest.py --sample --output reports/sample

migrate:
	.venv/bin/alembic upgrade head

check: lint typecheck test backtest
