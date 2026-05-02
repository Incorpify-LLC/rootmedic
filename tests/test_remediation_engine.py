"""Tests for remediation_engine.py — graduated autonomy, snapshots, dry-run."""

import json
import time
from pathlib import Path
from unittest import mock

import pytest

from remediation_engine import (
    AutonomyLevel,
    IssueRecord,
    RemediationEngine,
    RemediationPlan,
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
    CONFIDENCE_THRESHOLD,
)


# ------------------------------------------------------------------ fingerprint_issue

class TestFingerprintIssue:
    def test_same_message_same_fingerprint(self):
        fp1 = fingerprint_issue("error: connection refused")
        fp2 = fingerprint_issue("error: connection refused")
        assert fp1 == fp2

    def test_different_messages_different_fingerprint(self):
        fp1 = fingerprint_issue("connection refused")
        fp2 = fingerprint_issue("out of memory")
        assert fp1 != fp2

    def test_unit_affects_fingerprint(self):
        fp1 = fingerprint_issue("error 500", unit="nginx.service")
        fp2 = fingerprint_issue("error 500", unit="postgresql.service")
        assert fp1 != fp2

    def test_strips_timestamps(self):
        fp1 = fingerprint_issue("2025-01-15 12:00:00 error: timeout")
        fp2 = fingerprint_issue("2025-06-30 23:59:59 error: timeout")
        assert fp1 == fp2

    def test_strips_pids(self):
        fp1 = fingerprint_issue("nginx[1234]: connection refused")
        fp2 = fingerprint_issue("nginx[5678]: connection refused")
        assert fp1 == fp2

    def test_strips_ips(self):
        fp1 = fingerprint_issue("connect to 10.0.0.1:8080 failed")
        fp2 = fingerprint_issue("connect to 192.168.1.100:8080 failed")
        assert fp1 == fp2

    def test_strips_hex_addresses(self):
        fp1 = fingerprint_issue("segfault at 0x7f8a1c000000")
        fp2 = fingerprint_issue("segfault at 0xdeadbeef")
        assert fp1 == fp2

    def test_empty_message(self):
        fp = fingerprint_issue("", "")
        assert len(fp) == 16


# ---------------------------------------------------------------- compute_confidence

class TestComputeConfidence:
    def test_zero_occurrences_zero_confidence(self):
        record = IssueRecord(fingerprint="abc", occurrences=0)
        assert compute_confidence(record) == 0.0

    def test_low_occurrences_low_confidence(self):
        record = IssueRecord(fingerprint="abc", occurrences=1, successful_fixes=1)
        conf = compute_confidence(record)
        assert 0 < conf < 0.5  # weight < 1.0

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
            failed_fixes=0,
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
        conf = compute_confidence(record)
        assert conf < 1.0
        assert conf == pytest.approx(0.5, abs=0.01)


# --------------------------------------------------------------- determine_autonomy

class TestDetermineAutonomy:
    def test_below_gate_always_recommend(self):
        record = IssueRecord(fingerprint="a", occurrences=2)
        assert determine_autonomy(record, 1.0) == AutonomyLevel.RECOMMEND

    def test_at_gate_no_history_semi(self):
        record = IssueRecord(fingerprint="a", occurrences=OCCURRENCE_GATE)
        assert determine_autonomy(record, 0.0) == AutonomyLevel.SEMI_AUTONOMOUS

    def test_high_confidence_and_success_full(self):
        record = IssueRecord(
            fingerprint="a",
            occurrences=10,
            successful_fixes=10,
            failed_fixes=0,
        )
        assert determine_autonomy(record, 0.99) == AutonomyLevel.FULL_AUTONOMOUS

    def test_high_confidence_but_poor_success_stays_semi(self):
        record = IssueRecord(
            fingerprint="a",
            occurrences=10,
            successful_fixes=2,
            failed_fixes=8,
        )
        confidence = compute_confidence(record)
        assert determine_autonomy(record, confidence) == AutonomyLevel.SEMI_AUTONOMOUS


# ----------------------------------------------------------------- snapshot_configs

class TestSnapshotConfigs:
    def test_creates_snapshots_for_existing_files(self, temp_dir):
        src = temp_dir / "test.conf"
        src.write_text("original content")
        snapshots = snapshot_configs([str(src)])
        assert str(src) in snapshots
        assert Path(snapshots[str(src)]).exists()
        assert Path(snapshots[str(src)]).read_text() == "original content"

    def test_skips_nonexistent_paths(self, temp_dir):
        snapshots = snapshot_configs([str(temp_dir / "does_not_exist")])
        assert len(snapshots) == 0


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
        assert backup_path.exists()
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
        log = Path("dry_run.log")
        assert log.exists()
        content = log.read_text()
        assert "DRY-RUN" in content


# ------------------------------------------------------- load_state / save_state

class TestStatePersistence:
    def test_load_state_empty_when_file_missing(self):
        state = load_state()
        assert state == {}

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
        assert "fp1" in loaded
        assert loaded["fp1"].occurrences == 5
        assert loaded["fp1"].successful_fixes == 3

    def test_state_file_is_valid_json(self):
        save_state({"x": IssueRecord(fingerprint="x", occurrences=1)})
        data = json.loads(STATE_FILE.read_text())
        assert "x" in data
        assert data["x"]["occurrences"] == 1


# ------------------------------------------------------------ RemediationEngine

