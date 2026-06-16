#!/usr/bin/env python3
"""
RootMedic Autonomous Healing Demo

One-shot demo that:
1. Starts Podman stack (Loki + Promtail + Grafana)
2. Injects error scenarios into Loki
3. Runs agent with auto-apply mode
4. Verifies healing and prints summary
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

COMPOSE_FILE = Path("Deployment/docker-compose.yml")
LOKI_URL = "http://localhost:3100"
LOKI_PUSH_URL = f"{LOKI_URL}/loki/api/v1/push"
LOKI_QUERY_URL = f"{LOKI_URL}/loki/api/v1/query_range"
GRAFANA_URL = "http://localhost:3000"

# Stack health check settings
HEALTH_CHECK_TIMEOUT = 60  # seconds
HEALTH_CHECK_INTERVAL = 2  # seconds

# Agent loop settings
AGENT_POLL_INTERVAL = 5  # seconds between agent runs
MAX_AGENT_RUNS_PER_SCENARIO = 6  # max agent loops per scenario

# Demo scenarios
SCENARIOS = {
    "service_crash": {
        "name": "Service Crash (nginx)",
        "log_message": "nginx[1234]: worker process 5678 exited on signal 9",
        "unit": "nginx.service",
        "expected_commands": ["systemctl restart nginx"],
        "description": "nginx worker crashed, needs service restart",
    },
    "oom_kill": {
        "name": "OOM Kill / Memory Pressure",
        "log_message": "Out of memory: Killed process 9999 (java)",
        "unit": "java.service",
        "expected_commands": ["systemctl restart java.service", "sync && echo 3 > /proc/sys/vm/drop_caches"],
        "description": "Process killed by OOM killer, needs restart + cache drop",
    },
    "disk_full": {
        "name": "Disk Full / No Space Left",
        "log_message": "no space left on device",
        "unit": "systemd-journald.service",
        "expected_commands": ["journalctl --vacuum-size=200M", "apt-get clean"],
        "description": "Disk full, needs log cleanup and apt cache clean",
    },
    "connection_refused": {
        "name": "Connection Refused (upstream)",
        "log_message": "connect() to 10.0.0.5:8080 failed (111: Connection refused)",
        "unit": "nginx.service",
        "expected_commands": ["systemctl restart nginx"],
        "description": "Upstream connection refused, restart service",
    },
}


# ---------------------------------------------------------------------------
# Podman Stack Management
# ---------------------------------------------------------------------------


def run_podman_compose(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run podman-compose command."""
    # Use absolute path for compose file to avoid podman-compose path resolution issues
    compose_file_abs = COMPOSE_FILE.resolve()
    cmd = ["podman-compose", "-f", str(compose_file_abs), *args]
    return subprocess.run(cmd, cwd=cwd or compose_file_abs.parent, capture_output=True, text=True)


def start_stack() -> bool:
    """Start the Podman stack (Loki, Promtail, Grafana)."""
    print("[demo] Starting Podman stack...")
    result = run_podman_compose(["up", "-d"])
    if result.returncode != 0:
        print(f"[demo] Failed to start stack: {result.stderr}")
        return False
    print("[demo] Stack started, waiting for services to be healthy...")
    return wait_for_health()


def stop_stack() -> bool:
    """Stop the Podman stack."""
    print("[demo] Stopping Podman stack...")
    result = run_podman_compose(["down"])
    if result.returncode != 0:
        print(f"[demo] Failed to stop stack: {result.stderr}")
        return False
    print("[demo] Stack stopped.")
    return True


