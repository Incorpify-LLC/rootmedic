"""Tests for the alerting module."""

import os
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from alerting import (
    AlertConfig,
    AlertManager,
    AlertPayload,
    ALERTS_CONFIG,
    DB_PATH,
    build_alert_blocks,
    get_alert_state,
    init_alerts_db,
    mark_resolved,
    update_alert_state,
)


@pytest.fixture(autouse=True)
def clean_db():
    """Clean up the alerts database before and after each test."""
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_alerts_db()
    yield
    if DB_PATH.exists():
        DB_PATH.unlink()


@pytest.fixture
def sample_config():
    """Return a test configuration."""
    return AlertConfig(
        slack_webhook_url="https://hooks.slack.com/test",
        dedup_window_minutes=15,
        escalation_after_minutes=30,
        grafana_base_url="http://localhost:3000",
    )


@pytest.fixture
def sample_payload():
    """Return a test alert payload."""
    return AlertPayload(
        fingerprint="test-fp-123",
        error_summary="nginx failed to start",
        timestamp=time.time(),
        llm_root_cause="Configuration syntax error in nginx.conf",
        proposed_remediation="systemctl restart nginx",
        autonomy_level="RECOMMEND",
        occurrence_count=1,
    )


class TestAlertConfig:
    """Tests for AlertConfig loading."""

    def test_load_from_env(self):
        """Config loads from environment variable."""
        with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/env"}):
            config = AlertConfig.load()
            assert config.slack_webhook_url == "https://hooks.slack.com/env"

    def test_env_overrides_file(self, tmp_path):
        """Environment variable overrides file config."""
        config_file = tmp_path / "alerts.yml"
        config_file.write_text('slack_webhook_url: "https://hooks.slack.com/file"')

        with patch.object(__import__("alerting"), "ALERTS_CONFIG", config_file):
            with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/env"}):
                config = AlertConfig.load()
                assert config.slack_webhook_url == "https://hooks.slack.com/env"

    def test_defaults(self):
        """Default values are applied when no config exists."""
        config = AlertConfig.load()
        assert config.dedup_window_minutes == 15
        assert config.escalation_after_minutes == 30
        assert config.grafana_base_url == "http://localhost:3000"


