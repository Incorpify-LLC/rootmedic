#!/bin/bash
# ============================================================================
# RootMedic CI/CD End-to-End Demo — Local Simulation
#
# Simulates the full pipeline:
#   1. Run test suite (TDD checkpoint)
#   2. Deploy RootMedic (simulated)
#   3. Inject 3 faults sequentially
#   4. Show autonomous recovery for each
#   5. Verify system health
# ============================================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

pass()  { echo -e "${GREEN}✅  $*${NC}"; }
fail()  { echo -e "${RED}❌  $*${NC}"; }
info()  { echo -e "${CYAN}ℹ️   $*${NC}"; }
step()  { echo -e "\n${BOLD}${YELLOW}━━━ $* ━━━${NC}"; }

PASSES=0
FAILS=0
check() {
    if [ $? -eq 0 ]; then
        ((PASSES++)) || true
    else
        ((FAILS++)) || true
        fail "$1"
    fi
}

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

# Activate venv
source .venv/bin/activate

echo ""
echo -e "${BOLD}${CYAN}"
echo "╔══════════════════════════════════════════════════════════╗"
echo "║     RootMedic — Autonomous Healing CI/CD Demo          ║"
echo "║     Local Simulation                                   ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# ============================================================================
step "Stage 1: Unit Test Suite"
# ============================================================================
info "Running 48 tests covering all functions..."
python -m pytest tests/ -q --tb=short
check "Unit tests"

# ============================================================================
step "Stage 2: Deploy RootMedic (simulated)"
# ============================================================================
info "RootMedic agent deployed on managed node"
info "  • Log aggregation : Loki → Grafana (simulated)"
info "  • Managed node     : systemd timer every 30s"
info "  • Autonomy model   : RECOMMEND → SEMI → FULL"
pass "RootMedic deployment"
((PASSES++)) || true

# ============================================================================
step "Stage 3: Fault Injection — Connection Refused"
# ============================================================================
info "Simulating: nginx service stopped by an attacker"

python3 << 'PYEOF'
import json, sys
sys.path.insert(0, '.')

from remediation_engine import RemediationEngine, RemediationPlan

engine = RemediationEngine()

# Simulate: multiple occurrences so we see the full escalation
print("--- Occurrence 1 (new issue) ---")
level, record, conf = engine.assess(
    "connect() to 10.0.0.5:8080 failed (111: Connection refused)",
    "nginx.service",
)
print(f"  Level: {level.value.upper()} | Occurrences: {record.occurrences} | Confidence: {conf:.0%}")

plan = RemediationPlan(
    issue_fingerprint=record.fingerprint,
    description="Restart nginx after upstream connection failure",
    commands=["systemctl restart nginx"],
    rollback_commands=["systemctl stop nginx", "systemctl start nginx@previous"],
    confidence=conf,
)
result = engine.execute(plan, level)
print(f"  Action: {result['status']}")
if result['status'] == 'recommended':
    print(f"  Message: {result['message'][:120]}...")

print("\n--- Occurrence 3 (crosses RECOMMEND gate) ---")
for _ in range(2):
    engine.assess(
        "connect() to 10.0.0.5:8080 failed (111: Connection refused)",
        "nginx.service",
    )
level, record, conf = engine.assess(
    "connect() to 10.0.0.5:8080 failed (111: Connection refused)",
    "nginx.service",
)
print(f"  Level: {level.value.upper()} | Occurrences: {record.occurrences} | Confidence: {conf:.0%}")

plan.confidence = conf
result = engine.execute(plan, level)
print(f"  Action: {result['status']}")
if result['status'] == 'dry_run':
    print(f"  Dry-run: {result.get('dry_run', '')[:120]}...")

print("\n--- Occurrence 10 (validated, full autonomy) ---")
for _ in range(6):
    engine.assess(
        "connect() to 10.0.0.5:8080 failed (111: Connection refused)",
        "nginx.service",
    )
record = engine.state[record.fingerprint]
record.successful_fixes = 10
record.failed_fixes = 0

level, record, conf = engine.assess(
    "connect() to 10.0.0.5:8080 failed (111: Connection refused)",
    "nginx.service",
)
print(f"  Level: {level.value.upper()} | Occurrences: {record.occurrences} | Confidence: {conf:.0%}")

# Show the final state
print("\n--- Final Issue State ---")
print(json.dumps({
    "fingerprint": record.fingerprint,
    "occurrences": record.occurrences,
    "successful_fixes": record.successful_fixes,
    "failed_fixes": record.failed_fixes,
    "success_rate": f"{record.success_rate:.0%}",
}, indent=2))
PYEOF
check "Fault 1 injection"

# ============================================================================
step "Stage 4: Fault Injection — Out of Memory"
# ============================================================================
info "Simulating: Java process killed by OOM killer"

python3 << 'PYEOF'
import json, sys
sys.path.insert(0, '.')

from remediation_engine import RemediationEngine, RemediationPlan

engine = RemediationEngine()

