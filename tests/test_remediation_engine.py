"""Tests for remediation_engine.py — recommend-only autonomy, snapshots, dry-run, apply."""

import json
from pathlib import Path
from unittest import mock

import pytest

from remediation_engine import (
    AutonomyLevel,
    IssueRecord,
    RemediationEngine,
    RemediationPlan,
    REMEDIATION_YAML,
    cleanup_snapshots,
    compute_confidence,
    determine_autonomy,
    dry_run,
    fingerprint_issue,
    load_state,
    restore_snapshots,
    save_state,
    snapshot_configs,
    STATE_FILE,
    OCCURRENCE_GATE,
)


# ------------------------------------------------------------------ fingerprint_issue

class TestFingerprintIssue:
    def test_same_message_same_fingerprint(self):
        assert fingerprint_issue("error: connection refused") == fingerprint_issue("error: connection refused")

    def test_different_messages_different_fingerprint(self):
        assert fingerprint_issue("connection refused") != fingerprint_issue("out of memory")

    def test_unit_affects_fingerprint(self):
        assert (
            fingerprint_issue("error 500", unit="nginx.service")
            != fingerprint_issue("error 500", unit="postgresql.service")
        )

    def test_strips_timestamps(self):
        assert (
            fingerprint_issue("2025-01-15 12:00:00 error: timeout")
            == fingerprint_issue("2025-06-30 23:59:59 error: timeout")
        )

    def test_strips_pids(self):
        assert (
            fingerprint_issue("nginx[1234]: connection refused")
            == fingerprint_issue("nginx[5678]: connection refused")
        )

    def test_strips_ips(self):
        assert (
            fingerprint_issue("connect to 10.0.0.1:8080 failed")
            == fingerprint_issue("connect to 192.168.1.100:8080 failed")
        )

    def test_strips_hex_addresses(self):
        assert (
            fingerprint_issue("segfault at 0x7f8a1c000000")
            == fingerprint_issue("segfault at 0xdeadbeef")
        )

    def test_empty_message(self):
        assert len(fingerprint_issue("", "")) == 16


# ---------------------------------------------------------------- compute_confidence

class TestComputeConfidence:
    def test_zero_occurrences_zero_confidence(self):
        assert compute_confidence(IssueRecord(fingerprint="abc", occurrences=0)) == 0.0

    def test_low_occurrences_low_confidence(self):
        record = IssueRecord(fingerprint="abc", occurrences=1, successful_fixes=1)
        conf = compute_confidence(record)
        assert 0 < conf < 0.5

    def test_high_occurrences_full_confidence(self):
        record = IssueRecord(
            fingerprint="abc",
            occurrences=OCCURRENCE_GATE * 3,
            successful_fixes=10,
            failed_fixes=0,
        )
        assert compute_confidence(record) == 1.0

    def test_match_quality_scales_confidence(self):
        record = IssueRecord(
            fingerprint="abc",
            occurrences=OCCURRENCE_GATE * 3,
            successful_fixes=10,
        )
        full = compute_confidence(record, match_quality=1.0)
        half = compute_confidence(record, match_quality=0.5)
        assert half == pytest.approx(full * 0.5)

    def test_failures_reduce_confidence(self):
        record = IssueRecord(
            fingerprint="abc",
            occurrences=OCCURRENCE_GATE * 3,
            successful_fixes=5,
            failed_fixes=5,
        )
        assert compute_confidence(record) == pytest.approx(0.5, abs=0.01)


# --------------------------------------------------------------- determine_autonomy

class TestDetermineAutonomy:
    def test_below_gate_recommend(self):
        assert determine_autonomy(IssueRecord(fingerprint="a", occurrences=2)) == AutonomyLevel.RECOMMEND

    def test_at_gate_validated(self):
        record = IssueRecord(fingerprint="a", occurrences=OCCURRENCE_GATE)
        assert determine_autonomy(record) == AutonomyLevel.VALIDATED

    def test_far_past_gate_still_validated_never_auto(self):
        """The engine must never escalate past VALIDATED — no auto-apply tier exists."""
        record = IssueRecord(
            fingerprint="a",
            occurrences=100,
            successful_fixes=100,
            failed_fixes=0,
        )
        assert determine_autonomy(record, 1.0) == AutonomyLevel.VALIDATED


# ----------------------------------------------------------------- snapshot_configs