def wait_for_health() -> bool:
    """Wait for Loki and Grafana to be healthy."""
    start_time = time.time()
    loki_ready = False
    grafana_ready = False

    while time.time() - start_time < HEALTH_CHECK_TIMEOUT:
        # Check Loki
        if not loki_ready:
            try:
                resp = requests.get(f"{LOKI_URL}/ready", timeout=2)
                if resp.status_code == 200:
                    loki_ready = True
                    print("[demo] Loki is ready")
            except requests.RequestException:
                pass

        # Check Grafana
        if not grafana_ready:
            try:
                resp = requests.get(f"{GRAFANA_URL}/api/health", timeout=2)
                if resp.status_code == 200:
                    grafana_ready = True
                    print("[demo] Grafana is ready")
            except requests.RequestException:
                pass

        if loki_ready and grafana_ready:
            print("[demo] All services healthy!")
            return True

        time.sleep(HEALTH_CHECK_INTERVAL)

    print("[demo] Timeout waiting for services to be healthy")
    if not loki_ready:
        print("[demo] Loki not ready")
    if not grafana_ready:
        print("[demo] Grafana not ready")
    return False


# ---------------------------------------------------------------------------
# Loki Log Injection
# ---------------------------------------------------------------------------


def push_log_to_loki(message: str, unit: str, host: str = "demo-host") -> bool:
    """Push a synthetic log entry to Loki via push API."""
    timestamp_ns = str(int(time.time() * 1e9))
    payload = {
        "streams": [
            {
                "stream": {
                    "job": "systemd-journal",
                    "host": host,
                    "systemd_unit": unit,
                },
                "values": [[timestamp_ns, message]],
            }
        ]
    }

    try:
        resp = requests.post(LOKI_PUSH_URL, json=payload, timeout=5)
        resp.raise_for_status()
        print(f"[demo] Injected log: {message[:60]}... (unit={unit})")
        return True
    except requests.RequestException as e:
        print(f"[demo] Failed to push log to Loki: {e}")
        return False


# ---------------------------------------------------------------------------
# Agent Auto-Apply Loop
# ---------------------------------------------------------------------------


