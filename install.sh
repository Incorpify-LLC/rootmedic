#!/usr/bin/env bash
# RootMedic installer — autonomous AI medic for Linux systems.
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Incorpify-LLC/rootmedic/main/install.sh | sudo bash
#   curl -fsSL https://raw.githubusercontent.com/Incorpify-LLC/rootmedic/main/install.sh | sudo LITELLM_API_KEY=sk-... bash
#
# Environment overrides:
#   LITELLM_API_KEY    LiteLLM proxy API key (prompted interactively if unset)
#   LITELLM_BASE_URL   Default: https://litellm.saneax.in
#   LITELLM_MODEL      Default: smart
#   ROOTMEDIC_REPO     Default: https://github.com/Incorpify-LLC/rootmedic.git
#   ROOTMEDIC_BRANCH   Default: main
#   INSTALL_DIR        Default: /opt/rootmedic
set -euo pipefail

LITELLM_BASE_URL="${LITELLM_BASE_URL:-https://litellm.saneax.in}"
LITELLM_MODEL="${LITELLM_MODEL:-smart}"
ROOTMEDIC_REPO="${ROOTMEDIC_REPO:-https://github.com/Incorpify-LLC/rootmedic.git}"
ROOTMEDIC_BRANCH="${ROOTMEDIC_BRANCH:-main}"
INSTALL_DIR="${INSTALL_DIR:-/opt/rootmedic}"
CONFIG_DIR="/etc/rootmedic"
CONFIG_FILE="${CONFIG_DIR}/config.yaml"
LOG_DIR="/var/log/rootmedic"
SERVICE_FILE="/etc/systemd/system/rootmedic.service"

