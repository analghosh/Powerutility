# syntax=docker/dockerfile:1.4
FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_HOME=/app \
    PYTHONPATH=/app/libs:/app/epms_backend:/app/services/ingestion

WORKDIR $APP_HOME

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl iputils-ping build-essential git && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy dependency files first (better layer caching)
COPY services/ingestion/requirements.txt ./ingestion-requirements.txt
COPY libs/ ./libs/
COPY epms_backend/ ./epms_backend/

# Install Python dependencies
RUN pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r ingestion-requirements.txt && \
    pip install --editable ./epms_backend && \
    pip install --editable ./libs

# Copy ingestion source last (so code changes don’t invalidate dependency layers)
COPY services/ingestion/ ./services/ingestion/

# Create non-root user and fix permissions
RUN useradd -m appuser && chown -R appuser:appuser $APP_HOME
USER appuser

WORKDIR /app/services/ingestion

# Run Uvicorn (port mapped via Docker Compose, not exposed here)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0"]
