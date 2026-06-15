# GShare backend image — shared by `api` and `worker` processes
# Multi-stage: base (shared sources) branches into test (dev dependencies plus pytest) and runtime.
# runtime is the default target.
#   Runtime: docker build -t gshare-backend .
#   Tests:   docker build --target test -t gshare-backend-test . && docker run --rm gshare-backend-test
#            pytest caches and coverage data land only in the container's writable layer, so --rm
#            removes them.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY pyproject.toml README.md ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

# ── test: dev dependencies plus the test suite, run entirely inside the container ──
# Installing dependencies before copying tests means a test-only change rebuilds just the cheap
# COPY layer.
FROM base AS test
RUN pip install --upgrade pip && pip install ".[dev]"
COPY tests ./tests
# Unit and functional tests with a coverage summary, excluding the real-GPU e2e tests.
CMD ["pytest", "-m", "not realgpu", "--cov=app", "--cov-report=term-missing"]

# ── runtime: production dependencies only; the default build target ──
FROM base AS runtime
RUN pip install --upgrade pip && pip install .

EXPOSE 8000

# Default to the API; docker-compose overrides `command` for the worker.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