class TestRemediationEngine:
    def test_assess_new_issue_recommend(self):
        engine = RemediationEngine()
        level, record, conf = engine.assess("connection refused", "nginx.service")
        assert level == AutonomyLevel.RECOMMEND
        assert record.occurrences == 1
        assert conf == 0.0

    def test_assess_accumulates_occurrences(self):
        engine = RemediationEngine()
        for _ in range(OCCURRENCE_GATE):
            level, _, _ = engine.assess("connection refused", "nginx.service")
        assert level != AutonomyLevel.RECOMMEND

    def test_execute_recommend_skips_and_reports(self):
        engine = RemediationEngine()
        engine.assess("err", "unit")
        plan = RemediationPlan(
            issue_fingerprint="abc",
            description="restart nginx",
            commands=["systemctl restart nginx"],
            rollback_commands=["systemctl stop nginx"],
        )
        result = engine.execute(plan, AutonomyLevel.RECOMMEND)
        assert result["status"] == "recommended"
        assert "Human approval required" in result["message"]
        assert "systemctl restart nginx" in result["message"]

    def test_execute_semi_low_confidence_dry_run(self):
        engine = RemediationEngine()
        # Build up occurrences so it's at SEMI level
        for _ in range(OCCURRENCE_GATE):
            engine.assess("connection refused", "nginx.service")
        level, _, conf = engine.assess("connection refused", "nginx.service")
        assert level == AutonomyLevel.SEMI_AUTONOMOUS
        plan = RemediationPlan(
            issue_fingerprint="fp",
            description="restart",
            commands=["echo ok"],
            rollback_commands=["echo revert"],
            confidence=conf,
        )
        result = engine.execute(plan, AutonomyLevel.SEMI_AUTONOMOUS)
        # confidence is 0 → should dry-run, not apply
        assert result["status"] in ("dry_run", "recommended")

    @mock.patch("remediation_engine.subprocess.run")
    @mock.patch("remediation_engine.snapshot_configs", return_value={})
    @mock.patch("remediation_engine.cleanup_snapshots")
    def test_execute_full_applies_commands(self, mock_cleanup, mock_snap, mock_run, temp_dir):
        mock_run.return_value = mock.MagicMock(returncode=0)
        engine = RemediationEngine()
        plan = RemediationPlan(
            issue_fingerprint="fp",
            description="full fix",
            commands=["echo applied"],
            rollback_commands=[],
            confidence=1.0,
        )
        result = engine.execute(plan, AutonomyLevel.FULL_AUTONOMOUS)
        assert result["status"] == "applied"
        mock_run.assert_called()

    @mock.patch("remediation_engine.subprocess.run")
    @mock.patch("remediation_engine.snapshot_configs", return_value={})
    @mock.patch("remediation_engine.restore_snapshots", return_value=[])
    def test_execute_full_rollback_on_failure(self, mock_restore, mock_snap, mock_run):
        """When a command fails, rollback is triggered."""
        from subprocess import CalledProcessError

        # First call (the command) fails; subsequent calls (rollback) succeed
        mock_run.side_effect = [CalledProcessError(1, "cmd", stderr="fail"), mock.MagicMock(returncode=0)]
        engine = RemediationEngine()
        plan = RemediationPlan(
            issue_fingerprint="fp",
            description="failing fix",
            commands=["false"],
            rollback_commands=["echo rollback"],
            confidence=1.0,
        )
        result = engine.execute(plan, AutonomyLevel.FULL_AUTONOMOUS)
        assert result["status"] == "rolled_back"

    @mock.patch("remediation_engine.subprocess.run")
    @mock.patch("remediation_engine.snapshot_configs", return_value={})
    @mock.patch("remediation_engine.cleanup_snapshots")
    def test_execute_updates_track_record_on_success(self, mock_cleanup, mock_snap, mock_run):
        mock_run.return_value = mock.MagicMock(returncode=0)
        engine = RemediationEngine()
        fp = fingerprint_issue("disk full", "systemd-journald.service")
        for _ in range(OCCURRENCE_GATE + 5):
            engine.assess("disk full", "systemd-journald.service")
        record = engine.state[fp]
        record.successful_fixes = 10
        record.failed_fixes = 0
        save_state(engine.state)

        plan = RemediationPlan(
            issue_fingerprint=fp,
            description="clean logs",
            commands=["echo ok 2>/dev/null"],
            rollback_commands=[],
            confidence=1.0,
        )
        engine.execute(plan, AutonomyLevel.FULL_AUTONOMOUS)
        assert engine.state[fp].successful_fixes == 11

    @mock.patch("remediation_engine.subprocess.run")
    @mock.patch("remediation_engine.snapshot_configs", return_value={})
    @mock.patch("remediation_engine.restore_snapshots", return_value=[])
    def test_execute_updates_track_record_on_failure(self, mock_restore, mock_snap, mock_run):
        from subprocess import CalledProcessError
        # First call fails; rollback calls succeed
        mock_run.side_effect = [CalledProcessError(1, "cmd", stderr="fail"), mock.MagicMock(returncode=0)]
        engine = RemediationEngine()
        fp = fingerprint_issue("disk full", "systemd-journald.service")
        for _ in range(OCCURRENCE_GATE + 5):
            engine.assess("disk full", "systemd-journald.service")
        record = engine.state[fp]
        record.occurrences = 10
        record.successful_fixes = 5
        record.failed_fixes = 2
        save_state(engine.state)

        plan = RemediationPlan(
            issue_fingerprint=fp,
            description="bad fix",
            commands=["exit 1"],
            rollback_commands=["echo rollback"],
            confidence=1.0,
        )
        engine.execute(plan, AutonomyLevel.FULL_AUTONOMOUS)
        assert engine.state[fp].failed_fixes == 3
