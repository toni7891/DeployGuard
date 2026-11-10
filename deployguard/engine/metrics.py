"""Prometheus HTTP API — nginx ingress error-rate query for the canary gate."""
from __future__ import annotations

import math
from enum import Enum

import requests

_METRIC = "nginx_ingress_controller_requests"

# rate() window for the per-step gate. `dg deploy` dwells 10s per step, so a
# 1m window would be dominated by requests from before the weight change (or by
# a previous canary). 30s is the shortest window that still holds two samples
# at the common 15s scrape interval, which rate() needs to return anything.
GATE_WINDOW = "30s"


class MetricsStatus(str, Enum):
    """Whether the error-rate gate has anything to gate on."""

    OK = "ok"
    UNREACHABLE = "unreachable"  # Prometheus did not answer
    NO_DATA = "no_data"  # Prometheus answered, but has no requests for this service


# ── Private helpers (monkeypatch-friendly) ─────────────────────────────────────

def _http_get(url: str, params: dict, timeout: float = 5.0) -> requests.Response:
    return requests.get(url, params=params, timeout=timeout)


def _query(prometheus_url: str, promql: str) -> list:
    """Run an instant query and return data.result. Raises requests.RequestException."""
    resp = _http_get(f"{prometheus_url.rstrip('/')}/api/v1/query", params={"query": promql})
    resp.raise_for_status()
    data = resp.json()
    try:
        return data["data"]["result"] or []
    except (KeyError, TypeError):
        return []


def _canary_rate(service_name: str, namespace: str, window: str, status: str = "") -> str:
    """sum(rate(...)) of requests served by the canary backend.

    ingress-nginx labels a request with the Service it was routed to
    (`service="<name>-canary"`). Controllers that keep the main Service in
    `service` for canary traffic instead set `canary="<ns>-<svc>-<port>"`
    (the alternative upstream name), so fall back to that label. `or` picks
    the first non-empty side, so a request is never counted twice.
    """
    canary = f"{service_name}-canary"
    by_service = f'{_METRIC}{{namespace="{namespace}",service="{canary}"{status}}}'
    by_upstream = f'{_METRIC}{{namespace="{namespace}",canary=~"{namespace}-{canary}-.+"{status}}}'
    return f"(sum(rate({by_service}[{window}])) or sum(rate({by_upstream}[{window}])))"


def canary_error_rate_promql(service_name: str, namespace: str, window: str = GATE_WINDOW) -> str:
    """PromQL for the canary's 5xx ratio in percent.

    - numerator falls back to vector(0) when the canary has served no 5xx yet
    - denominator is filtered with `> 0`, so no canary traffic yields an empty
      result ("no data") instead of 0% or NaN
    """
    errors = _canary_rate(service_name, namespace, window, ',status=~"5.."')
    total = _canary_rate(service_name, namespace, window)
    return f"({errors} or vector(0)) / ({total} > 0) * 100"


# ── Public API ─────────────────────────────────────────────────────────────────

def check_metrics_available(
    service_name: str,
    namespace: str,
    prometheus_url: str = "http://localhost:9090",
) -> MetricsStatus:
    """Probe Prometheus once so callers can tell the user whether the gate is live.

    Looks for any ingress request series for the stable or canary Service in
    the namespace (series existence, not rate, so one scrape is enough).
    Never raises: an unreachable Prometheus must not block a deploy.
    """
    promql = (
        f'count({_METRIC}{{namespace="{namespace}",'
        f'service=~"{service_name}|{service_name}-canary"}})'
    )
    try:
        result = _query(prometheus_url, promql)
    except requests.RequestException:
        return MetricsStatus.UNREACHABLE
    return MetricsStatus.OK if result else MetricsStatus.NO_DATA


def get_error_rate(
    service_name: str,
    namespace: str,
    prometheus_url: str = "http://localhost:9090",
    window: str = GATE_WINDOW,
) -> float | None:
    """Return the canary backend's 5xx rate (0.0–100.0), or None for "no data".

    None means Prometheus is unreachable, answered with nothing (no canary
    traffic in the window), or answered with something unparseable. Callers
    must treat None as "do not gate": metrics being down never aborts a deploy;
    the pre-check is the hard gate. Silent by design: call
    check_metrics_available() once up front to tell the user the gate is inactive.
    """
    promql = canary_error_rate_promql(service_name, namespace, window)
    try:
        result = _query(prometheus_url, promql)
    except requests.RequestException:
        return None

    try:
        if not result:
            return None
        value = float(result[0]["value"][1])
    except (KeyError, IndexError, ValueError, TypeError):
        return None
    return None if math.isnan(value) else value
