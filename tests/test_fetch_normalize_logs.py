"""Tests for fetch_normalize_logs.py — agent pipeline orchestration."""

from pathlib import Path
from unittest import mock

import pytest

from fetch_normalize_logs import (
    _resolve_plan,
    build_remediation_plan,
    parse_and_normalize,
    run_agent,
)
from remediation_engine import RemediationEngine, RemediationPlan
from vector_store import VectorStore


# --------------------------------------------------------------- parse_and_normalize

class TestParseAndNormalize:
    def test_empty_logs(self):
        assert parse_and_normalize([]) == []

    def test_parses_stream_entries(self, sample_logs):
        events = parse_and_normalize(sample_logs)
        assert len(events) == 3
        assert events[0]["host"] == "node1"
        assert events[0]["unit"] == "nginx.service"
        assert "Connection refused" in events[0]["message"]
        assert events[2]["host"] == "node2"

    def test_timestamp_is_iso_format(self, sample_logs):
        events = parse_and_normalize(sample_logs)
        for ev in events:
            assert "T" in ev["timestamp"]
            assert "+" in ev["timestamp"] or "Z" in ev["timestamp"]

    def test_defaults_for_missing_labels(self):
        logs = [
            {"stream": {}, "values": [["1714000000000000000", "bare message"]]},
        ]
        events = parse_and_normalize(logs)
        assert events[0]["host"] == "unknown"
        assert events[0]["unit"] == "unknown"
        assert events[0]["message"] == "bare message"


# --------------------------------------------------------- build_remediation_plan

class TestBuildRemediationPlan:
    def test_connection_refused_triggers_restart_plan(self):
        engine = RemediationEngine()
        event = {
            "message": "connect() to 10.0.0.5:8080 failed (111: Connection refused)",
            "unit": "nginx.service",
        }
        plan = build_remediation_plan(event, engine)
        assert plan is not None
        assert "Restart" in plan.description
        assert "systemctl restart nginx.service" in plan.commands
        assert len(plan.rollback_commands) >= 1

    def test_out_of_memory_triggers_oom_plan(self):
        engine = RemediationEngine()
        event = {"message": "Out of memory: Killed process 9999 (java)", "unit": "java.service"}
        plan = build_remediation_plan(event, engine)
        assert plan is not None
        assert "cache" in " ".join(plan.commands).lower()

    def test_oom_variant_triggers_plan(self):
        engine = RemediationEngine()
        event = {"message": "oom-killer invoked", "unit": "myapp.service"}
        plan = build_remediation_plan(event, engine)
        assert plan is not None
        assert "Restart" in plan.description

    def test_disk_full_triggers_cleanup_plan(self):
        engine = RemediationEngine()
        event = {"message": "disk full on /dev/sda1", "unit": "systemd-journald.service"}
        plan = build_remediation_plan(event, engine)
        assert plan is not None
        assert "journalctl" in plan.commands[0]

    def test_no_space_triggers_cleanup_plan(self):
        engine = RemediationEngine()
        event = {"message": "no space left on device", "unit": "systemd-journald.service"}
        plan = build_remediation_plan(event, engine)
        assert plan is not None
        assert "apt-get clean" in plan.commands

    def test_unknown_message_returns_none(self):
        engine = RemediationEngine()
        event = {"message": "everything is fine", "unit": "dummy.service"}
        assert build_remediation_plan(event, engine) is None

    def test_case_insensitive_matching(self):
        engine = RemediationEngine()
        event = {"message": "Connection REFUSED on port 8080", "unit": "web.service"}
        plan = build_remediation_plan(event, engine)
        assert plan is not None


# -------------------------------------------------------------- _resolve_plan

class TestResolvePlan:
    def test_cache_hit_wins_over_rule(self):
        store = VectorStore()
        store.store(
            "connection refused",
            "nginx.service",
            description="cached fix",
            commands=["echo cached"],
            rollback_commands=[],
            source="seed",
        )
        engine = RemediationEngine()
        event = {"message": "connection refused", "unit": "nginx.service"}
        plan, source = _resolve_plan(event, engine, store, llm_config=None)
        assert source == "cached"
        assert plan.description == "cached fix"

    def test_rule_used_when_no_cache_hit(self):
        store = VectorStore()
        engine = RemediationEngine()
        event = {"message": "connection refused", "unit": "nginx.service"}
        plan, source = _resolve_plan(event, engine, store, llm_config=None)
        assert source == "rule"
        assert plan is not None

    def test_returns_none_when_nothing_matches(self):
        store = VectorStore()
        engine = RemediationEngine()
        event = {"message": "all good here", "unit": "happy.service"}
        plan, source = _resolve_plan(event, engine, store, llm_config=None)
        assert plan is None
        assert source == "none"


# ---------------------------------------------------------------------- run_agent

class TestRunAgent:
    @mock.patch("fetch_normalize_logs.fetch_logs")
    def test_no_events_graceful_exit(self, mock_fetch, capsys):
        mock_fetch.return_value = []
        run_agent()
        out = capsys.readouterr().out
        assert "No error/warning" in out

    @mock.patch("fetch_normalize_logs.fetch_logs")
    def test_events_are_processed(self, mock_fetch, capsys, sample_logs):
        mock_fetch.return_value = sample_logs
        run_agent()
        out = capsys.readouterr().out
        assert "nginx.service" in out or "node1" in out

    @mock.patch("fetch_normalize_logs.fetch_logs")
    def test_archive_is_written(self, mock_fetch, sample_logs):
        mock_fetch.return_value = sample_logs
        run_agent()
        # At least one event should produce an archived incident
        assert Path("archive").exists()
        # And the latest remediation.yaml should be on disk.
        assert Path("remediation.yaml").exists()
