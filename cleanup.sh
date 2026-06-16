#!/usr/bin/env bash
# RootMedic cleanup — removes every trace of a RootMedic installation.
#
# Usage:
#   sudo bash cleanup.sh              # interactive (asks for confirmation)
#   sudo bash cleanup.sh --force      # skip confirmation prompt
#   sudo ROOTMEDIC_NON_INTERACTIVE=1 bash cleanup.sh   # CI / scripted removal
#
# Scope (single node):
#   - systemd service + CLI shim
#   - install directory (/opt/rootmedic by default)
#   - config directory (/etc/rootmedic)
#   - Fluent Bit config + native service + repo file
#   - Loki / Fluent Bit / Grafana containers and (optionally) their images
#   - Runtime state files and log directory
#
# Future expansion: multi-node / cluster cleanup will be added below the
# "CLUSTER CLEANUP HOOKS" section at the bottom of this file.
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/rootmedic}"
ROOTMEDIC_NON_INTERACTIVE="${ROOTMEDIC_NON_INTERACTIVE:-0}"
FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

# ─── Colour helpers ──────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  RED='\033[1;31m'; YELLOW='\033[1;33m'; GREEN='\033[1;32m'
  CYAN='\033[1;36m'; BOLD='\033[1m'; RESET='\033[0m'
else
  RED=''; YELLOW=''; GREEN=''; CYAN=''; BOLD=''; RESET=''
fi

log()  { echo -e "${CYAN}[*]${RESET} $*"; }
ok()   { echo -e "${GREEN}[✓]${RESET} $*"; }
warn() { echo -e "${YELLOW}[!]${RESET} $*"; }
die()  { echo -e "${RED}[✗]${RESET} $*" >&2; exit 1; }

# ─── Root check ──────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || die "Run as root:  sudo bash cleanup.sh"

# ─── Caution banner ──────────────────────────────────────────────────────────
echo
echo -e "${RED}${BOLD}╔══════════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${RED}${BOLD}║                   ⚠  DESTRUCTIVE OPERATION  ⚠                   ║${RESET}"
echo -e "${RED}${BOLD}╠══════════════════════════════════════════════════════════════════╣${RESET}"
echo -e "${RED}${BOLD}║                                                                  ║${RESET}"
echo -e "${RED}${BOLD}║  This script will permanently delete:                            ║${RESET}"
echo -e "${RED}${BOLD}║    • RootMedic service, config, and all runtime data             ║${RESET}"
echo -e "${RED}${BOLD}║    • Fluent Bit config and (if native) its service + repo        ║${RESET}"
echo -e "${RED}${BOLD}║    • Loki, Grafana, and Fluent Bit containers                   ║${RESET}"
echo -e "${RED}${BOLD}║    • All incident archives and remediation history               ║${RESET}"
echo -e "${RED}${BOLD}║                                                                  ║${RESET}"
echo -e "${RED}${BOLD}║  There is NO undo. Back up anything you need first.              ║${RESET}"
echo -e "${RED}${BOLD}║                                                                  ║${RESET}"
echo -e "${RED}${BOLD}╚══════════════════════════════════════════════════════════════════╝${RESET}"
echo

# ─── Confirmation ─────────────────────────────────────────────────────────────
_confirm() {
  if [[ "${FORCE}" == "1" || "${ROOTMEDIC_NON_INTERACTIVE}" == "1" ]]; then
    warn "Running in non-interactive / forced mode — skipping confirmation."
    return 0
  fi
  echo -e "${BOLD}Type  yes-delete-rootmedic  to confirm, or anything else to abort:${RESET}"
  read -r answer < /dev/tty || true
  if [[ "${answer}" != "yes-delete-rootmedic" ]]; then
    echo "Aborted — nothing was changed."
    exit 0
  fi
}

_confirm

# ─── Detect container runtime ────────────────────────────────────────────────
_runtime() {
  command -v podman >/dev/null && echo "podman" && return
  command -v docker >/dev/null && echo "docker" && return
  echo ""
}
RUNTIME=$(_runtime)

# ─── 1. Stop and disable systemd service ─────────────────────────────────────
log "Stopping RootMedic service..."
if systemctl is-active --quiet rootmedic 2>/dev/null; then
  systemctl stop rootmedic
fi
if systemctl is-enabled --quiet rootmedic 2>/dev/null; then
  systemctl disable rootmedic
fi
rm -f /etc/systemd/system/rootmedic.service
systemctl daemon-reload
ok "rootmedic.service removed."

# ─── 2. Remove CLI shim ───────────────────────────────────────────────────────
rm -f /usr/local/bin/rootmedic
ok "CLI shim removed."

