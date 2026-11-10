"""Light, steady test traffic through the ingress during a local canary rollout.

Without requests there is no `nginx_ingress_controller_requests` data, and the
error-rate gate has nothing to gate on. Local target only: on aws the service
has real users and DeployGuard never generates traffic there.
"""
from __future__ import annotations

import http.client
import threading
import time
from contextlib import contextmanager
from typing import Iterator

from rich.console import Console

from deployguard.config import AppConfig
from deployguard.engine.portforward import port_forward
from deployguard.manifest import ServiceManifest

console = Console()

INGRESS_NAMESPACE = "ingress-nginx"
INGRESS_SERVICE = "svc/ingress-nginx-controller"
REQUESTS_PER_SECOND = 10


# ── Private helpers (monkeypatch-friendly) ─────────────────────────────────────

def _send_request(port: int, host: str, path: str = "/", timeout: float = 2.0) -> int:
    """One GET to localhost:<port><path> with the given Host header; returns the status."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", path, headers={"Host": host, "Connection": "close"})
        resp = conn.getresponse()
        resp.read()
        return resp.status
    finally:
        conn.close()


class TrafficGenerator:
    """Background thread sending ~rps GETs per second. Failures are counted, never raised."""

    def __init__(self, port: int, host: str, rps: int = REQUESTS_PER_SECOND, path: str = "/"):
        self.port = port
        self.host = host
        self.path = path
        self.interval = 1.0 / max(rps, 1)
        self.sent = 0
        self.failed = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="dg-traffic", daemon=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                status = _send_request(self.port, self.host, self.path)
                self.sent += 1
                if status >= 500:
                    self.failed += 1
            except Exception:  # noqa: BLE001 — test traffic must never break a deploy
                self.failed += 1
            remaining = self.interval - (time.monotonic() - started)
            if remaining > 0:
                self._stop.wait(remaining)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Stop and join the thread. Idempotent."""
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    @property
    def running(self) -> bool:
        return self._thread.is_alive()


# ── Public API ─────────────────────────────────────────────────────────────────

@contextmanager
def smoke_traffic(manifest: ServiceManifest, config: AppConfig) -> Iterator[TrafficGenerator | None]:
    """Send test requests to manifest.host through the ingress while the block runs.

    Yields the running TrafficGenerator (call .stop() to end it early; it is
    stopped again on exit), or None when traffic is off: aws target,
    deploy.smoke_traffic false, or the ingress port-forward could not start.
    """
    if config.deploy.target != "local" or not config.deploy.smoke_traffic:
        yield None
        return
    host = manifest.host or f"{manifest.name}.local"
    with port_forward(INGRESS_NAMESPACE, INGRESS_SERVICE, 80) as port:
        if port is None:
            yield None
            return
        gen = TrafficGenerator(port, host)
        console.print(f"[dim]traffic: sending test requests to {host} (local target)[/dim]")
        gen.start()
        try:
            yield gen
        finally:
            gen.stop()
