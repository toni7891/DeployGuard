"""Minimal health-check server used when DeployGuard deploys itself.

The deploy engine monitors /healthz (liveness) and /readyz (readiness) during
the gradual rollout. This server exposes those endpoints so the engine can
watch its own deployment the same way it watches any other service.
"""
from fastapi import FastAPI

app = FastAPI(title="deployguard", docs_url=None, redoc_url=None)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict:
    return {"status": "ok"}
