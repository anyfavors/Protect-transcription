"""
Lightweight in-process metrics.

No prometheus_client dependency — emits a simple text-format response on /metrics
that scrapers (Prometheus, VictoriaMetrics) understand.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict

_lock = threading.Lock()
_counters: dict[str, float] = defaultdict(float)
_gauges: dict[str, float] = defaultdict(float)
_started_at = time.time()


def uptime_seconds() -> float:
    return time.time() - _started_at


def inc(name: str, value: float = 1.0) -> None:
    with _lock:
        _counters[name] += value


def set_gauge(name: str, value: float) -> None:
    with _lock:
        _gauges[name] = value


def render() -> str:
    """Render counters and gauges in Prometheus text exposition format."""
    with _lock:
        counters = dict(_counters)
        gauges = dict(_gauges)

    # Always include uptime — useful for alerting on rapid restarts.
    gauges.setdefault("process_uptime_seconds", uptime_seconds())

    lines: list[str] = []
    for name, value in sorted(counters.items()):
        lines.append(f"# TYPE {name} counter")
        lines.append(f"{name} {value}")
    for name, value in sorted(gauges.items()):
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {value}")
    return "\n".join(lines) + "\n"


def snapshot() -> dict[str, dict[str, float]]:
    """Return current state as a dict (used by /health/ready)."""
    with _lock:
        return {"counters": dict(_counters), "gauges": dict(_gauges)}
