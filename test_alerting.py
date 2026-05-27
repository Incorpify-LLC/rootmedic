"""Tests for the alerting subsystem (manager + plugins + dedup state)."""

import os
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from alerting import (
    AlertConfig,
    AlertManager,
    ALERTS_CONFIG,
    DB_PATH,
    build_alert_blocks,
    get_alert_state,
    init_alerts_db,
    mark_resolved,
    update_alert_state,
)
from alert_plugins import (
    AlertPayload,
    AlertPlugin,
    SlackPlugin,
    WebhookPlugin,
    build_default_plugins,
    build_slack_blocks,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_db():
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_alerts_db()
    yield
    if DB_PATH.exists():
        DB_PATH.unlink()


@pytest.fixture
def sample_config():
    return AlertConfig(
        slack_webhook_url="https://hooks.slack.com/test",
        webhook_url=None,
        dedup_window_minutes=15,
        escalation_after_minutes=30,
        grafana_base_url="http://localhost:3000",
    )


@pytest.fixture
def sample_payload():
    return AlertPayload(
        fingerprint="test-fp-123",
        error_summary="nginx failed to start",
        timestamp=time.time(),
        llm_root_cause="Configuration syntax error in nginx.conf",
        proposed_remediation="systemctl restart nginx",
        autonomy_level="RECOMMEND",
        occurrence_count=1,
    )


class _RecordingPlugin(AlertPlugin):
    """Test plugin that records every call instead of doing network IO."""

    def __init__(self, name="record", succeed=True):
        self.name = name
        self.succeed = succeed
        self.calls: list[tuple[AlertPayload, bool]] = []

    def is_configured(self) -> bool:
        return True

    def send(self, payload, *, is_escalation=False):
        self.calls.append((payload, is_escalation))
        return self.succeed


# ---------------------------------------------------------------------------
# AlertConfig
# ---------------------------------------------------------------------------


class TestAlertConfig:
    def test_load_from_env(self):
        with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/env"}):
            config = AlertConfig.load()
            assert config.slack_webhook_url == "https://hooks.slack.com/env"

    def test_env_overrides_file(self, tmp_path):
        config_file = tmp_path / "alerts.yml"
        config_file.write_text('slack_webhook_url: "https://hooks.slack.com/file"')
        with patch.object(__import__("alerting"), "ALERTS_CONFIG", config_file):
            with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/env"}):
                config = AlertConfig.load()
                assert config.slack_webhook_url == "https://hooks.slack.com/env"

    def test_defaults(self):
        config = AlertConfig.load()
        assert config.dedup_window_minutes == 15
        assert config.escalation_after_minutes == 30
        assert config.grafana_base_url == "http://localhost:3000"

    def test_webhook_url_loads_from_env(self):
        with patch.dict(os.environ, {"ALERT_WEBHOOK_URL": "https://example.com/hook"}):
            config = AlertConfig.load()
            assert config.webhook_url == "https://example.com/hook"


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class TestAlertState:
    def test_init_creates_table(self):
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='alert_history'"
        ).fetchone()
        assert row is not None
        conn.close()

    def test_get_state_new_fingerprint(self):
        state = get_alert_state("new-fp")
        assert state["fingerprint"] == "new-fp"
        assert state["last_alert_time"] is None
        assert state["alert_count"] == 0

    def test_update_state_inserts(self):
        now = time.time()
        update_alert_state("fp-1", now)
        state = get_alert_state("fp-1")
        assert state["last_alert_time"] == now
        assert state["alert_count"] == 1

    def test_update_state_increments_count(self):
        update_alert_state("fp-2", time.time())
        update_alert_state("fp-2", time.time() + 100)
        assert get_alert_state("fp-2")["alert_count"] == 2

    def test_mark_resolved_deletes(self):
        update_alert_state("fp-3", time.time())
        mark_resolved("fp-3")
        state = get_alert_state("fp-3")
        assert state["last_alert_time"] is None
        assert state["alert_count"] == 0


# ---------------------------------------------------------------------------
# Slack block building (legacy `build_alert_blocks` shim + direct plugin)
# ---------------------------------------------------------------------------


class TestBuildAlertBlocks:
    def test_basic_blocks(self, sample_payload, sample_config):
        blocks = build_alert_blocks(sample_payload, sample_config)
        assert len(blocks) >= 5
        assert blocks[0]["type"] == "header"
        assert "Human Intervention Required" in blocks[0]["text"]["text"]

    def test_includes_root_cause(self, sample_payload, sample_config):
        blocks = build_alert_blocks(sample_payload, sample_config)
        rc = next(
            b for b in blocks
            if b.get("text", {}).get("text", "").startswith("*Root Cause Analysis:*")
        )
        assert "Configuration syntax error" in rc["text"]["text"]

    def test_includes_grafana_link(self, sample_payload, sample_config):
        blocks = build_alert_blocks(sample_payload, sample_config)
        link = next(b for b in blocks if "View Grafana Dashboard" in str(b))
        assert "http://localhost:3000/d/system-logs" in str(link)

    def test_dedup_info(self, sample_payload, sample_config):
        blocks = build_alert_blocks(sample_payload, sample_config)
        context = next(b for b in blocks if b["type"] == "context")
        assert "Silenced until" in context["elements"][0]["text"]
        assert "15 min" in context["elements"][0]["text"]

    def test_direct_slack_blocks_escalation_header(self, sample_payload):
        blocks = build_slack_blocks(sample_payload, "http://x", 15, is_escalation=True)
        assert "ESCALATION" in blocks[0]["text"]["text"]


