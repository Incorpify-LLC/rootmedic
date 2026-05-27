"""Tests for archive.py — incident YAML + tiered retention."""

import time
from pathlib import Path

import pytest

from archive import (
    IncidentRecord,
    RETENTION_TIERS,
    archive_incident,
    prune_archive,
)


def _record(ts=None, fp="abc123"):
    return IncidentRecord(
        fingerprint=fp,
        timestamp=ts if ts is not None else time.time(),
        host="node1",
        unit="nginx.service",
        message="connection refused",
        description="restart nginx",
        commands=["systemctl restart nginx"],
        rollback_commands=["systemctl stop nginx"],
        autonomy_level="recommend",
        confidence=0.0,
        dry_run_output="",
        source="rule",
    )


class TestArchiveIncident:
    def test_writes_incident_and_remediation_yaml(self, tmp_path):
        record = _record()
        dir = archive_incident(record, root=tmp_path)
        assert (dir / "incident.yaml").exists()
        assert (dir / "remediation.yaml").exists()

    def test_dry_run_log_only_when_present(self, tmp_path):
        record = _record()
        dir = archive_incident(record, root=tmp_path)
        assert not (dir / "dry_run.log").exists()

        record.dry_run_output = "=== DRY-RUN ===\n> echo hi\n"
        dir = archive_incident(record, root=tmp_path)
        assert (dir / "dry_run.log").exists()

    def test_client_id_namespaces_path(self, tmp_path):
        record = _record()
        dir = archive_incident(record, client_id="acme", root=tmp_path)
        assert "acme" in str(dir)

    def test_dirname_contains_fingerprint_and_timestamp(self, tmp_path):
        ts = 1714000000
        record = _record(ts=ts, fp="deadbeef")
        dir = archive_incident(record, root=tmp_path)
        assert "deadbeef" in dir.name
        assert str(ts) in dir.name


class TestPruneArchive:
    def test_removes_expired_incidents(self, tmp_path):
        old_ts = time.time() - (RETENTION_TIERS["free"] + 5) * 86400
        archive_incident(_record(ts=old_ts, fp="old"), root=tmp_path)
        archive_incident(_record(fp="new"), root=tmp_path)

        removed = prune_archive(tier="free", root=tmp_path)
        assert len(removed) == 1
        assert "old" in removed[0]

    def test_keeps_fresh_incidents_under_pro_tier(self, tmp_path):
        ts = time.time() - 60 * 86400  # 60 days ago
        archive_incident(_record(ts=ts, fp="midaged"), root=tmp_path)
        # Free tier (30d) would prune; pro tier (180d) must keep it.
        removed = prune_archive(tier="pro", root=tmp_path)
        assert removed == []

    def test_unknown_tier_raises(self, tmp_path):
        with pytest.raises(ValueError):
            prune_archive(tier="gold", root=tmp_path)

    def test_missing_root_is_noop(self, tmp_path):
        assert prune_archive(tier="free", root=tmp_path / "does-not-exist") == []
