"""Loki ingestion and log normalization.

Pulled out of ``fetch_normalize_logs.py`` so the orchestrator there stays
focused on pipeline wiring. The functions here are pure HTTP + parsing —
no remediation, alerting, or LLM dependency.
"""

from __future__ import annotations

import datetime
from typing import Any

LOKI_URL = "http://localhost:3100/loki/api/v1/query_range"
QUERY = '{job="systemd-journal"} |= "error" or |= "warning"'
LIMIT = 100


def fetch_logs(
    loki_url: str = LOKI_URL,
    query: str = QUERY,
    limit: int = LIMIT,
    lookback: datetime.timedelta = datetime.timedelta(hours=1),
) -> list[dict[str, Any]]:
    """Query Loki for recent error/warning log entries.

    Network errors are caught and reported but never re-raised: a temporarily
    unreachable Loki must not crash the agent loop.
    """
    import requests

    now = datetime.datetime.now(datetime.timezone.utc)
    end = int(now.timestamp() * 1e9)
    start = int((now - lookback).timestamp() * 1e9)

    params = {
        "query": query,
        "limit": limit,
        "start": start,
        "end": end,
        "direction": "backward",
    }

    try:
        response = requests.get(loki_url, params=params, timeout=10)
        response.raise_for_status()
        return response.json().get("data", {}).get("result", [])
    except Exception as exc:
        print(f"Error querying Loki: {exc}")
        return []


def parse_and_normalize(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert raw Loki stream entries into ``{timestamp, host, unit, message}``."""
    events: list[dict[str, Any]] = []
    for stream in logs:
        stream_labels = stream.get("stream", {})
        for entry in stream.get("values", []):
            ts_ns, raw_message = entry
            events.append({
                "timestamp": datetime.datetime.fromtimestamp(
                    int(ts_ns) / 1e9, tz=datetime.timezone.utc,
                ).isoformat(),
                "host": stream_labels.get("host", "unknown"),
                "unit": stream_labels.get("systemd_unit", "unknown"),
                "message": raw_message.strip(),
            })
    return events
