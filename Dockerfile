FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/goldflow/.venv/bin:${PATH}"

RUN groupadd --system goldflow \
    && useradd --system --gid goldflow --home-dir /opt/goldflow goldflow

WORKDIR /opt/goldflow

COPY requirements.txt ./
RUN python -m venv .venv \
    && .venv/bin/pip install --upgrade pip \
    && .venv/bin/pip install --requirement requirements.txt

COPY pyproject.toml README.md ./
COPY app ./app
COPY scripts ./scripts
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

RUN chown -R goldflow:goldflow /opt/goldflow
USER goldflow

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