class TestAlertState:
    """Tests for SQLite-backed alert state."""

    def test_init_creates_table(self):
        """Database initialization creates the table."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='alert_history'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_get_state_new_fingerprint(self):
        """New fingerprint returns empty state."""
        state = get_alert_state("new-fp")
        assert state["fingerprint"] == "new-fp"
        assert state["last_alert_time"] is None
        assert state["alert_count"] == 0

    def test_update_state_inserts(self):
        """Update inserts new record."""
        now = time.time()
        update_alert_state("fp-1", now)

        state = get_alert_state("fp-1")
        assert state["last_alert_time"] == now
        assert state["alert_count"] == 1

    def test_update_state_increments_count(self):
        """Subsequent updates increment count."""
        update_alert_state("fp-2", time.time())
        update_alert_state("fp-2", time.time() + 100)

        state = get_alert_state("fp-2")
        assert state["alert_count"] == 2

    def test_mark_resolved_deletes(self):
        """Marking resolved resets state."""
        update_alert_state("fp-3", time.time())
        mark_resolved("fp-3")

        state = get_alert_state("fp-3")
        assert state["last_alert_time"] is None
        assert state["alert_count"] == 0


class TestBuildAlertBlocks:
    """Tests for Slack block kit message building."""

    def test_basic_blocks(self, sample_payload, sample_config):
        """Basic blocks are created correctly."""
        blocks = build_alert_blocks(sample_payload, sample_config)

        assert len(blocks) >= 5
        assert blocks[0]["type"] == "header"
        assert "Human Intervention Required" in blocks[0]["text"]["text"]

    def test_includes_root_cause(self, sample_payload, sample_config):
        """Root cause analysis is included."""
        blocks = build_alert_blocks(sample_payload, sample_config)

        rc_block = next(
            b for b in blocks
            if b.get("text", {}).get("text", "").startswith("*Root Cause Analysis:*")
        )
        assert rc_block is not None
        assert "Configuration syntax error" in rc_block["text"]["text"]

    def test_includes_grafana_link(self, sample_payload, sample_config):
        """Grafana dashboard link is included."""
        blocks = build_alert_blocks(sample_payload, sample_config)

        link_block = next(
            b for b in blocks
            if "View Grafana Dashboard" in str(b)
        )
        assert link_block is not None
        assert "http://localhost:3000/d/system-logs" in str(link_block)

    def test_dedup_info(self, sample_payload, sample_config):
        """Deduplication info is in context block."""
        blocks = build_alert_blocks(sample_payload, sample_config)

        context_block = next(
            b for b in blocks if b["type"] == "context"
        )
        assert "Silenced until" in context_block["elements"][0]["text"]
        assert "15 min" in context_block["elements"][0]["text"]


class TestAlertManager:
    """Tests for AlertManager."""

    def test_no_webhook_suppressed(self, sample_payload):
        """Alerts suppressed when no webhook configured."""
        config = AlertConfig(slack_webhook_url=None)
        manager = AlertManager(config)

        should_send, _ = manager.should_send_alert(sample_payload.fingerprint)
        assert should_send is False

    def test_first_alert_sends(self, sample_payload, sample_config):
        """First alert for a fingerprint sends."""
        manager = AlertManager(sample_config)
        should_send, is_escalation = manager.should_send_alert(sample_payload.fingerprint)

        assert should_send is True
        assert is_escalation is False

    def test_dedup_window_suppresses(self, sample_payload, sample_config):
        """Alerts within dedup window are suppressed."""
        manager = AlertManager(sample_config)

        # First alert
        manager.send_alert(sample_payload)

        # Immediate second alert should be suppressed
        should_send, _ = manager.should_send_alert(sample_payload.fingerprint)
        assert should_send is False

    def test_escalation_after_timeout(self, sample_payload, sample_config):
        """Escalation triggers after configured timeout."""
        # Set up state with old alert time
        old_time = time.time() - (40 * 60)  # 40 minutes ago
        update_alert_state(sample_payload.fingerprint, old_time)

        manager = AlertManager(sample_config)
        should_send, is_escalation = manager.should_send_alert(sample_payload.fingerprint)

        assert should_send is True
        assert is_escalation is True

    @patch("alerting.send_slack_message")
    def test_send_alert_updates_state(self, mock_send, sample_payload, sample_config):
        """Sending alert updates state."""
        mock_send.return_value = True
        manager = AlertManager(sample_config)

        manager.send_alert(sample_payload)

        state = get_alert_state(sample_payload.fingerprint)
        assert state["last_alert_time"] is not None
        assert state["alert_count"] == 1

    def test_mark_resolved_resets_dedup(self, sample_payload, sample_config):
        """Marking resolved resets dedup state."""
        manager = AlertManager(sample_config)
        manager.send_alert(sample_payload)
        manager.mark_issue_resolved(sample_payload.fingerprint)

        # Should be able to alert again immediately
        should_send, _ = manager.should_send_alert(sample_payload.fingerprint)
        assert should_send is True


class TestIntegration:
    """Integration tests with mocked Slack."""

    @patch("alerting.send_slack_message")
    def test_full_alert_flow(self, mock_send, sample_config):
        """Complete alert flow from trigger to resolution."""
        mock_send.return_value = True
        manager = AlertManager(sample_config)

        payload = AlertPayload(
            fingerprint="integration-test",
            error_summary="Test error",
            timestamp=time.time(),
            llm_root_cause="Test root cause",
            proposed_remediation="echo test",
        )

        # First alert - sends
        assert manager.send_alert(payload) is True

        # Second alert within dedup - suppressed
        payload.occurrence_count = 2
        payload.timestamp = time.time()
        assert manager.send_alert(payload) is False

        # Mark resolved
        manager.mark_issue_resolved(payload.fingerprint)

        # Third alert after resolution - sends again
        payload.occurrence_count = 3
        payload.timestamp = time.time()
        assert manager.send_alert(payload) is True
