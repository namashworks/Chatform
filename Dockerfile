# syntax=docker/dockerfile:1.6
#
# Production image for Chatform.
# - Multi-stage build: deps compiled in a builder, copied into a slim runtime.
# - Non-root user.
# - Cloud Run-friendly: listens on $PORT, single CMD, fast cold start.
#
# Build:
#   docker build -t chatform:latest .
# Run locally:
#   docker run --rm -p 8080:8080 \
#     -e PUBLIC_BASE_URL=http://localhost:8080 \
#     chatform:latest
#
# For GCP deployment use scripts/deploy_cloud_run.sh or cloudbuild.yaml.

# ---------- builder ----------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Compile deps to wheels first so the runtime layer doesn't need a compiler.
COPY requirements.txt requirements-cloud.txt ./
RUN pip wheel --wheel-dir /wheels -r requirements.txt -r requirements-cloud.txt


# ---------- runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    STORAGE_BACKEND=cloud \
    PORT=8080

# Non-root user so the container follows least-privilege.
RUN groupadd --system app && useradd --system --gid app --home /home/app --shell /sbin/nologin app
WORKDIR /app

# Install only the pre-built wheels — no compiler, no apt cache.
COPY --from=builder /wheels /wheels
COPY requirements.txt requirements-cloud.txt ./
RUN pip install --no-index --find-links=/wheels -r requirements.txt -r requirements-cloud.txt \
 && rm -rf /wheels

# Copy application source. .dockerignore keeps junk (sessions/, .venv, .env) out.
COPY --chown=app:app . .

# Streamlit creates a config dir on startup; pre-create + chown to keep it writable.
RUN mkdir -p /home/app/.streamlit && chown -R app:app /home/app

USER app

EXPOSE 8080

# Cloud Run sets $PORT; default 8080 for local runs.
# --server.address 0.0.0.0 makes us reachable from outside the container.
# --browser.gatherUsageStats false keeps the container quiet on first request.
CMD streamlit run app.py \
    --server.port=${PORT:-8080} \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --browser.gatherUsageStats=false
