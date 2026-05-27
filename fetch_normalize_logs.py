"""RootMedic agent: fetch logs from Loki, normalize, and feed into the
graduated autonomy remediation engine.
"""

import datetime
import json

from remediation_engine import (
    RemediationEngine,
    RemediationPlan,
    fingerprint_issue,
)

try:
    from llm_client import load_config as _load_llm_config, propose_plan as _llm_propose_plan
except ImportError:  # pragma: no cover - llm_client is optional at import time
    _load_llm_config = lambda: None
    _llm_propose_plan = lambda event, config=None: None

# --- Configuration -----------------------------------------------------------
LOKI_URL = "http://localhost:3100/loki/api/v1/query_range"
QUERY = '{job="systemd-journal"} |= "error" or |= "warning"'
LIMIT = 100


def fetch_logs(loki_url: str = LOKI_URL, query: str = QUERY, limit: int = LIMIT):
    """Query Loki for recent error/warning log entries."""
    import requests

    now = datetime.datetime.now(datetime.timezone.utc)
    end = int(now.timestamp() * 1e9)
    start = int((now - datetime.timedelta(hours=1)).timestamp() * 1e9)

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


def parse_and_normalize(logs):
    """Convert raw Loki stream entries into structured events."""
    events = []
    for stream in logs:
        stream_labels = stream.get("stream", {})
        for entry in stream.get("values", []):
            ts_ns, raw_message = entry
            events.append({
                "timestamp": datetime.datetime.fromtimestamp(
                    int(ts_ns) / 1e9, tz=datetime.timezone.utc
                ).isoformat(),
                "host": stream_labels.get("host", "unknown"),
                "unit": stream_labels.get("systemd_unit", "unknown"),
                "message": raw_message.strip(),
            })
    return events


def build_remediation_plan(event: dict, engine: RemediationEngine) -> RemediationPlan | None:
    """Produce a RemediationPlan for a single normalized log event.

    In production this would call the LLM; here a rule-based stub
    demonstrates the pipeline.
    """
    msg_lower = event["message"].lower()
    fp = fingerprint_issue(event["message"], event["unit"])

    # --- Simple rule-based dispatch ------------------------------------------
    if "connection refused" in msg_lower:
        return RemediationPlan(
            issue_fingerprint=fp,
            description="Restart service after upstream connection refusal",
            commands=[f"systemctl restart {event['unit']}"],
            rollback_commands=[
                f"systemctl stop {event['unit']}",
                f"systemctl start {event['unit']}",
            ],
        )

    if "out of memory" in msg_lower or "oom" in msg_lower:
        svc = event["unit"]
        return RemediationPlan(
            issue_fingerprint=fp,
            description=f"Restart {svc} and drop caches after OOM",
            commands=[
                f"systemctl restart {svc}",
                "sync && echo 3 > /proc/sys/vm/drop_caches",
            ],
            rollback_commands=[f"systemctl stop {svc}", f"systemctl start {svc}"],
        )

    if "disk full" in msg_lower or "no space" in msg_lower:
        return RemediationPlan(
            issue_fingerprint=fp,
            description="Clean journal logs and apt cache to free disk space",
            commands=[
                "journalctl --vacuum-size=200M",
                "apt-get clean",
            ],
            rollback_commands=[],
        )

    return None  # no plan for unrecognized issues


def run_agent():
    """Main agent loop: fetch → normalize → remediate with graduated autonomy.

    For each event we first try the rule-based stub. If it has nothing, and a
    LiteLLM config is present, we ask the LLM for a plan as a fallback.
    """
    engine = RemediationEngine()
    logs = fetch_logs()
    events = parse_and_normalize(logs)

    if not events:
        print("No error/warning events found.")
        return

    llm_config = _load_llm_config()

    results = []
    for event in events:
        plan = build_remediation_plan(event, engine)
        if plan is None and llm_config is not None:
            plan = _llm_propose_plan(event, llm_config)
        if plan is None:
            continue

        level, record, confidence = engine.assess(event["message"], event["unit"])
        plan.confidence = confidence

        print(
            f"[{event['timestamp']}] {event['host']} "
            f"{event['unit']} → {level.value.upper()} "
            f"(conf={confidence:.0%}, seen={record.occurrences}x)"
        )

        result = engine.execute(plan, level)
        result["event"] = event
        results.append(result)

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    run_agent()
