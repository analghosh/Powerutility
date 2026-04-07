# syntax=docker/dockerfile:1.4

FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl iputils-ping \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements-dev.txt . 
COPY services/auth/requirements.txt ./auth-requirements.txt
COPY libs/ ./libs/

RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r auth-requirements.txt

COPY services/auth/ ./services/auth/

# EXPOSE is optional, but good for documentation
EXPOSE 8000

# Use shell form CMD so ENV vars are expanded at runtime
CMD uvicorn services.auth.app.main:app --host 0.0.0.0 --port ${AUTH_PORT} --reload --root-path /auth
