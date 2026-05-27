"""Shared fixtures for RootMedic tests."""

import shutil
import tempfile
from pathlib import Path

import pytest

# Ensure repo root is on sys.path so imports work from tests/
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


_RUNTIME_FILES = [
    "remediation_state.json",
    "dry_run.log",
    "remediation.yaml",
    "known_issues.json",
]
_RUNTIME_DIRS = [".rollback_snapshots", "archive"]


def _purge_runtime_state() -> None:
    for fname in _RUNTIME_FILES:
        p = Path(fname)
        if p.exists():
            p.unlink()
    for dname in _RUNTIME_DIRS:
        d = Path(dname)
        if d.exists():
            shutil.rmtree(d)


@pytest.fixture
def temp_dir():
    """Isolated temporary directory, auto-cleaned."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture(autouse=True)
def clean_runtime_state():
    """Remove runtime state files before and after each test."""
    _purge_runtime_state()
    yield
    _purge_runtime_state()


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