def run_agent_auto_apply(max_runs: int = MAX_AGENT_RUNS_PER_SCENARIO) -> list[dict[str, Any]]:
    """
    Run the agent loop with auto-apply mode.
    Returns list of recommendations/results.
    """
    # Import here to avoid circular imports
    from fetch_normalize_logs import run_agent
    from remediation_engine import RemediationEngine, AutonomyLevel
    from vector_store import VectorStore
    from fingerprint import fingerprint_issue

    engine = RemediationEngine()
    store = VectorStore()

    # Load LLM config
    try:
        from llm_client import load_config as load_llm_config, propose_plan as llm_propose_plan
        llm_config = load_llm_config()
    except ImportError:
        llm_config = None
        llm_propose_plan = None

    results = []

    for run in range(max_runs):
        print(f"[demo] Agent run {run + 1}/{max_runs}...")

        # We need to replicate run_agent logic but with auto-apply
        # Let's call the internal functions
        from ingest import fetch_logs, parse_and_normalize
        from redactor import redact_event

        raw_logs = fetch_logs()
        events = parse_and_normalize(raw_logs)

        if not events:
            print("[demo] No error/warning events found.")
            time.sleep(AGENT_POLL_INTERVAL)
            continue

        events = [redact_event(ev) for ev in events]

        for event in events:
            # Resolve plan (cache -> rule -> LLM)
            from fetch_normalize_logs import build_remediation_plan, _plan_from_known_issue

            known = store.lookup(event["message"], event["unit"])
            if known is not None:
                plan = _plan_from_known_issue(event, known)
                source = "cached"
            else:
                plan = build_remediation_plan(event, engine)
                if plan is not None:
                    source = "rule"
                elif llm_config is not None and llm_propose_plan is not None:
                    plan = llm_propose_plan(event, llm_config)
                    source = "llm" if plan else "none"
                else:
                    plan = None
                    source = "none"

            if plan is None:
                continue

            level, record, confidence = engine.assess(event["message"], event["unit"])
            plan.confidence = confidence

            print(
                f"[demo] {event['timestamp']} {event['host']} {event['unit']} "
                f"-> {level.value.upper()} (conf={confidence:.0%}, seen={record.occurrences}x, source={source})"
            )

            # AUTO-APPLY: Call recommend then apply
            recommendation = engine.recommend(plan, level)
            recommendation["event"] = event
            recommendation["source"] = source
            results.append(recommendation)

            # For demo, auto-apply if VALIDATED or force for demo
            if level == AutonomyLevel.VALIDATED or os.environ.get("DEMO_FORCE_APPLY") == "1":
                print(f"[demo] AUTO-APPLYING fix for {plan.description}")
                apply_result = engine.apply(plan)
                print(f"[demo] Apply result: {apply_result['status']}")
                recommendation["apply_result"] = apply_result

                # Alerting (optional)
                try:
                    from alerting import AlertManager, AlertPayload
                    alert_manager = AlertManager()
                    if alert_manager.plugins:
                        payload = AlertPayload(
                            fingerprint=plan.issue_fingerprint,
                            error_summary=event["message"][:200],
                            timestamp=time.time(),
                            proposed_remediation="\n".join(plan.commands),
                            autonomy_level=level.value.upper(),
                            occurrence_count=record.occurrences,
                            host=event.get("host", ""),
                            unit=event.get("unit", ""),
                        )
                        alert_manager.send_alert(payload)
                except Exception as e:
                    print(f"[demo] Alert failed (ok): {e}")

                # Archive
                try:
                    from archive import IncidentRecord, archive_incident
                    record_obj = IncidentRecord(
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
                    archive_incident(record_obj, client_id="demo", plan_yaml=plan.to_yaml())
                except Exception as e:
                    print(f"[demo] Archive failed (ok): {e}")

                # Learn if validated
                if level == AutonomyLevel.VALIDATED and source != "cached":
                    store.store(
                        event["message"], event["unit"],
                        description=plan.description,
                        commands=plan.commands,
                        rollback_commands=plan.rollback_commands,
                        source="learned",
                    )

        # Check if we've processed all events for this scenario
        if results:
            break

        time.sleep(AGENT_POLL_INTERVAL)

    return results


# ---------------------------------------------------------------------------
# Scenario Verification
# ---------------------------------------------------------------------------


def verify_healing(scenario_key: str, applied_commands: list[str]) -> tuple[bool, str]:
    """Verify that the healing commands match expected for the scenario."""
    scenario = SCENARIOS[scenario_key]
    expected = scenario["expected_commands"]

    # Check if all expected commands were run (order doesn't matter)
    expected_set = set(expected)
    applied_set = set(applied_commands)

    if expected_set.issubset(applied_set):
        return True, f"All expected commands executed: {expected}"
    else:
        missing = expected_set - applied_set
        return False, f"Missing expected commands: {missing}"


# ---------------------------------------------------------------------------
# Main Demo Runner
# ---------------------------------------------------------------------------


def run_scenario(scenario_key: str, force_apply: bool = False) -> dict[str, Any]:
    """Run a single demo scenario."""
    scenario = SCENARIOS[scenario_key]
    print(f"\n{'='*60}")
    print(f"SCENARIO: {scenario['name']}")
    print(f"DESCRIPTION: {scenario['description']}")
    print(f"{'='*60}")

    # Inject the error log
    if not push_log_to_loki(scenario["log_message"], scenario["unit"]):
        return {"scenario": scenario_key, "passed": False, "error": "Failed to inject log"}

    # Give Loki a moment to index
    time.sleep(2)

    # Run agent with auto-apply
    if force_apply:
        os.environ["DEMO_FORCE_APPLY"] = "1"

    results = run_agent_auto_apply()

    if force_apply:
        os.environ.pop("DEMO_FORCE_APPLY", None)

    if not results:
        return {"scenario": scenario_key, "passed": False, "error": "No agent results"}

    # Check apply results
    all_passed = True
    errors = []
    applied_commands = []

    for result in results:
        apply_result = result.get("apply_result")
        if apply_result:
            applied_commands.extend(apply_result.get("applied_commands", []))
            if apply_result["status"] != "applied":
                all_passed = False
                errors.append(f"Apply failed: {apply_result.get('failed_command', 'unknown')}")

    # Verify expected commands were run
    verified, verify_msg = verify_healing(scenario_key, applied_commands)
    if not verified:
        all_passed = False
        errors.append(verify_msg)
    else:
        print(f"[demo] Verification: {verify_msg}")

    return {
        "scenario": scenario_key,
        "passed": all_passed,
        "results": results,
        "applied_commands": applied_commands,
        "errors": errors,
    }


def run_all_scenarios(force_apply: bool = False) -> dict[str, Any]:
    """Run all demo scenarios sequentially."""
    print("\n" + "="*60)
    print("ROOTMEDIC AUTONOMOUS HEALING DEMO")
    print("="*60)

    # Start stack
    if not start_stack():
        return {"overall": False, "error": "Failed to start stack", "scenarios": []}

    try:
        scenario_results = []
        for scenario_key in SCENARIOS.keys():
            result = run_scenario(scenario_key, force_apply=force_apply)
            scenario_results.append(result)

            # Brief pause between scenarios
            time.sleep(3)

        # Summary
        print("\n" + "="*60)
        print("DEMO SUMMARY")
        print("="*60)

        all_passed = True
        for r in scenario_results:
            status = "PASS" if r["passed"] else "FAIL"
            print(f"  {r['scenario']:20s} : {status}")
            if not r["passed"]:
                all_passed = False
                for err in r.get("errors", []):
                    print(f"    - {err}")

        print("="*60)
        overall_status = "ALL SCENARIOS PASSED" if all_passed else "SOME SCENARIOS FAILED"
        print(f"  OVERALL: {overall_status}")

        return {
            "overall": all_passed,
            "scenarios": scenario_results,
        }

    finally:
        stop_stack()


def main():
    parser = argparse.ArgumentParser(description="RootMedic Autonomous Healing Demo")
    parser.add_argument("--scenario", choices=list(SCENARIOS.keys()) + ["all"],
                        default="all", help="Scenario to run")
    parser.add_argument("--force-apply", action="store_true",
                        help="Force auto-apply even for RECOMMEND level (demo mode)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show scenarios without running")
    parser.add_argument("--no-stack", action="store_true",
                        help="Don't start/stop Podman stack (assume already running)")

    args = parser.parse_args()

    if args.dry_run:
        print("Available scenarios:")
        for key, sc in SCENARIOS.items():
            print(f"  {key}: {sc['name']} - {sc['description']}")
        return 0

    if args.scenario == "all":
        if args.no_stack:
            # Run scenarios without stack management
            print("[demo] Skipping stack management (--no-stack)")
            scenario_results = []
            for key in SCENARIOS.keys():
                result = run_scenario(key, force_apply=args.force_apply)
                scenario_results.append(result)
                time.sleep(2)

            all_passed = all(r["passed"] for r in scenario_results)
            print("\nSUMMARY:")
            for r in scenario_results:
                status = "PASS" if r["passed"] else "FAIL"
                print(f"  {r['scenario']:20s} : {status}")
            return 0 if all_passed else 1
        else:
            result = run_all_scenarios(force_apply=args.force_apply)
            return 0 if result["overall"] else 1
    else:
        # Single scenario
        if not args.no_stack:
            if not start_stack():
                return 1
            try:
                result = run_scenario(args.scenario, force_apply=args.force_apply)
            finally:
                stop_stack()
        else:
            result = run_scenario(args.scenario, force_apply=args.force_apply)

        status = "PASS" if result["passed"] else "FAIL"
        print(f"\n{args.scenario}: {status}")
        if not result["passed"]:
            for err in result.get("errors", []):
                print(f"  - {err}")
        return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())