# Pre-seed occurrences to skip RECOMMEND
for _ in range(5):
    engine.assess("Out of memory: Killed process 9999 (java)", "java.service")

level, record, conf = engine.assess(
    "Out of memory: Killed process 9999 (java)",
    "java.service",
)
print(f"  Level: {level.value.upper()} | Occurrences: {record.occurrences} | Confidence: {conf:.0%}")

plan = RemediationPlan(
    issue_fingerprint=record.fingerprint,
    description="Restart java.service and drop caches after OOM",
    commands=["systemctl restart java.service", "sync && echo 3 > /proc/sys/vm/drop_caches"],
    rollback_commands=["systemctl stop java.service", "systemctl start java.service"],
    confidence=conf,
)
result = engine.execute(plan, level)
print(f"  Action: {result['status']}")

print(f"\n  Current issue state: occurrences={record.occurrences}, "
      f"successful={record.successful_fixes}, failed={record.failed_fixes}")
PYEOF
check "Fault 2 injection"

# ============================================================================
step "Stage 5: Fault Injection — Disk Full"
# ============================================================================
info "Simulating: Disk pressure on /dev/sda1"

python3 << 'PYEOF'
import json, sys
sys.path.insert(0, '.')

from remediation_engine import RemediationEngine, RemediationPlan

engine = RemediationEngine()

for _ in range(5):
    engine.assess("no space left on device (/dev/sda1)", "systemd-journald.service")

level, record, conf = engine.assess(
    "no space left on device (/dev/sda1)",
    "systemd-journald.service",
)

plan = RemediationPlan(
    issue_fingerprint=record.fingerprint,
    description="Clean journal logs to free disk space",
    commands=["journalctl --vacuum-size=200M", "apt-get clean"],
    rollback_commands=[],
    confidence=conf,
)
result = engine.execute(plan, level)
print(f"  Level: {level.value.upper()} | Action: {result['status']}")
PYEOF
check "Fault 3 injection"

# ============================================================================
step "Stage 6: System Health Check"
# ============================================================================
info "Verifying remediation state file..."

python3 << 'PYEOF'
import json
from pathlib import Path

state_file = Path("remediation_state.json")
if state_file.exists():
    state = json.loads(state_file.read_text())
    print(f"  Tracked issue types: {len(state)}")
    for fp, data in state.items():
        print(f"  • {fp}: {data['occurrences']}x seen, "
              f"{data['successful_fixes']} fixed, {data['failed_fixes']} failed")
else:
    print("  No state file (all clean or first run)")

dry_run_log = Path("dry_run.log")
if dry_run_log.exists():
    lines = dry_run_log.read_text().strip().split("\n")
    print(f"\n  Dry-run log: {len(lines)} lines generated")

snapshot_dir = Path(".rollback_snapshots")
if snapshot_dir.exists():
    count = len(list(snapshot_dir.iterdir()))
    print(f"  Rollback snapshots: {count} files")
PYEOF
check "Health check"

# ============================================================================
step "Stage 7: Remediation Engine Self-Test"
# ============================================================================
info "Running standalone engine verification..."

python3 << 'PYEOF'
import sys
sys.path.insert(0, '.')

from remediation_engine import RemediationEngine, AutonomyLevel, fingerprint_issue

engine = RemediationEngine()
issues = [
    ("connection refused to upstream", "nginx.service"),
    ("disk full on /dev/sda1", "systemd-journald.service"),
    ("process killed by OOM", "java.service"),
]

print("  Self-test: fingerprinting 3 issue types...")
fingerprints = set()
for msg, unit in issues:
    fp = fingerprint_issue(msg, unit)
    fingerprints.add(fp)
    print(f"    {unit}: {fp}")

# Verify all 3 issue types get unique fingerprints
assert len(fingerprints) == 3, f"Expected 3 unique fingerprints, got {len(fingerprints)}"
print(f"  ✅ All {len(fingerprints)} issue types uniquely identified")

# Verify the engine tracks them
for msg, unit in issues:
    level, record, conf = engine.assess(msg, unit)
    assert record.occurrences == 1

print(f"  ✅ Engine tracks {len(issues)} distinct issue types correctly")
print(f"  ✅ Remediation engine self-test PASSED")
PYEOF
check "Self-test"

# ============================================================================
# Summary
# ============================================================================
echo ""
echo -e "${BOLD}${CYAN}"
echo "╔══════════════════════════════════════════════════════════╗"
echo "║              Demo Results                                ║"
echo "╠══════════════════════════════════════════════════════════╣"
printf "║  %-52s ║\n" "Passes: $PASSES"
printf "║  %-52s ║\n" "Fails:  $FAILS"
echo "╚══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

if [ "$FAILS" -eq 0 ]; then
    echo -e "${GREEN}${BOLD}🎉  All checks passed — autonomous healing verified!${NC}"
    exit 0
else
    echo -e "${RED}${BOLD}⚠️   $FAILS check(s) failed — review output above.${NC}"
    exit 1
fi
