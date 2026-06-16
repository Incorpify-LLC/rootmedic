#!/usr/bin/env bash
# Runs inside the install-target container.
# Copies the local repo into INSTALL_DIR (simulating a git clone from disk)
# then runs install.sh in non-interactive mode.
set -euo pipefail

REPO_DIR="${REPO_DIR:-/repo}"
INSTALL_DIR="${INSTALL_DIR:-/opt/rootmedic}"
LOKI_URL="${LOKI_URL:-http://loki:3100}"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://mock-llm:8080}"
LITELLM_MODEL="${LITELLM_MODEL:-mock-model}"
LITELLM_API_KEY="${LITELLM_API_KEY:-ci-test-key}"
LLM_TYPE="${LLM_TYPE:-external}"

echo "=== RootMedic install CI test ==="
echo "  LOKI_URL         = ${LOKI_URL}"
echo "  LITELLM_BASE_URL = ${LITELLM_BASE_URL}"
echo "  LITELLM_MODEL    = ${LITELLM_MODEL}"

# Wait for Loki to be ready
echo "[ci] Waiting for Loki at ${LOKI_URL} ..."
for i in $(seq 1 30); do
  code=$(curl -fsS -o /dev/null -w "%{http_code}" --connect-timeout 3 "${LOKI_URL}/ready" 2>/dev/null || echo "000")
  [[ "${code}" == "200" ]] && echo "[ci] Loki ready." && break
  echo "[ci] Not ready yet (attempt ${i}/30)..."
  sleep 2
done
[[ "${code}" == "200" ]] || { echo "[ci] ERROR: Loki never became ready."; exit 1; }

# Wait for mock-llm to be ready
echo "[ci] Waiting for mock-LLM at ${LITELLM_BASE_URL} ..."
for i in $(seq 1 15); do
  code=$(curl -fsS -o /dev/null -w "%{http_code}" --connect-timeout 3 "${LITELLM_BASE_URL}/ready" 2>/dev/null || echo "000")
  [[ "${code}" == "200" ]] && echo "[ci] Mock LLM ready." && break
  sleep 2
done
[[ "${code}" == "200" ]] || { echo "[ci] ERROR: Mock LLM never became ready."; exit 1; }

# Simulate a pre-cloned repo (avoids needing GitHub in CI)
echo "[ci] Copying repo from ${REPO_DIR} to ${INSTALL_DIR} ..."
mkdir -p "$(dirname "${INSTALL_DIR}")"
cp -r "${REPO_DIR}" "${INSTALL_DIR}"

# Run the installer in non-interactive mode
echo "[ci] Running install.sh ..."
ROOTMEDIC_NON_INTERACTIVE=1 \
LOKI_URL="${LOKI_URL}" \
LLM_TYPE="${LLM_TYPE}" \
LITELLM_BASE_URL="${LITELLM_BASE_URL}" \
LITELLM_MODEL="${LITELLM_MODEL}" \
LITELLM_API_KEY="${LITELLM_API_KEY}" \
START_LOKI_IF_DOWN=0 \
INSTALL_DIR="${INSTALL_DIR}" \
ROOTMEDIC_REPO="file://${REPO_DIR}" \
  bash "${INSTALL_DIR}/install.sh"

echo
echo "=== Assertions ==="

# 1. Config file written
[[ -f /etc/rootmedic/config.yaml ]] || { echo "FAIL: config file missing"; exit 1; }
echo "PASS: /etc/rootmedic/config.yaml exists"

# 2. Config contains correct Loki URL
grep -q "${LOKI_URL}" /etc/rootmedic/config.yaml || { echo "FAIL: LOKI_URL not in config"; exit 1; }
echo "PASS: LOKI_URL in config"

# 3. Config contains LLM base URL
grep -q "${LITELLM_BASE_URL}" /etc/rootmedic/config.yaml || { echo "FAIL: LITELLM_BASE_URL not in config"; exit 1; }
echo "PASS: LITELLM_BASE_URL in config"

# 4. CLI shim installed
[[ -x /usr/local/bin/rootmedic ]] || { echo "FAIL: /usr/local/bin/rootmedic missing"; exit 1; }
echo "PASS: /usr/local/bin/rootmedic installed"

# 5. Python venv functional
"${INSTALL_DIR}/.venv/bin/python" -c "import requests, yaml; print('deps ok')" \
  || { echo "FAIL: venv or deps broken"; exit 1; }
echo "PASS: Python venv and deps OK"

# 6. Fluent Bit config written
[[ -f /etc/fluent-bit/fluent-bit.conf ]] || { echo "FAIL: fluent-bit config missing"; exit 1; }
echo "PASS: /etc/fluent-bit/fluent-bit.conf written"

echo
echo "=== ALL ASSERTIONS PASSED ==="
