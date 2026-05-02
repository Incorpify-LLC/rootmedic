"""Shared fixtures for RootMedic tests."""

import os
import tempfile
from pathlib import Path

import pytest

# Ensure repo root is on sys.path so imports work from tests/
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def temp_dir():
    """Isolated temporary directory, auto-cleaned."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture(autouse=True)
def clean_runtime_state():
    """Remove runtime state files before and after each test."""
    for fname in ["remediation_state.json", "dry_run.log"]:
        p = Path(fname)
        if p.exists():
            p.unlink()
    snap_dir = Path(".rollback_snapshots")
    if snap_dir.exists():
        import shutil
        shutil.rmtree(snap_dir)
    yield
    for fname in ["remediation_state.json", "dry_run.log"]:
        p = Path(fname)
        if p.exists():
            p.unlink()
    snap_dir = Path(".rollback_snapshots")
    if snap_dir.exists():
        import shutil
        shutil.rmtree(snap_dir)


@pytest.fixture
def sample_logs():
    """Minimal Loki-style raw log entries."""
    return [
        {
            "stream": {"host": "node1", "systemd_unit": "nginx.service"},
            "values": [
                ["1714000000000000000", "connect() to 10.0.0.5:8080 failed (111: Connection refused)"],
                ["1714000001000000000", "nginx[1234]: worker process 5678 exited on signal 9"],
            ],
        },
        {
            "stream": {"host": "node2", "systemd_unit": "systemd-journald.service"},
            "values": [
                ["1714000002000000000", "Out of memory: Killed process 9999 (java)"],
            ],
        },
    ]
