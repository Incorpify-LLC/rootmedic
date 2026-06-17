#!/usr/bin/env bash
# Developer utility — push scripts to the test VM.
#
# Default behaviour: SCP only. SSH into the VM yourself and run the scripts
# there. This avoids SSH timeout issues during long operations (image pulls,
# LLM connectivity tests, etc.).
#
# Prerequisites — one-time SSH key setup:
#   ssh-keygen -t ed25519 -C rootmedic-dev   # skip if you already have a key
#   ssh-copy-id sanjayu@192.168.2.177
#
# Usage:
#   bash dev-deploy.sh                  # SCP scripts → print what to run on VM
#   bash dev-deploy.sh --remote-install # also SSH-exec the installer (may time out)
#   bash dev-deploy.sh --verify         # SCP + SSH-exec verify_install.sh only
#
# Environment overrides:
#   TARGET    SSH destination   (default: sanjayu@192.168.2.177)
#   LLM_URL   Ollama base URL   (default: http://192.168.2.112:11434)
#   MODEL     Model name        (default: llama3.2)
set -euo pipefail

TARGET="${TARGET:-sanjayu@192.168.2.177}"
LLM_URL="${LLM_URL:-http://192.168.2.112:11434}"
MODEL="${MODEL:-llama3.2}"
REMOTE_DIR="/home/sanjayu"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # repo's scripts/ — all scripts live here
SSH_OPTS="-o ServerAliveInterval=15 -o ServerAliveCountMax=20 -o ConnectTimeout=10"

REMOTE_INSTALL=0
VERIFY_ONLY=0
for arg in "$@"; do
  case "${arg}" in
    --remote-install) REMOTE_INSTALL=1 ;;
    --verify)         VERIFY_ONLY=1 ;;
  esac
done

if [[ -t 1 ]]; then
  CYAN='\033[1;36m'; GREEN='\033[1;32m'; YELLOW='\033[1;33m'
  BOLD='\033[1m'; RESET='\033[0m'
else
  CYAN=''; GREEN=''; YELLOW=''; BOLD=''; RESET=''
fi
step() { echo -e "\n${BOLD}${CYAN}▶  $*${RESET}"; }
ok()   { echo -e "${GREEN}   ✓${RESET}  $*"; }
warn() { echo -e "${YELLOW}   !${RESET}  $*"; }
cmd()  { echo -e "   ${CYAN}${*}${RESET}"; }

# ─── Pre-flight: key auth ────────────────────────────────────────────────────
step "Checking SSH key auth to ${TARGET} ..."
if ! ssh ${SSH_OPTS} -o BatchMode=yes -o PasswordAuthentication=no \
     "${TARGET}" true 2>/dev/null; then
  echo
  warn "SSH key auth not configured for ${TARGET}."
  echo "  Set it up once:"
  cmd "ssh-keygen -t ed25519 -C rootmedic-dev"
  cmd "ssh-copy-id ${TARGET}"
  echo
  exit 1
fi
ok "Key auth OK."

# ─── SCP ─────────────────────────────────────────────────────────────────────
step "Uploading scripts to ${TARGET}:${REMOTE_DIR}/ ..."
scp ${SSH_OPTS} \
  "${SCRIPT_DIR}/install.sh" \
  "${SCRIPT_DIR}/cleanup.sh" \
  "${SCRIPT_DIR}/verify_install.sh" \
  "${TARGET}:${REMOTE_DIR}/"
ok "Scripts ready on VM."

# ─── Verify-only path ────────────────────────────────────────────────────────
if [[ "${VERIFY_ONLY}" == "1" ]]; then
  step "Running verify_install.sh on ${TARGET} ..."
  ssh ${SSH_OPTS} -t "${TARGET}" "sudo bash ${REMOTE_DIR}/verify_install.sh"
  exit $?
fi

# ─── Remote install (opt-in, may time out on slow LLM) ───────────────────────
if [[ "${REMOTE_INSTALL}" == "1" ]]; then
  step "Running non-interactive installer on ${TARGET} ..."
  warn "SSH may time out during image pulls or LLM test — consider running on VM directly."
  echo "  LLM: ${LLM_URL}  (${MODEL})"
  echo
  ssh ${SSH_OPTS} -t "${TARGET}" "sudo \
    ROOTMEDIC_NON_INTERACTIVE=1 \
    LLM_TYPE=lan_ollama \
    LITELLM_BASE_URL=${LLM_URL} \
    LITELLM_MODEL=${MODEL} \
    LITELLM_API_KEY=ollama \
    LOKI_URL=http://localhost:3100 \
    START_LOKI_IF_DOWN=1 \
    bash ${REMOTE_DIR}/install.sh"
  ssh ${SSH_OPTS} "${TARGET}" \
    "sudo install -m 0755 ${REMOTE_DIR}/verify_install.sh /opt/rootmedic/scripts/verify_install.sh 2>/dev/null || true"
  echo
  ok "Done.  Run the demo:"
  cmd "bash dev-deploy.sh --verify"
  exit 0
fi

# ─── Default: SCP done — print what to do next ───────────────────────────────
echo
echo -e "${GREEN}${BOLD}  Scripts uploaded. SSH in and run:${RESET}"
echo
echo -e "  ${BOLD}1. Connect:${RESET}"
cmd "ssh ${TARGET}"
echo
echo -e "  ${BOLD}2. Clean previous install (if needed):${RESET}"
cmd "sudo bash ~/cleanup.sh"
echo
echo -e "  ${BOLD}3. Install:${RESET}"
cmd "sudo LLM_TYPE=lan_ollama \\"
cmd "     LITELLM_BASE_URL=${LLM_URL} \\"
cmd "     LITELLM_MODEL=${MODEL} \\"
cmd "     LITELLM_API_KEY=ollama \\"
cmd "     START_LOKI_IF_DOWN=1 \\"
cmd "     bash ~/install.sh"
echo
echo -e "  ${BOLD}4. Run the live healing demo:${RESET}"
cmd "sudo bash ~/verify_install.sh"
echo