class TestSnapshotConfigs:
    def test_creates_snapshots_for_existing_files(self, temp_dir):
        src = temp_dir / "test.conf"
        src.write_text("original content")
        snapshots = snapshot_configs([str(src)])
        assert str(src) in snapshots
        assert Path(snapshots[str(src)]).read_text() == "original content"

    def test_skips_nonexistent_paths(self, temp_dir):
        assert snapshot_configs([str(temp_dir / "does_not_exist")]) == {}


class TestRestoreSnapshots:
    def test_restores_file_content(self, temp_dir):
        src = temp_dir / "restore_test.conf"
        src.write_text("before")
        snapshots = snapshot_configs([str(src)])
        src.write_text("changed")
        restored = restore_snapshots(snapshots)
        assert str(src) in restored
        assert src.read_text() == "before"


class TestCleanupSnapshots:
    def test_removes_backup_files(self, temp_dir):
        src = temp_dir / "cleanup_test.conf"
        src.write_text("data")
        snapshots = snapshot_configs([str(src)])
        backup_path = Path(snapshots[str(src)])
        cleanup_snapshots(snapshots)
        assert not backup_path.exists()


# ------------------------------------------------------------------------ dry_run

class TestDryRun:
    def test_dry_run_does_not_apply_changes(self, temp_dir):
        plan = RemediationPlan(
            issue_fingerprint="abc",
            description="Create a file",
            commands=[f"touch {temp_dir}/should_not_exist"],
            rollback_commands=[],
        )
        output = dry_run(plan)
        assert "DRY-RUN" in output
        assert "would execute" in output
        assert not (temp_dir / "should_not_exist").exists()

    def test_dry_run_writes_log_file(self, temp_dir):
        plan = RemediationPlan(
            issue_fingerprint="abc",
            description="test",
            commands=["echo hello"],
            rollback_commands=[],
        )
        dry_run(plan)
        assert Path("dry_run.log").exists()


# ------------------------------------------------------- load_state / save_state

class TestStatePersistence:
    def test_load_state_empty_when_file_missing(self):
        assert load_state() == {}

    def test_save_and_load_roundtrip(self):
        record = IssueRecord(
            fingerprint="fp1",
            occurrences=5,
            last_seen=12345.0,
            successful_fixes=3,
            failed_fixes=1,
            first_seen=1000.0,
        )
        save_state({"fp1": record})
        loaded = load_state()
        assert loaded["fp1"].occurrences == 5
        assert loaded["fp1"].successful_fixes == 3

    def test_state_file_is_valid_json(self):
        save_state({"x": IssueRecord(fingerprint="x", occurrences=1)})
        data = json.loads(STATE_FILE.read_text())
        assert data["x"]["occurrences"] == 1


# ------------------------------------------------------------ RemediationPlan.to_yaml

class TestPlanYaml:
    def test_to_yaml_includes_core_fields(self):
        plan = RemediationPlan(
            issue_fingerprint="fp",
            description="restart nginx",
            commands=["systemctl restart nginx"],
            rollback_commands=["systemctl stop nginx"],
            confidence=0.5,
        )
        rendered = plan.to_yaml()
        assert "restart nginx" in rendered
        assert "systemctl restart nginx" in rendered
        assert "fp" in rendered


# ------------------------------------------------------ RemediationEngine.assess

class TestEngineAssess:
    def test_assess_new_issue_recommend(self):
        engine = RemediationEngine()
        level, record, conf = engine.assess("connection refused", "nginx.service")
        assert level == AutonomyLevel.RECOMMEND
        assert record.occurrences == 1
        assert conf == 0.0

    def test_assess_promotes_to_validated_at_gate(self):
        engine = RemediationEngine()
        for _ in range(OCCURRENCE_GATE):
            level, _, _ = engine.assess("connection refused", "nginx.service")
        assert level == AutonomyLevel.VALIDATED


# ---------------------------------------------------- RemediationEngine.recommend