# ---------------------------------------------------------------------------
# Plugin registry
# ---------------------------------------------------------------------------


class TestPluginRegistry:
    def test_slack_only(self, sample_config):
        plugins = build_default_plugins(sample_config)
        assert any(isinstance(p, SlackPlugin) for p in plugins)
        assert not any(isinstance(p, WebhookPlugin) for p in plugins)

    def test_webhook_only(self):
        config = AlertConfig(slack_webhook_url=None, webhook_url="https://example.com/hook")
        plugins = build_default_plugins(config)
        assert any(isinstance(p, WebhookPlugin) for p in plugins)
        assert not any(isinstance(p, SlackPlugin) for p in plugins)

    def test_both_channels(self):
        config = AlertConfig(
            slack_webhook_url="https://hooks.slack.com/x",
            webhook_url="https://example.com/hook",
        )
        plugins = build_default_plugins(config)
        assert len(plugins) == 2

    def test_none_configured(self):
        config = AlertConfig(slack_webhook_url=None, webhook_url=None)
        assert build_default_plugins(config) == []


class TestSlackPluginSend:
    @patch("alert_plugins.requests.post")
    def test_posts_to_webhook(self, mock_post, sample_payload):
        mock_post.return_value.raise_for_status.return_value = None
        plugin = SlackPlugin("https://hooks.slack.com/x")
        assert plugin.send(sample_payload) is True
        assert mock_post.called
        body = mock_post.call_args.kwargs["json"]
        assert "blocks" in body


class TestWebhookPluginSend:
    @patch("alert_plugins.requests.post")
    def test_posts_payload_as_json(self, mock_post, sample_payload):
        mock_post.return_value.raise_for_status.return_value = None
        plugin = WebhookPlugin("https://example.com/hook", headers={"X-Key": "abc"})
        assert plugin.send(sample_payload, is_escalation=True) is True
        kwargs = mock_post.call_args.kwargs
        assert kwargs["json"]["is_escalation"] is True
        assert kwargs["headers"]["X-Key"] == "abc"


# ---------------------------------------------------------------------------
# AlertManager
# ---------------------------------------------------------------------------


class TestAlertManager:
    def test_no_plugins_suppressed(self, sample_payload):
        config = AlertConfig(slack_webhook_url=None, webhook_url=None)
        manager = AlertManager(config)
        should_send, _ = manager.should_send_alert(sample_payload.fingerprint)
        assert should_send is False

    def test_first_alert_sends(self, sample_payload, sample_config):
        manager = AlertManager(sample_config, plugins=[_RecordingPlugin()])
        should_send, is_escalation = manager.should_send_alert(sample_payload.fingerprint)
        assert should_send is True
        assert is_escalation is False

    def test_dedup_window_suppresses(self, sample_payload, sample_config):
        plugin = _RecordingPlugin()
        manager = AlertManager(sample_config, plugins=[plugin])
        assert manager.send_alert(sample_payload) is True
        should_send, _ = manager.should_send_alert(sample_payload.fingerprint)
        assert should_send is False

    def test_escalation_after_timeout(self, sample_payload, sample_config):
        old_time = time.time() - (40 * 60)
        update_alert_state(sample_payload.fingerprint, old_time)
        manager = AlertManager(sample_config, plugins=[_RecordingPlugin()])
        should_send, is_escalation = manager.should_send_alert(sample_payload.fingerprint)
        assert should_send is True
        assert is_escalation is True

    def test_send_alert_updates_state(self, sample_payload, sample_config):
        manager = AlertManager(sample_config, plugins=[_RecordingPlugin()])
        manager.send_alert(sample_payload)
        state = get_alert_state(sample_payload.fingerprint)
        assert state["last_alert_time"] is not None
        assert state["alert_count"] == 1

    def test_fans_out_to_all_plugins(self, sample_payload, sample_config):
        p1, p2 = _RecordingPlugin("a"), _RecordingPlugin("b")
        manager = AlertManager(sample_config, plugins=[p1, p2])
        assert manager.send_alert(sample_payload) is True
        assert len(p1.calls) == 1
        assert len(p2.calls) == 1

    def test_one_plugin_failure_does_not_block_others(self, sample_payload, sample_config):
        failing = _RecordingPlugin("fail", succeed=False)
        ok = _RecordingPlugin("ok", succeed=True)
        manager = AlertManager(sample_config, plugins=[failing, ok])
        assert manager.send_alert(sample_payload) is True  # any success → True
        assert len(ok.calls) == 1
        assert len(failing.calls) == 1

    def test_mark_resolved_resets_dedup(self, sample_payload, sample_config):
        manager = AlertManager(sample_config, plugins=[_RecordingPlugin()])
        manager.send_alert(sample_payload)
        manager.mark_issue_resolved(sample_payload.fingerprint)
        should_send, _ = manager.should_send_alert(sample_payload.fingerprint)
        assert should_send is True


class TestIntegration:
    def test_full_alert_flow(self, sample_config):
        plugin = _RecordingPlugin()
        manager = AlertManager(sample_config, plugins=[plugin])

        payload = AlertPayload(
            fingerprint="integration-test",
            error_summary="Test error",
            timestamp=time.time(),
            llm_root_cause="Test root cause",
            proposed_remediation="echo test",
        )

        assert manager.send_alert(payload) is True

        payload.occurrence_count = 2
        payload.timestamp = time.time()
        assert manager.send_alert(payload) is False  # within dedup

        manager.mark_issue_resolved(payload.fingerprint)
        payload.occurrence_count = 3
        payload.timestamp = time.time()
        assert manager.send_alert(payload) is True
