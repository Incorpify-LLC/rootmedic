"""Alert manager for RootMedic human-intervention events.

The manager owns:

* Configuration loading (``alerts.yml`` + environment variables).
* SQLite-backed deduplication / escalation state (``alerts_state.db``).
* Fan-out to every configured plugin from :mod:`alert_plugins`.

Channel-specific formatting and transport live in :mod:`alert_plugins`. This
module is intentionally channel-agnostic — adding email, IRC or PagerDuty is
a matter of dropping another :class:`alert_plugins.AlertPlugin` subclass into
the registry, not editing :class:`AlertManager`.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from alert_plugins import (
    AlertPayload,
    AlertPlugin,
    SlackPlugin,
    WebhookPlugin,
    build_default_plugins,
    build_slack_blocks,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ALERTS_CONFIG = Path("alerts.yml")
DB_PATH = Path("alerts_state.db")
DEFAULT_DEDUP_WINDOW_MINUTES = 15
DEFAULT_ESCALATION_AFTER_MINUTES = 30
DEFAULT_GRAFANA_BASE_URL = "http://localhost:3000"


@dataclass
class AlertConfig:
    """Configuration for alerting behavior."""

    slack_webhook_url: Optional[str] = None
    webhook_url: Optional[str] = None
    webhook_headers: Optional[dict[str, str]] = None
    dedup_window_minutes: int = DEFAULT_DEDUP_WINDOW_MINUTES
    escalation_after_minutes: int = DEFAULT_ESCALATION_AFTER_MINUTES
    grafana_base_url: str = DEFAULT_GRAFANA_BASE_URL

    @classmethod
    def load(cls) -> "AlertConfig":
        """Load from ``alerts.yml`` and overlay environment variables."""
        config: dict[str, Any] = {}

        if ALERTS_CONFIG.exists():
            try:
                import yaml
                with open(ALERTS_CONFIG) as f:
                    config.update(yaml.safe_load(f) or {})
            except ImportError:
                for line in ALERTS_CONFIG.read_text().splitlines():
                    if ":" in line and not line.strip().startswith("#"):
                        key, _, value = line.partition(":")
                        config[key.strip()] = value.strip().strip('"\'')

        config["slack_webhook_url"] = (
            os.environ.get("SLACK_WEBHOOK_URL")
            or config.get("slack_webhook_url")
        )
        config["webhook_url"] = (
            os.environ.get("ALERT_WEBHOOK_URL")
            or config.get("webhook_url")
        )
        config["grafana_base_url"] = (
            os.environ.get("GRAFANA_BASE_URL")
            or config.get("grafana_base_url", DEFAULT_GRAFANA_BASE_URL)
        )

        webhook_headers = config.get("webhook_headers") or None
        if isinstance(webhook_headers, dict):
            webhook_headers = {str(k): str(v) for k, v in webhook_headers.items()}
        else:
            webhook_headers = None

        return cls(
            slack_webhook_url=config.get("slack_webhook_url"),
            webhook_url=config.get("webhook_url"),
            webhook_headers=webhook_headers,
            dedup_window_minutes=int(config.get("dedup_window_minutes", DEFAULT_DEDUP_WINDOW_MINUTES)),
            escalation_after_minutes=int(config.get("escalation_after_minutes", DEFAULT_ESCALATION_AFTER_MINUTES)),
            grafana_base_url=config.get("grafana_base_url", DEFAULT_GRAFANA_BASE_URL),
        )


# ---------------------------------------------------------------------------
# SQLite-backed deduplication state
# ---------------------------------------------------------------------------


def init_alerts_db() -> None:
    """Initialise the alerts state database."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
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
    """Return the persisted alert state for ``fingerprint`` (defaults if absent)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM alert_history WHERE fingerprint = ?", (fingerprint,)
    ).fetchone()
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
    """Insert or update the alert-state row for a fingerprint."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if reset:
        cursor.execute("DELETE FROM alert_history WHERE fingerprint = ?", (fingerprint,))
    else:
        row = cursor.execute(
            "SELECT alert_count FROM alert_history WHERE fingerprint = ?",
            (fingerprint,)
        ).fetchone()
        count = (row[0] + 1) if row else 1
        cursor.execute("""
            INSERT OR REPLACE INTO alert_history
            (fingerprint, last_alert_time, alert_count, last_escalation_time, resolved)
            VALUES (?, ?, ?, ?, 0)
        """, (fingerprint, alert_time, count, escalation_time))

    conn.commit()
    conn.close()


def mark_resolved(fingerprint: str) -> None:
    """Reset dedup state for an issue once it has been confirmed fixed."""
    update_alert_state(fingerprint, 0, reset=True)


