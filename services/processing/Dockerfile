# syntax=docker/dockerfile:1.4

# 🔹 Lightweight base image
FROM python:3.11-slim-bookworm

# 🔹 Environment settings
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DOCKER_CONTAINER=true

# 🔹 Set working directory
WORKDIR /app

# 🔹 Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        iputils-ping \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 🔹 Copy requirements and libs for caching
COPY requirements-dev.txt ./
COPY services/processing/requirements.txt ./processing-requirements.txt
COPY libs/ ./libs/

# 🔹 Install Python dependencies
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r processing-requirements.txt

# 🔹 Copy processing service source code
COPY services/processing/ ./services/processing/

# 🔹 Healthcheck (using env port)
HEALTHCHECK CMD curl --fail http://localhost:${PROCESSING_PORT}/health || exit 1

# 🔹 Expose app port (dynamic)
EXPOSE ${PROCESSING_PORT}

# 🔹 Start FastAPI app
#
# --timeout-graceful-shutdown is required because this service holds SSE
# connections open (/api/v2/notifications/stream, up to MAX_STREAM_SECONDS=1800).
# uvicorn waits for every open connection before running the app's shutdown, and
# that wait is unbounded by default -- so SIGTERM would leave this process alive,
# still subscribed to Redis, long after its replacement had started. An in-app
# stop event cannot fix it: the lifespan that would set the event only runs after
# the wait it is meant to end. Measured: 1s to exit with no streams open, >60s
# with streams and no timeout, 10s with it.
CMD ["sh", "-c", "uvicorn services.processing.app.main:app --host 0.0.0.0 --port ${PROCESSING_PORT} --workers 1 --timeout-graceful-shutdown 10 --root-path /processing"]
