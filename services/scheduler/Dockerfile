FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app:/app/services/scheduler

WORKDIR /app/services/scheduler

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl iputils-ping && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements-dev.txt /app/
COPY services/scheduler/requirements.txt ./scheduler-requirements.txt
COPY libs/ /app/libs/

RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r scheduler-requirements.txt

COPY services/scheduler/ ./

EXPOSE ${SCHEDULER_PORT}
HEALTHCHECK CMD curl --fail http://localhost:${SCHEDULER_PORT}/scheduler/health || exit 1

CMD ["sh", "-c", "uvicorn services.scheduler.app.main:app --host 0.0.0.0 --port ${SCHEDULER_PORT} --workers 1 --root-path /scheduler"]
