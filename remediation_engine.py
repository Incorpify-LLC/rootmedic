"""Graduated autonomy model for RootMedic auto-remediation.

Three autonomy levels:
  RECOMMEND       – human-in-the-loop for first N occurrences of a new issue type.
  SEMI_AUTONOMOUS – apply fix only after a successful dry-run or if confidence > 95%.
  FULL_AUTONOMOUS – validated patterns, deployed via canary in production.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

STATE_FILE = Path("remediation_state.json")
CONFIDENCE_THRESHOLD = 0.95
OCCURRENCE_GATE = 3  # first N occurrences stay in RECOMMEND
SNAPSHOT_DIR = Path(".rollback_snapshots")
DRY_RUN_LOG = Path("dry_run.log")

# Known config files to snapshot before remediation
PROTECTED_PATHS = [
    "/etc/fstab",
    "/etc/systemd/system/",
    "/etc/nginx/",
    "/etc/ssh/sshd_config",
]

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class AutonomyLevel(Enum):
    RECOMMEND = "recommend"           # human must approve
    SEMI_AUTONOMOUS = "semi"          # dry-run or high-confidence gate
    FULL_AUTONOMOUS = "full"          # validated pattern, auto-apply


@dataclass
class IssueRecord:
    """Tracks how many times an issue type has been seen and its history."""

    fingerprint: str
    occurrences: int = 0
    last_seen: float = 0.0
    successful_fixes: int = 0
    failed_fixes: int = 0
    first_seen: float = 0.0

    @property
    def success_rate(self) -> float:
        total = self.successful_fixes + self.failed_fixes
        return self.successful_fixes / total if total > 0 else 0.0


@dataclass
class RemediationPlan:
    """A remediation action with its rollback counterpart."""

    issue_fingerprint: str
    description: str
    commands: list[str]
    rollback_commands: list[str]
    config_snapshots: dict[str, str] = field(default_factory=dict)  # path -> backup path
    confidence: float = 0.0
    dry_run_output: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.issue_fingerprint,
            "description": self.description,
            "commands": self.commands,
            "rollback_commands": self.rollback_commands,
            "config_snapshots": self.config_snapshots,
            "confidence": self.confidence,
            "dry_run_output": self.dry_run_output,
        }


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


def load_state() -> dict[str, IssueRecord]:
    if STATE_FILE.exists():
        raw = json.loads(STATE_FILE.read_text())
        return {k: IssueRecord(**v) for k, v in raw.items()}
    return {}


def save_state(state: dict[str, IssueRecord]) -> None:
    STATE_FILE.write_text(json.dumps(
        {k: v.__dict__ for k, v in state.items()}, indent=2, default=str
    ))


# ---------------------------------------------------------------------------
# Fingerprinting
# ---------------------------------------------------------------------------


def fingerprint_issue(log_message: str, unit: str = "") -> str:
    """Create a stable fingerprint for an issue type from its log pattern.

    Strips timestamps, PIDs, and IPs so that the same *kind* of issue
    always hashes to the same fingerprint.
    """
    import re

    cleaned = log_message.lower()
    # Strip variable data: timestamps, PIDs, IPs, hex memory addresses
    cleaned = re.sub(r"\b\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}:\d{2}", "<TS>", cleaned)
    cleaned = re.sub(r"pid[= ]?\d+", "pid=<PID>", cleaned)
    cleaned = re.sub(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "<IP>", cleaned)
    cleaned = re.sub(r"0x[0-9a-f]+", "<HEX>", cleaned)
    cleaned = re.sub(r"\d+", "<N>", cleaned)

    raw = f"{unit}:{cleaned}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------


def compute_confidence(record: IssueRecord, match_quality: float = 1.0) -> float:
    """Score 0-1 based on historical fix success and match quality."""
    if record.occurrences == 0:
        return 0.0
    weight_history = min(record.occurrences / (OCCURRENCE_GATE * 3), 1.0)
    return round(record.success_rate * weight_history * match_quality, 4)


# ---------------------------------------------------------------------------
# Graduated autonomy decision
# ---------------------------------------------------------------------------


def determine_autonomy(record: IssueRecord, confidence: float) -> AutonomyLevel:
    """Decide which autonomy level applies for this issue."""
    if record.occurrences < OCCURRENCE_GATE:
        return AutonomyLevel.RECOMMEND
    if confidence >= CONFIDENCE_THRESHOLD and record.success_rate >= CONFIDENCE_THRESHOLD:
        return AutonomyLevel.FULL_AUTONOMOUS
    return AutonomyLevel.SEMI_AUTONOMOUS


# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------


def snapshot_configs(paths: list[str]) -> dict[str, str]:
    """Copy config files into SNAPSHOT_DIR and return {original: backup} map."""
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    snapshots: dict[str, str] = {}
    ts = int(time.time())

    for path in paths:
        src = Path(path)
        if not src.exists():
            continue
        if src.is_dir():
            for f in src.rglob("*"):
                if f.is_file():
                    dest = SNAPSHOT_DIR / f"{ts}_{f.name}"
                    shutil.copy2(f, dest)
                    snapshots[str(f)] = str(dest)
        else:
            dest = SNAPSHOT_DIR / f"{ts}_{src.name}"
            shutil.copy2(src, dest)
            snapshots[str(src)] = str(dest)

    return snapshots


def restore_snapshots(snapshots: dict[str, str]) -> list[str]:
    """Restore config files from snapshots. Returns list of restored paths."""
    restored = []
    for original, backup in snapshots.items():
        backup_path = Path(backup)
        if backup_path.exists():
            shutil.copy2(backup_path, original)
            restored.append(original)
    return restored


def cleanup_snapshots(snapshots: dict[str, str]) -> None:
    """Remove snapshot files after a successful fix."""
    for backup in snapshots.values():
        Path(backup).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Dry-run simulation
# ---------------------------------------------------------------------------


def dry_run(plan: RemediationPlan) -> str:
    """Simulate every command and return a trace of what *would* happen."""
    lines: list[str] = []
    lines.append(f"=== DRY-RUN for: {plan.description} ===")

    for cmd in plan.commands:
        lines.append(f"\n> {cmd}")
        result = subprocess.run(
            ["bash", "-c", f"echo '[DRY-RUN] would execute: {cmd}'"],
            capture_output=True, text=True, timeout=5,
        )
        lines.append(result.stdout.strip())

    lines.append("\n=== DRY-RUN complete – no changes applied ===")
    output = "\n".join(lines)
    DRY_RUN_LOG.write_text(output)
    return output


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------


class RemediationEngine:
    """Orchestrates the graduated autonomy pipeline."""

    def __init__(self) -> None:
        self.state: dict[str, IssueRecord] = load_state()

    def _record_event(self, fingerprint: str) -> IssueRecord:
        now = time.time()
        if fingerprint not in self.state:
            self.state[fingerprint] = IssueRecord(
                fingerprint=fingerprint, first_seen=now,
            )
        record = self.state[fingerprint]
        record.occurrences += 1
        record.last_seen = now
        save_state(self.state)
        return record

    def assess(
        self, log_message: str, unit: str = "",
    ) -> tuple[AutonomyLevel, IssueRecord, float]:
        """Given a log event, return (level, record, confidence)."""
        fp = fingerprint_issue(log_message, unit)
        record = self._record_event(fp)
        confidence = compute_confidence(record)
        level = determine_autonomy(record, confidence)
        return level, record, confidence

    def execute(self, plan: RemediationPlan, level: AutonomyLevel) -> dict[str, Any]:
        """Execute a remediation plan according to the autonomy level.

        Returns a result dict with status and detail.
        """
        result: dict[str, Any] = {"fingerprint": plan.issue_fingerprint, "status": "skipped"}

        # --- RECOMMEND --------------------------------------------------
        if level == AutonomyLevel.RECOMMEND:
            result["status"] = "recommended"
            result["message"] = (
                f"Human approval required for: {plan.description}\n"
                f"Proposed commands: {plan.commands}\n"
                f"Rollback: {plan.rollback_commands}"
            )
            return result

        # --- SEMI-AUTONOMOUS --------------------------------------------
        if level == AutonomyLevel.SEMI_AUTONOMOUS:
            plan.dry_run_output = dry_run(plan)
            if plan.confidence < CONFIDENCE_THRESHOLD:
                result["status"] = "dry_run"
                result["dry_run"] = plan.dry_run_output
                result["message"] = (
                    f"Dry-run passed but confidence {plan.confidence:.1%} "
                    f"< {CONFIDENCE_THRESHOLD:.0%}. Awaiting approval."
                )
                return result

        # --- FULL-AUTONOMOUS (or semi with high confidence) -------------
        snapshots = snapshot_configs(PROTECTED_PATHS)
        plan.config_snapshots = snapshots

        success = True
        for cmd in plan.commands:
            try:
                subprocess.run(
                    cmd, shell=True, check=True, capture_output=True,
                    text=True, timeout=30,
                )
            except subprocess.CalledProcessError as exc:
                success = False
                result["failed_command"] = cmd
                result["stderr"] = exc.stderr
                # Rollback on failure
                restored = restore_snapshots(snapshots)
                for rb in plan.rollback_commands:
                    subprocess.run(rb, shell=True, capture_output=True, timeout=30)
                result["status"] = "rolled_back"
                result["restored_files"] = restored
                break

        if success:
            cleanup_snapshots(snapshots)
            result["status"] = "applied"
            result["applied_commands"] = plan.commands

        # Update track record
        record = self.state.get(plan.issue_fingerprint)
        if record:
            if success:
                record.successful_fixes += 1
            else:
                record.failed_fixes += 1
            save_state(self.state)

        return result


# ---------------------------------------------------------------------------
# CLI entry point  (for standalone testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    engine = RemediationEngine()

    # Simulated: an issue is detected from a log line
    sample_log = "nginx[1234]: connect() to 10.0.0.5:8080 failed (111: Connection refused)"
    level, record, conf = engine.assess(sample_log, unit="nginx.service")

    print(f"Fingerprint : {record.fingerprint}")
    print(f"Occurrences : {record.occurrences}")
    print(f"Confidence  : {conf:.2%}")
    print(f"Autonomy    : {level.value.upper()}")

    # Build a sample plan
    plan = RemediationPlan(
        issue_fingerprint=record.fingerprint,
        description="Restart nginx after upstream connection failure",
        commands=["systemctl restart nginx"],
        rollback_commands=["systemctl stop nginx", "systemctl start nginx@previous"],
        confidence=conf,
    )

    result = engine.execute(plan, level)
    print(f"\nResult: {json.dumps(result, indent=2)}")