# ---------------------------------------------------------------------------
# Back-compat helpers
# ---------------------------------------------------------------------------


def build_alert_blocks(payload: AlertPayload, config: AlertConfig) -> list[dict[str, Any]]:
    """Back-compat shim: build Slack blocks via :func:`alert_plugins.build_slack_blocks`.

    Determines escalation by looking at the persisted state, the same way the
    pre-refactor implementation did.
    """
    state = get_alert_state(payload.fingerprint)
    is_escalation = False
    if state["last_alert_time"]:
        elapsed_minutes = (payload.timestamp - state["last_alert_time"]) / 60
        if elapsed_minutes >= config.escalation_after_minutes:
            is_escalation = True
    return build_slack_blocks(
        payload, config.grafana_base_url, config.dedup_window_minutes, is_escalation,
    )


def send_slack_message(webhook_url: str, blocks: list[dict[str, Any]]) -> bool:
    """Back-compat shim used by older callers and tests."""
    import requests
    try:
        response = requests.post(
            webhook_url,
            json={"blocks": blocks},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        print(f"[ALERT] Slack webhook failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# AlertManager
# ---------------------------------------------------------------------------


class AlertManager:
    """Manages alerts with deduplication, escalation, and plugin fan-out."""

    def __init__(
        self,
        config: Optional[AlertConfig] = None,
        plugins: Optional[list[AlertPlugin]] = None,
    ) -> None:
        self.config = config or AlertConfig.load()
        self.plugins: list[AlertPlugin] = (
            plugins if plugins is not None else build_default_plugins(self.config)
        )
        init_alerts_db()

    # -- dedup logic ------------------------------------------------------

    def should_send_alert(self, fingerprint: str) -> tuple[bool, bool]:
        """Decide whether to send. Returns ``(should_send, is_escalation)``."""
        if not self.plugins:
            return False, False

        state = get_alert_state(fingerprint)
        now = time.time()

        if state["last_alert_time"] is None:
            return True, False
        if state["resolved"]:
            return True, False

        elapsed_minutes = (now - state["last_alert_time"]) / 60
        if elapsed_minutes < self.config.dedup_window_minutes:
            return False, False
        if elapsed_minutes >= self.config.escalation_after_minutes:
            return True, True
        return True, False

    # -- send -------------------------------------------------------------

    def send_alert(self, payload: AlertPayload) -> bool:
        """Send via every configured plugin. Returns True if **any** succeeded."""
        should_send, is_escalation = self.should_send_alert(payload.fingerprint)

        if not should_send:
            print(f"[ALERT] Suppressed (dedup window active) for {payload.fingerprint}")
            return False

        if is_escalation:
            payload.autonomy_level = "ESCALATION"

        any_success = False
        for plugin in self.plugins:
            try:
                ok = plugin.send(payload, is_escalation=is_escalation)
            except Exception as exc:  # one plugin failing must not break others
                print(f"[ALERT][{plugin.name}] raised: {exc}")
                ok = False
            any_success = any_success or ok
            print(f"[ALERT][{plugin.name}] {'sent' if ok else 'failed'} for {payload.fingerprint}")

        if any_success:
            now = time.time()
            update_alert_state(payload.fingerprint, now, now if is_escalation else None)
        return any_success

    def send_test_alert(self) -> bool:
        """Send a synthetic alert through every plugin (smoke test)."""
        payload = AlertPayload(
            fingerprint="test-" + hashlib.sha256(str(time.time()).encode()).hexdigest()[:8],
            error_summary="Test alert - verify channels",
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
    parser.add_argument("--test", action="store_true", help="Send a test alert")
    parser.add_argument("--config", type=str, default=None, help="Path to alerts.yml")
    parser.add_argument("--show-config", action="store_true", help="Print current configuration")
    args = parser.parse_args()

    if args.config:
        ALERTS_CONFIG = Path(args.config)

    config = AlertConfig.load()

    if args.show_config:
        print("Current Alert Configuration:")
        print(f"  Slack Webhook : {'configured' if config.slack_webhook_url else 'NOT SET'}")
        print(f"  Generic Webhook: {'configured' if config.webhook_url else 'NOT SET'}")
        print(f"  Dedup Window  : {config.dedup_window_minutes} minutes")
        print(f"  Escalation    : {config.escalation_after_minutes} minutes")
        print(f"  Grafana Base  : {config.grafana_base_url}")
    elif args.test:
        manager = AlertManager(config)
        if not manager.plugins:
            print("Error: no alert plugins configured (set SLACK_WEBHOOK_URL or ALERT_WEBHOOK_URL)")
            exit(1)
        success = manager.send_test_alert()
        exit(0 if success else 1)
    else:
        parser.print_help()
