"""Prometheus HTTP API — nginx ingress error-rate query."""
from __future__ import annotations

import warnings

import requests


# ── Private helper (monkeypatch-friendly) ──────────────────────────────────────

def _http_get(url: str, params: dict, timeout: float = 5.0) -> requests.Response:
    return requests.get(url, params=params, timeout=timeout)


# ── Public API ─────────────────────────────────────────────────────────────────

def get_error_rate(
    service_name: str,
    namespace: str,
    prometheus_url: str = "http://localhost:9090",
    window: str = "1m",
) -> float:
    """Return the 5xx error rate (0.0–100.0) for service_name from Prometheus.

    Uses the nginx ingress controller request metrics split by HTTP status.
    Returns 0.0 when Prometheus is unreachable or returns no data — metrics
    being down should never abort a deploy; the smoke test is the hard gate.
    """
    promql = (
        f'sum(rate(nginx_ingress_controller_requests'
        f'{{service=~"{service_name}",status=~"5.."}}[{window}]))'
        f' / sum(rate(nginx_ingress_controller_requests'
        f'{{service=~"{service_name}"}}[{window}])) * 100'
    )
    url = f"{prometheus_url}/api/v1/query"

    try:
        resp = _http_get(url, params={"query": promql})
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        warnings.warn(
            f"Prometheus unreachable at {prometheus_url}: {exc} — skipping metrics gate",
            stacklevel=2,
        )
        return 0.0

    try:
        result = data["data"]["result"]
        if not result:
            return 0.0
        return float(result[0]["value"][1])
    except (KeyError, IndexError, ValueError, TypeError):
        return 0.0
