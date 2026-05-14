"""Slack alerting for RootMedic human-intervention events.

Sends alerts to Slack when an issue requires human approval, including:
- Error summary and timestamp
- Grafana dashboard link
- LLM root cause analysis
- Proposed remediation commands
- Deduplication counter and silence window

Deduplication state persists in SQLite across restarts.
"""

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ALERTS_CONFIG = Path("alerts.yml")
DB_PATH = Path("alerts_state.db")
DEFAULT_DEDUP_WINDOW_MINUTES = 15
DEFAULT_ESCALATION_AFTER_MINUTES = 30
DEFAULT_GRAFANA_BASE_URL = "http://localhost:3000"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class AlertConfig:
    """Configuration for alerting behavior."""

    slack_webhook_url: Optional[str] = None
    dedup_window_minutes: int = DEFAULT_DEDUP_WINDOW_MINUTES
    escalation_after_minutes: int = DEFAULT_ESCALATION_AFTER_MINUTES
    grafana_base_url: str = DEFAULT_GRAFANA_BASE_URL

    @classmethod
    def load(cls) -> "AlertConfig":
        """Load from alerts.yml or environment variables."""
        config: dict[str, Any] = {}

        # Try to load from YAML file
        if ALERTS_CONFIG.exists():
            try:
                import yaml
                with open(ALERTS_CONFIG) as f:
                    yaml_config = yaml.safe_load(f) or {}
                    config.update(yaml_config)
            except ImportError:
                # Fallback: parse simple key: value format
                for line in ALERTS_CONFIG.read_text().splitlines():
                    if ":" in line and not line.strip().startswith("#"):
                        key, _, value = line.partition(":")
                        config[key.strip()] = value.strip().strip('"\'')

        # Environment variables override file config
        config["slack_webhook_url"] = (
            os.environ.get("SLACK_WEBHOOK_URL")
            or config.get("slack_webhook_url")
        )
        config["grafana_base_url"] = (
            os.environ.get("GRAFANA_BASE_URL")
            or config.get("grafana_base_url", DEFAULT_GRAFANA_BASE_URL)
        )

        return cls(
            slack_webhook_url=config.get("slack_webhook_url"),
            dedup_window_minutes=int(config.get("dedup_window_minutes", DEFAULT_DEDUP_WINDOW_MINUTES)),
            escalation_after_minutes=int(config.get("escalation_after_minutes", DEFAULT_ESCALATION_AFTER_MINUTES)),
            grafana_base_url=config.get("grafana_base_url", DEFAULT_GRAFANA_BASE_URL),
        )


@dataclass
class AlertPayload:
    """Data needed to construct an alert."""

    fingerprint: str
    error_summary: str
    timestamp: float
    grafana_dashboard_uid: str = "system-logs"  # default Loki dashboard
    llm_root_cause: str = ""
    proposed_remediation: str = ""
    autonomy_level: str = "RECOMMEND"
    occurrence_count: int = 1


# ---------------------------------------------------------------------------
# SQLite-backed deduplication state
# ---------------------------------------------------------------------------

def init_alerts_db() -> None:
    """Initialize the alerts state database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alert_history (
            fingerprint TEXT PRIMARY KEY,
            last_alert_time REAL,
            alert_count INTEGER DEFAULT 1,
            last_escalation_time REAL,
            resolved INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def get_alert_state(fingerprint: str) -> dict[str, Any]:
    """Get the current state for a fingerprint."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM alert_history WHERE fingerprint = ?",
        (fingerprint,)
    )
    row = cursor.fetchone()
    conn.close()

    if row:
        return dict(row)
    return {
        "fingerprint": fingerprint,
        "last_alert_time": None,
        "alert_count": 0,
        "last_escalation_time": None,
        "resolved": 0,
    }


