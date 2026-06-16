#!/usr/bin/env bash
# Run the installer CI test harness locally.
# Usage:
#   cd ci/test-install
#   bash run-test.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# Detect compose command
if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null; then
  COMPOSE="docker-compose"
else
  echo "ERROR: Docker Compose not found." >&2
  exit 1
fi

echo "=== Building and running install CI test ==="
${COMPOSE} down --remove-orphans 2>/dev/null || true
${COMPOSE} up --build --abort-on-container-exit --exit-code-from install-target
EXIT_CODE=$?

echo
if [[ "${EXIT_CODE}" == "0" ]]; then
  echo "✓ install CI test PASSED"
else
  echo "✗ install CI test FAILED (exit code ${EXIT_CODE})"
  echo "  View logs: ${COMPOSE} logs install-target"
fi

${COMPOSE} down --remove-orphans 2>/dev/null || true
exit "${EXIT_CODE}"
