FROM python:3.11-slim AS builder
WORKDIR /build
COPY pyproject.toml ./
COPY deployguard/ ./deployguard/
RUN pip install --no-cache-dir .

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=builder /usr/local /usr/local
COPY deployguard/ ./deployguard/
USER 65534
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')" || exit 1
CMD ["uvicorn", "deployguard.server:app", "--host", "0.0.0.0", "--port", "8080"]