def update_alert_state(
    fingerprint: str,
    alert_time: float,
    escalation_time: Optional[float] = None,
    reset: bool = False,
) -> None:
    """Update or insert alert state for a fingerprint."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if reset:
        cursor.execute(
            "DELETE FROM alert_history WHERE fingerprint = ?",
            (fingerprint,)
        )
    else:
        # Get current count
        cursor.execute(
            "SELECT alert_count FROM alert_history WHERE fingerprint = ?",
            (fingerprint,)
        )
        row = cursor.fetchone()
        count = (row[0] + 1) if row else 1

        cursor.execute("""
            INSERT OR REPLACE INTO alert_history
            (fingerprint, last_alert_time, alert_count, last_escalation_time, resolved)
            VALUES (?, ?, ?, ?, 0)
        """, (fingerprint, alert_time, count, escalation_time))

    conn.commit()
    conn.close()


def mark_resolved(fingerprint: str) -> None:
    """Mark an issue as resolved, resetting dedup state."""
    update_alert_state(fingerprint, 0, reset=True)


# ---------------------------------------------------------------------------
# Slack notification
# ---------------------------------------------------------------------------

def send_slack_message(webhook_url: str, blocks: list[dict[str, Any]]) -> bool:
    """Send a Slack message via incoming webhook.

    Returns True if successful, False otherwise.
    """
    payload = {"blocks": blocks}

    try:
        response = requests.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"[ALERT] Slack webhook failed: {e}")
        return False


def build_alert_blocks(payload: AlertPayload, config: AlertConfig) -> list[dict[str, Any]]:
    """Build Slack block kit message for an alert."""
    now = datetime.fromtimestamp(payload.timestamp)
    time_str = now.strftime("%Y-%m-%d %H:%M:%S")

    # Determine if this is an escalation
    state = get_alert_state(payload.fingerprint)
    is_escalation = False
    if state["last_alert_time"]:
        elapsed_minutes = (payload.timestamp - state["last_alert_time"]) / 60
        if elapsed_minutes >= config.escalation_after_minutes:
            is_escalation = True

    blocks: list[dict[str, Any]] = []

    # Header
    header_text = (
        "[ESCALATION] Human Intervention Required" if is_escalation
        else "Human Intervention Required"
    )
    blocks.append({
        "type": "header",
        "text": {"type": "plain_text", "text": header_text, "emoji": True},
    })

    # Error summary
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*Error:* {payload.error_summary}",
        },
    })

    # Metadata
    blocks.append({
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": f"*Time:* {time_str}"},
            {"type": "mrkdwn", "text": f"*Occurrences:* {payload.occurrence_count}"},
            {"type": "mrkdwn", "text": f"*Autonomy Level:* {payload.autonomy_level}"},
            {"type": "mrkdwn", "text": f"*Fingerprint:* `{payload.fingerprint}`"},
        ],
    })

    # Root cause analysis
    if payload.llm_root_cause:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Root Cause Analysis:*\n{payload.llm_root_cause}",
            },
        })

    # Proposed remediation
    if payload.proposed_remediation:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Proposed Remediation:*\n```\n{payload.proposed_remediation}\n```",
            },
        })

    # Grafana link
    grafana_url = f"{config.grafana_base_url}/d/{payload.grafana_dashboard_uid}"
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"<{grafana_url}|:bar_chart: View Grafana Dashboard>",
        },
    })

    # Dedup info
    dedup_until = payload.timestamp + (config.dedup_window_minutes * 60)
    dedup_str = datetime.fromtimestamp(dedup_until).strftime("%H:%M:%S")
    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": f"Silenced until {dedup_str} if same issue recurs (dedup window: {config.dedup_window_minutes} min)",
            },
        ],
    })

    return blocks


# ---------------------------------------------------------------------------
# AlertManager
# ---------------------------------------------------------------------------

class AlertManager:
    """Manages Slack alerts with deduplication and escalation."""

    def __init__(self, config: Optional[AlertConfig] = None) -> None:
        self.config = config or AlertConfig.load()
        init_alerts_db()

    def should_send_alert(self, fingerprint: str) -> tuple[bool, bool]:
        """Check if an alert should be sent.

        Returns (should_send, is_escalation).
        """
        if not self.config.slack_webhook_url:
            return False, False

        state = get_alert_state(fingerprint)
        now = time.time()

        # First alert for this fingerprint
        if state["last_alert_time"] is None:
            return True, False

        # Check if resolved
        if state["resolved"]:
            return True, False

        # Check dedup window
        elapsed_minutes = (now - state["last_alert_time"]) / 60
        if elapsed_minutes < self.config.dedup_window_minutes:
            # Still in dedup window - suppress
            return False, False

        # Check escalation
        if elapsed_minutes >= self.config.escalation_after_minutes:
            return True, True

        # Outside dedup but before escalation - send normal alert
        return True, False

    def send_alert(self, payload: AlertPayload) -> bool:
        """Send an alert if not suppressed by deduplication.

        Returns True if alert was sent successfully.
        """
        should_send, is_escalation = self.should_send_alert(payload.fingerprint)

        if not should_send:
            print(f"[ALERT] Suppressed (dedup window active) for {payload.fingerprint}")
            return False

        # Update payload for escalation
        if is_escalation:
            payload.autonomy_level = "ESCALATION"

        # Build and send
        blocks = build_alert_blocks(payload, self.config)
        success = send_slack_message(self.config.slack_webhook_url, blocks)

        if success:
            now = time.time()
            escalation_time = now if is_escalation else None
            update_alert_state(payload.fingerprint, now, escalation_time)
            print(f"[ALERT] Sent to Slack for {payload.fingerprint}")
        else:
            print(f"[ALERT] Failed to send for {payload.fingerprint}")

        return success

    def send_test_alert(self) -> bool:
        """Send a test alert to verify Slack integration."""
        payload = AlertPayload(
            fingerprint="test-" + hashlib.sha256(str(time.time()).encode()).hexdigest()[:8],
            error_summary="Test alert - verify Slack integration",
            timestamp=time.time(),
            llm_root_cause="This is a test message to verify the alerting system is working correctly.",
            proposed_remediation="echo 'No action needed - this is a test'",
            autonomy_level="TEST",
            occurrence_count=1,
        )
        return self.send_alert(payload)

    def mark_issue_resolved(self, fingerprint: str) -> None:
        """Mark an issue as resolved, resetting dedup state."""
        mark_resolved(fingerprint)
        print(f"[ALERT] Marked {fingerprint} as resolved")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RootMedic Alerting System")
    parser.add_argument(
        "--test",
        action="store_true",
        help="Send a test alert to Slack",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to alerts.yml config file",
    )
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Print current configuration",
    )

    args = parser.parse_args()

    if args.config:
        global ALERTS_CONFIG
        ALERTS_CONFIG = Path(args.config)

    config = AlertConfig.load()

    if args.show_config:
        print("Current Alert Configuration:")
        print(f"  Slack Webhook: {'configured' if config.slack_webhook_url else 'NOT SET'}")
        print(f"  Dedup Window: {config.dedup_window_minutes} minutes")
        print(f"  Escalation After: {config.escalation_after_minutes} minutes")
        print(f"  Grafana Base URL: {config.grafana_base_url}")
    elif args.test:
        if not config.slack_webhook_url:
            print("Error: SLACK_WEBHOOK_URL not set")
            exit(1)
        manager = AlertManager(config)
        success = manager.send_test_alert()
        exit(0 if success else 1)
    else:
        parser.print_help()
