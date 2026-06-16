"""RootMedic agent orchestration.

Wires the pipeline stages defined in the surrounding modules:

    Loki (ingest) → redactor → vector_store lookup
                  → rule-based planner → llm_client fallback
                  → RemediationEngine.recommend → AlertManager → archive

The agent never executes a remediation directly. Per ``log-analyzer-plan-A.md``
all applied fixes go through :meth:`remediation_engine.RemediationEngine.apply`,
which is invoked from a separate operator-driven entry point (CLI / web UI)
after a human has reviewed ``remediation.yaml``.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from ingest import fetch_logs, parse_and_normalize  # re-exported for tests
from redactor import redact_event
from vector_store import VectorStore, KnownIssue
from remediation_engine import (
    AutonomyLevel,
    RemediationEngine,
    RemediationPlan,
)
from fingerprint import fingerprint_issue
from archive import IncidentRecord, archive_incident

# --- Optional integrations ---------------------------------------------------

try:
    from llm_client import (
        load_config as _load_llm_config,
        propose_plan as _llm_propose_plan,
        LLMUnavailable,
    )
except ImportError:  # pragma: no cover
    _load_llm_config = lambda: None
    _llm_propose_plan = lambda event, config=None: None

    class LLMUnavailable(Exception):
        pass

try:
    from alerting import AlertManager
    from alert_plugins import AlertPayload
    _ALERTING_AVAILABLE = True
except ImportError:  # pragma: no cover
    _ALERTING_AVAILABLE = False


# ---------------------------------------------------------------------------
# Stage: planner
# ---------------------------------------------------------------------------


def _plan_from_known_issue(event: dict, issue: KnownIssue) -> RemediationPlan:
    return RemediationPlan(
        issue_fingerprint=issue.fingerprint,
        description=issue.description,
        commands=list(issue.commands),
        rollback_commands=list(issue.rollback_commands),
    )


def build_remediation_plan(event: dict, engine: RemediationEngine) -> Optional[RemediationPlan]:
    """Rule-based remediation suggestion (LLM-free path).

    Kept as a deterministic baseline so the agent has *something* sensible to
    propose when the LLM is offline. The pipeline orchestrator
    (:func:`run_agent`) tries the vector store first, then this function, then
    the LLM.
    """
    msg_lower = event["message"].lower()
    fp = fingerprint_issue(event["message"], event["unit"])

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

    if (
        "read-only file system" in msg_lower
        or "remounting read-only" in msg_lower
        or "-fs error" in msg_lower            # EXT4-fs / XFS-fs error
        or "i/o error" in msg_lower
    ):
        return RemediationPlan(
            issue_fingerprint=fp,
            description="Filesystem error — inspect kernel log and remount read-write",
            commands=[
                "journalctl -k -b | tail -n 50",
                "mount -o remount,rw /",
            ],
            rollback_commands=["mount -o remount,ro /"],
        )

    if (
        "main process exited" in msg_lower
        or "code=killed" in msg_lower
        or "result 'signal'" in msg_lower
        or "result 'core-dump'" in msg_lower
        or "segfault" in msg_lower
        or "/segv" in msg_lower
        or "entered failed state" in msg_lower
    ):
        svc = event["unit"]
        return RemediationPlan(
            issue_fingerprint=fp,
            description=f"Service {svc} crashed — clear failed state and restart",
            commands=[
                f"systemctl reset-failed {svc}",
                f"systemctl restart {svc}",
            ],
            rollback_commands=[f"systemctl stop {svc}"],
        )

    return None


# ---------------------------------------------------------------------------
# Stage: alert + archive
# ---------------------------------------------------------------------------


def _send_alert(
    manager: Optional["AlertManager"],
    event: dict,
    plan: RemediationPlan,
    level: AutonomyLevel,
    occurrences: int,
) -> None:
    if manager is None or not _ALERTING_AVAILABLE:
        return
    payload = AlertPayload(
        fingerprint=plan.issue_fingerprint,
        error_summary=event["message"][:200],
        timestamp=time.time(),
        proposed_remediation="\n".join(plan.commands),
        autonomy_level=level.value.upper(),
        occurrence_count=occurrences,
        host=event.get("host", ""),
        unit=event.get("unit", ""),
    )
    try:
        manager.send_alert(payload)
    except Exception as exc:
        print(f"[agent] alert dispatch failed: {exc}")


def _archive(
    event: dict,
    plan: RemediationPlan,
    level: AutonomyLevel,
    source: str,
    client_id: str = "default",
) -> None:
    record = IncidentRecord(
        fingerprint=plan.issue_fingerprint,
        timestamp=time.time(),
        host=event.get("host", "unknown"),
        unit=event.get("unit", "unknown"),
        message=event.get("message", ""),
        description=plan.description,
        commands=list(plan.commands),
        rollback_commands=list(plan.rollback_commands),
        autonomy_level=level.value,
        confidence=plan.confidence,
        dry_run_output=plan.dry_run_output,
        source=source,
    )
    try:
        archive_incident(record, client_id=client_id, plan_yaml=plan.to_yaml())
    except Exception as exc:
        print(f"[agent] archive write failed: {exc}")


# ---------------------------------------------------------------------------
# Pipeline driver
# ---------------------------------------------------------------------------


def run_agent() -> None:
    """Main agent loop: fetch → redact → lookup → plan → recommend → alert → archive."""
    engine = RemediationEngine()
    store = VectorStore()
    llm_config = _load_llm_config()
    alert_manager: Optional[AlertManager] = None
    if _ALERTING_AVAILABLE:
        try:
            alert_manager = AlertManager()
            if not alert_manager.plugins:
                alert_manager = None
        except Exception as exc:
            print(f"[agent] alert manager unavailable: {exc}")

    raw_logs = fetch_logs()
    events = parse_and_normalize(raw_logs)

    if not events:
        print("No error/warning events found.")
        return

    # Sanitize every event *before* it goes anywhere else.
    events = [redact_event(ev) for ev in events]

    results: list[dict[str, Any]] = []
    llm_enabled = llm_config is not None
    for event in events:
        try:
            plan, source = _resolve_plan(
                event, engine, store, llm_config if llm_enabled else None
            )
        except LLMUnavailable as exc:
            # A slow/unreachable LLM must not stall the whole run: stop trying it
            # after the first transport failure and fall through on remaining events.
            print(f"[agent] LLM unavailable ({exc}); skipping LLM for the rest of this run.")
            llm_enabled = False
            continue
        if plan is None:
            continue

        level, record, confidence = engine.assess(event["message"], event["unit"])
        plan.confidence = confidence

        print(
            f"[{event['timestamp']}] {event['host']} {event['unit']} "
            f"→ {level.value.upper()} (conf={confidence:.0%}, seen={record.occurrences}x, "
            f"source={source})"
        )

        recommendation = engine.recommend(plan, level)
        recommendation["event"] = event
        recommendation["source"] = source
        results.append(recommendation)

        _send_alert(alert_manager, event, plan, level, record.occurrences)
        _archive(event, plan, level, source)

        # Learn validated recommendations back into the known-issue store so
        # subsequent occurrences skip both the rule engine and the LLM.
        if level == AutonomyLevel.VALIDATED and source != "cached":
            store.store(
                event["message"], event["unit"],
                description=plan.description,
                commands=plan.commands,
                rollback_commands=plan.rollback_commands,
                source="learned",
            )

    print(json.dumps(results, indent=2))


def _resolve_plan(
    event: dict,
    engine: RemediationEngine,
    store: VectorStore,
    llm_config,
) -> tuple[Optional[RemediationPlan], str]:
    """Return ``(plan, source)`` for an event, trying cache → rule → LLM."""
    known = store.lookup(event["message"], event["unit"])
    if known is not None:
        return _plan_from_known_issue(event, known), "cached"

    plan = build_remediation_plan(event, engine)
    if plan is not None:
        return plan, "rule"

    if llm_config is not None:
        plan = _llm_propose_plan(event, llm_config)
        if plan is not None:
            return plan, "llm"

    return None, "none"


def run_loop(interval: int) -> None:
    """Run the agent forever, every ``interval`` seconds.

    Used by the systemd unit so the service stays active between scans instead
    of exiting after a single pass (which made ``systemctl is-active`` report
    the service as dead). A single fetch failure never breaks the loop.
    """
    print(f"[agent] starting in loop mode (every {interval}s). Ctrl-C to stop.")
    while True:
        try:
            run_agent()
        except KeyboardInterrupt:
            print("[agent] stopping.")
            return
        except Exception as exc:  # never let one bad scan kill the daemon
            print(f"[agent] scan failed: {exc}")
        time.sleep(interval)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RootMedic log-analysis agent.")
    parser.add_argument(
        "--loop", action="store_true",
        help="Run continuously (for the systemd service) instead of a single pass.",
    )
    parser.add_argument(
        "--interval", type=int, default=60,
        help="Seconds between scans in --loop mode (default: 60).",
    )
    args = parser.parse_args()

    if args.loop:
        run_loop(args.interval)
    else:
        run_agent()