banner() {
  cat <<'EOF'
   ____             _   __  __          _ _
  |  _ \ ___   ___ | |_|  \/  | ___  __| (_) ___
  | |_) / _ \ / _ \| __| |\/| |/ _ \/ _` | |/ __|
  |  _ < (_) | (_) | |_| |  | |  __/ (_| | | (__
  |_| \_\___/ \___/ \__|_|  |_|\___|\__,_|_|\___|

      Autonomous AI medic for Linux systems
      Powered by LiteLLM at https://litellm.saneax.in
EOF
}

log()  { echo -e "\033[1;36m[*]\033[0m $*"; }
ok()   { echo -e "\033[1;32m[\xe2\x9c\x93]\033[0m $*"; }
warn() { echo -e "\033[1;33m[!]\033[0m $*"; }
die()  { echo -e "\033[1;31m[x]\033[0m $*" >&2; exit 1; }

require_root() {
  [[ $EUID -eq 0 ]] || die "Run as root: curl ... | sudo bash"
}

require_linux() {
  [[ "$(uname -s)" == "Linux" ]] || die "RootMedic only supports Linux."
}

detect_distro() {
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    echo "${PRETTY_NAME:-${NAME:-unknown}}"
  else
    echo "unknown"
  fi
}

install_deps() {
  log "Installing OS packages (python3, git, curl, jq, venv)..."
  if   command -v apt-get >/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
      python3 python3-venv python3-pip git curl jq ca-certificates
  elif command -v dnf >/dev/null; then
    dnf install -y -q python3 python3-pip git curl jq ca-certificates
  elif command -v pacman >/dev/null; then
    pacman -Sy --noconfirm --needed python python-pip git curl jq ca-certificates
  elif command -v apk >/dev/null; then
    apk add --no-cache python3 py3-pip git curl jq ca-certificates
  elif command -v zypper >/dev/null; then
    zypper --non-interactive install -y python3 python3-pip git curl jq ca-certificates
  else
    die "No supported package manager found (apt/dnf/pacman/apk/zypper)."
  fi
  ok "Dependencies installed."
}

prompt_api_key() {
  if [[ -n "${LITELLM_API_KEY:-}" ]]; then
    return
  fi
  # When piped through `curl | bash`, stdin is the script — read from /dev/tty.
  if [[ ! -t 0 && ! -e /dev/tty ]]; then
    die "LITELLM_API_KEY not set and no terminal available. Re-run as: LITELLM_API_KEY=sk-... curl ... | sudo -E bash"
  fi
  local tty_src
  tty_src=$([[ -t 0 ]] && echo /dev/stdin || echo /dev/tty)
  echo
  echo "RootMedic uses LiteLLM at ${LITELLM_BASE_URL} (model: ${LITELLM_MODEL})."
  echo "Paste your LiteLLM API key (input is hidden):"
  read -rs LITELLM_API_KEY < "$tty_src" || die "Could not read API key."
  echo
  [[ -n "$LITELLM_API_KEY" ]] || die "Empty API key. Aborting."
}

clone_or_update_repo() {
  if [[ -d "${INSTALL_DIR}/.git" ]]; then
    log "Updating existing repo at ${INSTALL_DIR}..."
    git -C "${INSTALL_DIR}" fetch --quiet origin "${ROOTMEDIC_BRANCH}"
    git -C "${INSTALL_DIR}" reset --hard "origin/${ROOTMEDIC_BRANCH}"
  else
    log "Cloning ${ROOTMEDIC_REPO} (branch: ${ROOTMEDIC_BRANCH}) into ${INSTALL_DIR}..."
    mkdir -p "$(dirname "${INSTALL_DIR}")"
    git clone --depth 1 --branch "${ROOTMEDIC_BRANCH}" --quiet \
      "${ROOTMEDIC_REPO}" "${INSTALL_DIR}"
  fi
  ok "Source ready at ${INSTALL_DIR}."
}

setup_venv() {
  log "Creating Python virtualenv..."
  python3 -m venv "${INSTALL_DIR}/.venv"
  "${INSTALL_DIR}/.venv/bin/pip" install --quiet --upgrade pip
  "${INSTALL_DIR}/.venv/bin/pip" install --quiet -r "${INSTALL_DIR}/requirements.txt"
  "${INSTALL_DIR}/.venv/bin/pip" install --quiet pyyaml
  ok "Python environment ready."
}

write_config() {
  log "Writing config to ${CONFIG_FILE}..."
  mkdir -p "${CONFIG_DIR}" "${LOG_DIR}"
  umask 077
  cat > "${CONFIG_FILE}" <<EOF
# RootMedic configuration — written by install.sh
litellm_base_url: "${LITELLM_BASE_URL}"
litellm_model: "${LITELLM_MODEL}"
litellm_api_key: "${LITELLM_API_KEY}"

# Loki endpoint used by fetch_normalize_logs.py
loki_url: "http://localhost:3100/loki/api/v1/query_range"

# Alerting (optional)
slack_webhook_url: ""
dedup_window_minutes: 15
escalation_after_minutes: 30
grafana_base_url: "http://localhost:3000"
EOF
  chmod 600 "${CONFIG_FILE}"
  ok "Config written (mode 600)."
}

write_cli_shim() {
  log "Installing /usr/local/bin/rootmedic CLI..."
  cat > /usr/local/bin/rootmedic <<EOF
#!/usr/bin/env bash
cd "${INSTALL_DIR}"
exec "${INSTALL_DIR}/.venv/bin/python" "${INSTALL_DIR}/fetch_normalize_logs.py" "\$@"
EOF
  chmod +x /usr/local/bin/rootmedic
  ok "CLI installed: run 'rootmedic' to invoke the agent."
}

write_systemd_unit() {
  log "Installing systemd unit ${SERVICE_FILE}..."
  cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=RootMedic — autonomous AI medic for Linux
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/.venv/bin/python ${INSTALL_DIR}/fetch_normalize_logs.py
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now rootmedic.service
  ok "rootmedic.service enabled and started."
}

verify_install() {
  log "Verifying LiteLLM connectivity..."
  local http_code
  http_code=$(curl -fsS -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer ${LITELLM_API_KEY}" \
    "${LITELLM_BASE_URL}/v1/models" 2>/dev/null || echo "000")
  if [[ "$http_code" == "200" ]]; then
    ok "LiteLLM reachable at ${LITELLM_BASE_URL}."
  else
    warn "LiteLLM check returned HTTP ${http_code}. Verify the key/URL in ${CONFIG_FILE}."
  fi
}

finish() {
  cat <<EOF

\033[1;32mRootMedic installed.\033[0m

  Status :  systemctl status rootmedic
  Logs   :  journalctl -u rootmedic -f
  Config :  ${CONFIG_FILE}
  CLI    :  rootmedic

The agent is now running as a systemd service and will autonomously detect
and remediate Linux issues using LiteLLM at ${LITELLM_BASE_URL}.

EOF
}

main() {
  banner
  require_root
  require_linux
  log "Detected: $(detect_distro)"
  install_deps
  prompt_api_key
  clone_or_update_repo
  setup_venv
  write_config
  write_cli_shim
  write_systemd_unit
  verify_install
  finish
}

main "$@"
