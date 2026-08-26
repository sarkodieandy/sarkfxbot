param(
    [ValidateSet("install", "test", "lint", "typecheck", "api", "worker", "backtest", "migrate")]
    [string]$Command = "test"
)

$ErrorActionPreference = "Stop"

switch ($Command) {
    "install"   { uv sync --extra dev }
    "test"      { .\.venv\Scripts\pytest.exe --cov=app --cov-report=term-missing --cov-fail-under=80 }
    "lint"      { .\.venv\Scripts\ruff.exe check .; .\.venv\Scripts\black.exe --check . }
    "typecheck" { .\.venv\Scripts\mypy.exe app scripts }
    "api"       { .\.venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 8000 --no-access-log }
    "worker"    { .\.venv\Scripts\python.exe -m app.workers.runner }
    "backtest"  { .\.venv\Scripts\python.exe scripts\run_backtest.py --sample --output reports\sample }
    "migrate"   { .\.venv\Scripts\alembic.exe upgrade head }
}
