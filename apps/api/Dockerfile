# NETRA API — deployable to any container host (Render, Railway, Fly.io,
# Cloud Run, a bare VM, or Docker Desktop).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so code edits do not invalidate the layer.
COPY apps/api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY apps/api/app       ./app
COPY infra/schema.sql   ./infra/schema.sql
COPY scripts/seed_demo.py ./scripts/seed_demo.py

# The database lives on a mounted volume so it survives redeploys.
# On Fly.io/Render, attach a persistent disk at /data.
ENV NETRA_DB=/data/netra.db \
    NETRA_EVIDENCE_DIR=/data/evidence
RUN mkdir -p /data/evidence

EXPOSE 8000

# Single worker on purpose: the SSE broker holds subscriber queues in
# process memory, so a second worker would serve half the dashboard
# connections events the other half never sees. Scale out only after
# moving the broker to Redis.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
