"""Incident archive with tiered retention.

Plan-A archives every incident under
``/client-id/yyyy-mm/incident-UUID/`` with three retention tiers
(30d / 6mo / 12mo). This module implements that on a local filesystem
backend; swapping in S3/MinIO is a contained change to :func:`_write_artifact`.

Each incident directory holds:

* ``incident.yaml``       – the full structured record (timestamps, host,
  fingerprint, normalized message, RCA, autonomy level, confidence).
* ``remediation.yaml``    – the declarative plan (matches what the engine
  emits to the top-level ``remediation.yaml`` for the *latest* incident).
* ``dry_run.log``         – the dry-run trace, when one was produced.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

ARCHIVE_ROOT = Path("archive")

# Days of retention for each subscription tier (plan-A).
RETENTION_TIERS: dict[str, int] = {
    "free": 30,
    "pro": 180,
    "enterprise": 365,
}


@dataclass
class IncidentRecord:
    """Everything we know about a single incident at archive time."""

    fingerprint: str
    timestamp: float
    host: str
    unit: str
    message: str
    description: str
    commands: list[str]
    rollback_commands: list[str]
    autonomy_level: str
    confidence: float
    rca: str = ""
    dry_run_output: str = ""
    source: str = "agent"  # "agent" | "cached" | "llm" | "manual"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# YAML helpers (graceful fallback to JSON if PyYAML is unavailable)
# ---------------------------------------------------------------------------


def _dump(data: dict[str, Any]) -> str:
    try:
        import yaml
        return yaml.safe_dump(data, sort_keys=False)
    except ImportError:
        return json.dumps(data, indent=2)


def _write_artifact(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# ---------------------------------------------------------------------------
# Archive write
# ---------------------------------------------------------------------------


def archive_incident(
    record: IncidentRecord,
    *,
    client_id: str = "default",
    root: Path = ARCHIVE_ROOT,
    plan_yaml: Optional[str] = None,
) -> Path:
    """Persist ``record`` under ``root/<client>/<YYYY-MM>/<fp>-<ts>/``.

    Returns the directory the artifacts were written to.
    """
    month = time.strftime("%Y-%m", time.gmtime(record.timestamp))
    incident_dir = root / client_id / month / f"{record.fingerprint}-{int(record.timestamp)}"
    incident_dir.mkdir(parents=True, exist_ok=True)

    _write_artifact(incident_dir / "incident.yaml", _dump(record.to_dict()))

    if plan_yaml is None:
        plan_yaml = _dump({
            "fingerprint": record.fingerprint,
            "description": record.description,
            "commands": record.commands,
            "rollback_commands": record.rollback_commands,
            "confidence": record.confidence,
        })
    _write_artifact(incident_dir / "remediation.yaml", plan_yaml)

    if record.dry_run_output:
        _write_artifact(incident_dir / "dry_run.log", record.dry_run_output)

    return incident_dir


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def prune_archive(
    *,
    tier: str = "free",
    root: Path = ARCHIVE_ROOT,
    now: Optional[float] = None,
) -> list[str]:
    """Delete incident directories older than the tier's retention window.

    The timestamp is parsed from each incident directory name
    (``<fp>-<unix_ts>``) rather than ``stat().st_mtime`` so we don't get
    confused by filesystem ``touch`` events or rsync replication.

    Returns the list of removed directory paths.
    """
    if tier not in RETENTION_TIERS:
        raise ValueError(f"unknown retention tier: {tier!r}; choose from {list(RETENTION_TIERS)}")

    cutoff = (now if now is not None else time.time()) - RETENTION_TIERS[tier] * 86400
    removed: list[str] = []
    root = Path(root)
    if not root.exists():
        return removed

    for client_dir in root.iterdir():
        if not client_dir.is_dir():
            continue
        for month_dir in client_dir.iterdir():
            if not month_dir.is_dir():
                continue
            for incident_dir in month_dir.iterdir():
                if not incident_dir.is_dir():
                    continue
                ts = _parse_incident_timestamp(incident_dir.name)
                if ts is not None and ts < cutoff:
                    shutil.rmtree(incident_dir, ignore_errors=True)
                    removed.append(str(incident_dir))
            # opportunistically remove empty month dirs
            if not any(month_dir.iterdir()):
                month_dir.rmdir()

    return removed


def _parse_incident_timestamp(dirname: str) -> Optional[int]:
    """Best-effort: extract the trailing ``-<unix_ts>`` from an incident dirname."""
    if "-" not in dirname:
        return None
    suffix = dirname.rsplit("-", 1)[-1]
    try:
        return int(suffix)
    except ValueError:
        return None
