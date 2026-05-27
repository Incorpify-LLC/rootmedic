"""Recommend-only remediation engine for RootMedic.

Per ``log-analyzer-plan-A.md`` the engine is **never** allowed to apply a fix
without an explicit human-approved call to :meth:`RemediationEngine.apply`.
The :meth:`assess` + :meth:`recommend` path is the default flow used by the
agent loop: it tracks issue occurrences, attaches a dry-run trace once the
pattern has been seen often enough, and emits a YAML artifact suitable for
review.

Two autonomy levels remain:

* ``RECOMMEND``  – new or rarely-seen issue, recommendation only.
* ``VALIDATED``  – occurrence count is past the gate; dry-run trace and
  historical confidence score are attached to the recommendation.

Neither level executes commands. :meth:`apply` is the only entrypoint that
runs subprocess and is intended to be invoked by an operator (CLI, web UI,
or an explicit ``--auto-approve`` flag in CI) once they have reviewed the
generated ``remediation.yaml``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from fingerprint import fingerprint_issue

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

STATE_FILE = Path("remediation_state.json")
CONFIDENCE_THRESHOLD = 0.95
OCCURRENCE_GATE = 3  # below this, issue stays in RECOMMEND
SNAPSHOT_DIR = Path(".rollback_snapshots")
DRY_RUN_LOG = Path("dry_run.log")
REMEDIATION_YAML = Path("remediation.yaml")

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
    RECOMMEND = "recommend"   # new pattern, raw recommendation only
    VALIDATED = "validated"   # past OCCURRENCE_GATE, dry-run + confidence attached


@dataclass
class IssueRecord:
    """Tracks how many times an issue type has been seen and its fix history."""

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
    """A remediation proposal with its rollback counterpart."""

    issue_fingerprint: str
    description: str
    commands: list[str]
    rollback_commands: list[str]
    config_snapshots: dict[str, str] = field(default_factory=dict)
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

    def to_yaml(self) -> str:
        """Render the plan as YAML (declarative ``remediation.yaml`` artifact)."""
        try:
            import yaml
            return yaml.safe_dump(self.to_dict(), sort_keys=False)
        except ImportError:
            return json.dumps(self.to_dict(), indent=2)


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


def load_state() -> dict[str, IssueRecord]:
    if STATE_FILE.exists():
        raw = json.loads(STATE_FILE.read_text())
        return {k: IssueRecord(**v) for k, v in raw.items()}
    return {}


def save_state(state: dict[str, IssueRecord]) -> None:
    STATE_FILE.write_text(
        json.dumps({k: v.__dict__ for k, v in state.items()}, indent=2, default=str)
    )


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
# Autonomy decision
# ---------------------------------------------------------------------------


def determine_autonomy(record: IssueRecord, confidence: float = 0.0) -> AutonomyLevel:
    """Return ``RECOMMEND`` until the issue has been seen ``OCCURRENCE_GATE``
    times, then ``VALIDATED``. Neither tier auto-applies — both are advisory.
    """
    if record.occurrences < OCCURRENCE_GATE:
        return AutonomyLevel.RECOMMEND
    return AutonomyLevel.VALIDATED


# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------


def snapshot_configs(paths: list[str]) -> dict[str, str]:
    """Copy config files into SNAPSHOT_DIR and return ``{original: backup}``."""
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
    lines: list[str] = [f"=== DRY-RUN for: {plan.description} ==="]

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
    """Orchestrates assessment, recommendation and (human-approved) apply."""

    def __init__(self) -> None:
        self.state: dict[str, IssueRecord] = load_state()

    # -- state ------------------------------------------------------------

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
        """Given a log event, return ``(level, record, confidence)``."""
        fp = fingerprint_issue(log_message, unit)
        record = self._record_event(fp)
        confidence = compute_confidence(record)
        level = determine_autonomy(record, confidence)
        return level, record, confidence

    # -- recommend (default, never executes) ------------------------------

    def recommend(
        self,
        plan: RemediationPlan,
        level: AutonomyLevel,
        yaml_path: Path | None = REMEDIATION_YAML,
    ) -> dict[str, Any]:
        """Produce a recommendation. Never runs any command.

        Attaches a dry-run trace when ``level == VALIDATED`` and writes the
        plan to ``yaml_path`` (set to ``None`` to skip the artifact).
        """
        if level == AutonomyLevel.VALIDATED:
            plan.dry_run_output = dry_run(plan)

        if yaml_path is not None:
            yaml_path.write_text(plan.to_yaml())

        status = "validated_recommendation" if level == AutonomyLevel.VALIDATED else "recommended"
        message = (
            f"Human approval required for: {plan.description}\n"
            f"Proposed commands: {plan.commands}\n"
            f"Rollback: {plan.rollback_commands}"
        )
        if plan.dry_run_output:
            message += f"\nDry-run trace written to {DRY_RUN_LOG}"

        return {
            "fingerprint": plan.issue_fingerprint,
            "status": status,
            "autonomy_level": level.value,
            "confidence": plan.confidence,
            "message": message,
            "yaml_path": str(yaml_path) if yaml_path else None,
            "dry_run": plan.dry_run_output or None,
        }

    # -- apply (human-approved execution path) ----------------------------

    def apply(self, plan: RemediationPlan) -> dict[str, Any]:
        """Execute the plan. **Only call after human approval.**

        Snapshots protected configs, runs the commands sequentially, and rolls
        back on the first failure. Updates the issue record's success/fail
        counters so that confidence reflects real outcomes.
        """
        snapshots = snapshot_configs(PROTECTED_PATHS)
        plan.config_snapshots = snapshots
        result: dict[str, Any] = {
            "fingerprint": plan.issue_fingerprint,
            "status": "skipped",
        }

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

        record = self.state.get(plan.issue_fingerprint)
        if record:
            if success:
                record.successful_fixes += 1
            else:
                record.failed_fixes += 1
            save_state(self.state)

        return result


# ---------------------------------------------------------------------------
# CLI entry point (standalone testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    engine = RemediationEngine()

    sample_log = "nginx[1234]: connect() to 10.0.0.5:8080 failed (111: Connection refused)"
    level, record, conf = engine.assess(sample_log, unit="nginx.service")

    print(f"Fingerprint : {record.fingerprint}")
    print(f"Occurrences : {record.occurrences}")
    print(f"Confidence  : {conf:.2%}")
    print(f"Autonomy    : {level.value.upper()}")

    plan = RemediationPlan(
        issue_fingerprint=record.fingerprint,
        description="Restart nginx after upstream connection failure",
        commands=["systemctl restart nginx"],
        rollback_commands=["systemctl stop nginx", "systemctl start nginx@previous"],
        confidence=conf,
    )

    result = engine.recommend(plan, level)
    print(f"\nRecommendation: {json.dumps(result, indent=2)}")
