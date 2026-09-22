FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9

ARG APP_VERSION=v0.1.0
ARG COMMIT_SHA=not-built

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    APP_VERSION=${APP_VERSION} \
    COMMIT_SHA=${COMMIT_SHA}

LABEL org.opencontainers.image.source="https://github.com/vincody/multi-cloud-deployment-failover" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${COMMIT_SHA}"

WORKDIR /service

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/ready', timeout=2)"]

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