# ─── 3. Remove install directory (source + venv + runtime artifacts) ─────────
if [[ -d "${INSTALL_DIR}" ]]; then
  log "Removing install directory ${INSTALL_DIR} ..."
  rm -rf "${INSTALL_DIR}"
  ok "Install directory removed."
else
  warn "Install directory not found: ${INSTALL_DIR}"
fi

# ─── 4. Remove config directory ───────────────────────────────────────────────
rm -rf /etc/rootmedic
ok "Config directory /etc/rootmedic removed."

# ─── 5. Remove log directory ─────────────────────────────────────────────────
rm -rf /var/log/rootmedic
ok "Log directory /var/log/rootmedic removed."

# ─── 6. Fluent Bit ───────────────────────────────────────────────────────────
log "Stopping Fluent Bit service (if native)..."
if systemctl is-active --quiet fluent-bit 2>/dev/null; then
  systemctl stop fluent-bit
fi
if systemctl is-enabled --quiet fluent-bit 2>/dev/null; then
  systemctl disable fluent-bit
fi

rm -rf /etc/fluent-bit

# Remove package repo files so a future install starts clean
rm -f /etc/apt/sources.list.d/fluentbit.list
rm -f /usr/share/keyrings/fluentbit-keyring.gpg
rm -f /etc/yum.repos.d/fluentbit.repo
ok "Fluent Bit config and repo files removed."

# Uninstall the native fluent-bit package if present
if command -v fluent-bit >/dev/null 2>&1; then
  log "Uninstalling native Fluent Bit package..."
  if command -v apt-get >/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get remove -y -qq fluent-bit 2>/dev/null || true
  elif command -v dnf >/dev/null; then
    dnf remove -y -q fluent-bit 2>/dev/null || true
  elif command -v apk >/dev/null; then
    apk del fluent-bit 2>/dev/null || true
  fi
  ok "Native Fluent Bit package removed."
fi

# ─── 7. Containers ────────────────────────────────────────────────────────────
if [[ -n "${RUNTIME}" ]]; then
  log "Removing containers (loki, grafana, fluent-bit)..."
  for ctr in loki grafana fluent-bit; do
    if ${RUNTIME} container exists "${ctr}" 2>/dev/null || \
       ${RUNTIME} ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${ctr}$"; then
      ${RUNTIME} rm -f "${ctr}" 2>/dev/null && log "  removed container: ${ctr}" || true
    fi
  done
  ok "Containers removed."

  # Remove pulled images — ask separately (they're large but can be re-pulled)
  _remove_images=0
  if [[ "${FORCE}" == "1" || "${ROOTMEDIC_NON_INTERACTIVE}" == "1" ]]; then
    _remove_images=1
  else
    echo
    echo -ne "${BOLD}Also remove pulled container images? This frees disk space [y/N]: ${RESET}"
    read -r _img_ans < /dev/tty || true
    [[ "${_img_ans,,}" =~ ^y(es)?$ ]] && _remove_images=1
  fi

  if [[ "${_remove_images}" == "1" ]]; then
    log "Removing RootMedic container images..."
    for img in \
      "docker.io/grafana/loki:2.9.0" \
      "docker.io/fluent/fluent-bit:latest" \
      "docker.io/grafana/grafana:10.2.3"; do
      ${RUNTIME} rmi -f "${img}" 2>/dev/null && log "  removed: ${img}" || true
    done
    ok "Container images removed."
  fi
else
  warn "No container runtime found — skipping container removal."
fi

# Remove the temp compose file if present
rm -f /tmp/rootmedic-loki-stack.yml

# ─── 8. Podman compose leftover volumes (best-effort) ────────────────────────
if [[ "${RUNTIME}" == "podman" ]]; then
  podman volume prune -f 2>/dev/null || true
fi

# ─── CLUSTER CLEANUP HOOKS ───────────────────────────────────────────────────
# Future: iterate over inventory nodes (Ansible / SSH loop) and run the single-
# node steps above on each remote host.  Placeholder:
#
#   _cluster_cleanup() {
#     local inventory="${1:-inventory.ini}"
#     ansible all -i "${inventory}" -m shell \
#       -a "bash /tmp/cleanup.sh --force" --become
#   }
#
#   if [[ "${CLUSTER_MODE:-0}" == "1" ]]; then
#     _cluster_cleanup "${INVENTORY:-inventory.ini}"
#   fi
# ─────────────────────────────────────────────────────────────────────────────

echo
echo -e "${GREEN}${BOLD}RootMedic has been completely removed from this host.${RESET}"
echo "  Re-install any time:  curl -fsSL <installer-url> | sudo bash"
echo
