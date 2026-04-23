# syntax=docker/dockerfile:1.6
#
# Lambda ERP — single-container demo image.
#
# Stage 1 builds the React/Vite frontend into static files.
# Stage 2 installs Python deps and runs uvicorn serving both the FastAPI
# backend and the frontend build at the same origin.
#
# Defaults are tuned for the public demo experience: SQLite in a mounted
# volume (/data), auto-seeded demo data + public_manager on startup. Override
# LAMBDA_ERP_AUTO_DEMO=0 and LAMBDA_ERP_DB=<path> for private use.

# ---------------------------------------------------------------------------
# Stage 1 — frontend build
# ---------------------------------------------------------------------------
FROM node:20-alpine AS frontend-build
WORKDIR /build

# Cache npm install when only source changes.
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci

COPY frontend/ ./
RUN npm run build
# Output lives at /build/dist

# ---------------------------------------------------------------------------
# Stage 2 — Python runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim

WORKDIR /app

# weasyprint needs the Pango/Cairo stack. curl powers the HEALTHCHECK.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install the Python project and its dependencies. flit needs the README.
COPY pyproject.toml README.md ./
COPY lambda_erp ./lambda_erp
RUN pip install --no-cache-dir .

# Application code + built frontend.
COPY api ./api
COPY --from=frontend-build /build/dist ./frontend/dist

# Writable paths — /data is the intended volume mount for persisted SQLite.
RUN mkdir -p /data /app/uploads \
    && groupadd -r app \
    && useradd -r -g app -d /app app \
    && chown -R app:app /data /app
USER app

ENV LAMBDA_ERP_DB=/data/lambda_erp.db \
    LAMBDA_ERP_AUTO_DEMO=1 \
    PORT=8000 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT}/api/health" || exit 1

# --workers 1 is a hard constraint, not a default. SQLite + the in-memory
# chat session state (session_tasks, demo_typing_waiters) cannot be shared
# across worker processes. Scaling up means moving to Postgres and shared
# Redis state, not bumping this number.
CMD ["sh", "-c", "exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