class TestEngineRecommend:
    def test_recommend_returns_recommendation_without_executing(self, temp_dir):
        engine = RemediationEngine()
        plan = RemediationPlan(
            issue_fingerprint="abc",
            description="restart nginx",
            commands=[f"touch {temp_dir}/should_not_exist"],
            rollback_commands=["systemctl stop nginx"],
        )
        result = engine.recommend(plan, AutonomyLevel.RECOMMEND, yaml_path=temp_dir / "plan.yaml")
        assert result["status"] == "recommended"
        assert "Human approval required" in result["message"]
        assert (temp_dir / "plan.yaml").exists()
        # The command must not have been run.
        assert not (temp_dir / "should_not_exist").exists()

    def test_recommend_validated_attaches_dry_run(self, temp_dir):
        engine = RemediationEngine()
        plan = RemediationPlan(
            issue_fingerprint="fp",
            description="restart",
            commands=["echo ok"],
            rollback_commands=["echo revert"],
            confidence=0.5,
        )
        result = engine.recommend(plan, AutonomyLevel.VALIDATED, yaml_path=temp_dir / "plan.yaml")
        assert result["status"] == "validated_recommendation"
        assert "DRY-RUN" in (result["dry_run"] or "")
        assert (temp_dir / "plan.yaml").exists()

    def test_recommend_writes_default_yaml(self):
        engine = RemediationEngine()
        plan = RemediationPlan(
            issue_fingerprint="fp",
            description="restart",
            commands=["echo ok"],
            rollback_commands=[],
        )
        engine.recommend(plan, AutonomyLevel.RECOMMEND)
        assert REMEDIATION_YAML.exists()
        REMEDIATION_YAML.unlink()  # cleanup


# ------------------------------------------------------- RemediationEngine.apply

class TestEngineApply:
    @mock.patch("remediation_engine.subprocess.run")
    @mock.patch("remediation_engine.snapshot_configs", return_value={})
    @mock.patch("remediation_engine.cleanup_snapshots")
    def test_apply_runs_commands(self, mock_cleanup, mock_snap, mock_run):
        mock_run.return_value = mock.MagicMock(returncode=0)
        engine = RemediationEngine()
        plan = RemediationPlan(
            issue_fingerprint="fp",
            description="fix",
            commands=["echo applied"],
            rollback_commands=[],
        )
        result = engine.apply(plan)
        assert result["status"] == "applied"
        mock_run.assert_called()

    @mock.patch("remediation_engine.subprocess.run")
    @mock.patch("remediation_engine.snapshot_configs", return_value={})
    @mock.patch("remediation_engine.restore_snapshots", return_value=[])
    def test_apply_rolls_back_on_failure(self, mock_restore, mock_snap, mock_run):
        from subprocess import CalledProcessError
        mock_run.side_effect = [
            CalledProcessError(1, "cmd", stderr="fail"),
            mock.MagicMock(returncode=0),  # rollback command
        ]
        engine = RemediationEngine()
        plan = RemediationPlan(
            issue_fingerprint="fp",
            description="failing fix",
            commands=["false"],
            rollback_commands=["echo rollback"],
        )
        result = engine.apply(plan)
        assert result["status"] == "rolled_back"

    @mock.patch("remediation_engine.subprocess.run")
    @mock.patch("remediation_engine.snapshot_configs", return_value={})
    @mock.patch("remediation_engine.cleanup_snapshots")
    def test_apply_increments_success_counter(self, mock_cleanup, mock_snap, mock_run):
        mock_run.return_value = mock.MagicMock(returncode=0)
        engine = RemediationEngine()
        fp = fingerprint_issue("disk full", "systemd-journald.service")
        for _ in range(OCCURRENCE_GATE + 5):
            engine.assess("disk full", "systemd-journald.service")
        engine.state[fp].successful_fixes = 10
        save_state(engine.state)

        plan = RemediationPlan(
            issue_fingerprint=fp,
            description="clean logs",
            commands=["echo ok"],
            rollback_commands=[],
        )
        engine.apply(plan)
        assert engine.state[fp].successful_fixes == 11

    @mock.patch("remediation_engine.subprocess.run")
    @mock.patch("remediation_engine.snapshot_configs", return_value={})
    @mock.patch("remediation_engine.restore_snapshots", return_value=[])
    def test_apply_increments_failure_counter(self, mock_restore, mock_snap, mock_run):
        from subprocess import CalledProcessError
        mock_run.side_effect = [
            CalledProcessError(1, "cmd", stderr="fail"),
            mock.MagicMock(returncode=0),
        ]
        engine = RemediationEngine()
        fp = fingerprint_issue("disk full", "systemd-journald.service")
        for _ in range(OCCURRENCE_GATE + 5):
            engine.assess("disk full", "systemd-journald.service")
        engine.state[fp].failed_fixes = 2
        save_state(engine.state)

        plan = RemediationPlan(
            issue_fingerprint=fp,
            description="bad fix",
            commands=["exit 1"],
            rollback_commands=["echo rollback"],
        )
        engine.apply(plan)
        assert engine.state[fp].failed_fixes == 3
