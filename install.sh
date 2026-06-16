#!/usr/bin/env bash
# RootMedic installer — autonomous AI medic for Linux systems.
#
# Usage (interactive):
#   curl -fsSL https://raw.githubusercontent.com/Incorpify-LLC/rootmedic/main/install.sh | sudo bash
#
# Usage (non-interactive / CI):
#   sudo ROOTMEDIC_NON_INTERACTIVE=1 \
#        LOKI_URL=http://localhost:3100 \
#        LLM_TYPE=external \
#        LITELLM_BASE_URL=https://api.openai.com \
#        LITELLM_MODEL=gpt-4o-mini \
#        LITELLM_API_KEY=sk-... \
#        bash install.sh
#
# Environment overrides (all optional):
#   ROOTMEDIC_NON_INTERACTIVE  Set to 1 to skip all prompts (uses defaults/env vars)
#   LOKI_URL                   Default: http://localhost:3100
#   LLM_TYPE                   local_ollama | lan_ollama | external  (skips menu)
#   LITELLM_BASE_URL           LLM API base URL
#   LITELLM_MODEL              Model name
#   LITELLM_API_KEY            API key (empty string OK for unauthenticated Ollama)
#   SLACK_WEBHOOK_URL          Optional Slack incoming webhook
#   ROOTMEDIC_REPO             Default: https://github.com/Incorpify-LLC/rootmedic.git
#   ROOTMEDIC_BRANCH           Default: main
#   INSTALL_DIR                Default: /opt/rootmedic
#   START_LOKI_IF_DOWN         Set to 1 to auto-start Docker stack without prompting
set -euo pipefail

# ─── Defaults ────────────────────────────────────────────────────────────────
LOKI_URL="${LOKI_URL:-http://localhost:3100}"
LLM_TYPE="${LLM_TYPE:-}"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-}"
LITELLM_MODEL="${LITELLM_MODEL:-}"
LITELLM_API_KEY="${LITELLM_API_KEY:-}"
SLACK_WEBHOOK_URL="${SLACK_WEBHOOK_URL:-}"
ROOTMEDIC_REPO="${ROOTMEDIC_REPO:-https://github.com/Incorpify-LLC/rootmedic.git}"
ROOTMEDIC_BRANCH="${ROOTMEDIC_BRANCH:-main}"
INSTALL_DIR="${INSTALL_DIR:-/opt/rootmedic}"
ROOTMEDIC_NON_INTERACTIVE="${ROOTMEDIC_NON_INTERACTIVE:-0}"
START_LOKI_IF_DOWN="${START_LOKI_IF_DOWN:-0}"
FLUENT_BIT_VIA_COMPOSE=0   # set to 1 when compose stack starts Fluent Bit for us

CONFIG_DIR="/etc/rootmedic"
CONFIG_FILE="${CONFIG_DIR}/config.yaml"
LOG_DIR="/var/log/rootmedic"
SERVICE_FILE="/etc/systemd/system/rootmedic.service"
DOCS_REPO_BASE="https://github.com/Incorpify-LLC/rootmedic/blob/main/docs"

# ─── Colour helpers ──────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  RED='\033[1;31m'; GREEN='\033[1;32m'; YELLOW='\033[1;33m'
  CYAN='\033[1;36m'; BOLD='\033[1m'; RESET='\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; CYAN=''; BOLD=''; RESET=''
fi

log()  { echo -e "${CYAN}[*]${RESET} $*"; }
ok()   { echo -e "${GREEN}[✓]${RESET} $*"; }
warn() { echo -e "${YELLOW}[!]${RESET} $*"; }
die()  {
  echo -e "${RED}[✗]${RESET} $*" >&2
  exit 1
}

doc_hint() {
  local page="$1"
  echo
  echo -e "  ${BOLD}Local docs:${RESET}  ${INSTALL_DIR}/docs/${page}"
  echo -e "  ${BOLD}Online:${RESET}      ${DOCS_REPO_BASE}/${page}"
  echo
}

# ─── Interactive prompt helpers ───────────────────────────────────────────────
_tty_src() {
  [[ -t 0 ]] && echo /dev/stdin || echo /dev/tty
}

# ask <prompt> <default>  → sets $REPLY
ask() {
  local prompt="$1" default="${2:-}"
  if [[ "${ROOTMEDIC_NON_INTERACTIVE}" == "1" ]]; then
    REPLY="${default}"; return
  fi
  local hint="${default:+ [${default}]}"
  echo -ne "${BOLD}${prompt}${hint}: ${RESET}"
  read -r REPLY < "$(_tty_src)" || true
  [[ -z "${REPLY}" ]] && REPLY="${default}"
}

# ask_secret <prompt>  → sets $REPLY (hidden input)
ask_secret() {
  local prompt="$1"
  if [[ "${ROOTMEDIC_NON_INTERACTIVE}" == "1" ]]; then
    REPLY="${2:-}"; return
  fi
  echo -ne "${BOLD}${prompt} (hidden): ${RESET}"
  read -rs REPLY < "$(_tty_src)" || true
  echo
}

# confirm <prompt> <default y|n>  → returns 0 (yes) or 1 (no)
confirm() {
  local prompt="$1" default="${2:-y}"
  if [[ "${ROOTMEDIC_NON_INTERACTIVE}" == "1" ]]; then
    [[ "${default}" == "y" ]] && return 0 || return 1
  fi
  local hint="[Y/n]"; [[ "${default}" == "n" ]] && hint="[y/N]"
  echo -ne "${BOLD}${prompt} ${hint}: ${RESET}"
  read -r answer < "$(_tty_src)" || true
  answer="${answer:-${default}}"
  [[ "${answer,,}" =~ ^y(es)?$ ]]
}

# menu <title> <opt1_label> <opt1_key> [<opt2_label> <opt2_key> ...]  → sets $MENU_RESULT to key
menu() {
  local title="$1"; shift
  if [[ "${ROOTMEDIC_NON_INTERACTIVE}" == "1" ]]; then
    MENU_RESULT="${1:-}"; shift; return  # first key is default in CI
  fi
  echo
  echo -e "${BOLD}${title}${RESET}"
  local i=1
  local labels=() keys=()
  while [[ $# -ge 2 ]]; do
    labels+=("$1"); keys+=("$2"); shift 2
  done
  for idx in "${!labels[@]}"; do
    echo "  $((idx+1))) ${labels[$idx]}"
  done
  echo
  while true; do
    echo -ne "${BOLD}Choice [1-${#keys[@]}]: ${RESET}"
    read -r choice < "$(_tty_src)" || true
    if [[ "${choice}" =~ ^[0-9]+$ ]] && \
       (( choice >= 1 && choice <= ${#keys[@]} )); then
      MENU_RESULT="${keys[$((choice-1))]}"; return
    fi
    warn "Enter a number between 1 and ${#keys[@]}"
  done
}

# ─── Environment checks ───────────────────────────────────────────────────────
banner() {
  cat <<'EOF'

   ____             _   __  __          _ _
  |  _ \ ___   ___ | |_|  \/  | ___  __| (_) ___
  | |_) / _ \ / _ \| __| |\/| |/ _ \/ _` | |/ __|
  |  _ < (_) | (_) | |_| |  | |  __/ (_| | | (__
  |_| \_\___/ \___/ \__|_|  |_|\___|\__,_|_|\___|

      Autonomous AI medic for Linux systems

EOF
}

require_root() {
  [[ $EUID -eq 0 ]] || die "Run as root:  sudo bash install.sh   (or curl ... | sudo bash)"
}

require_linux() {
  [[ "$(uname -s)" == "Linux" ]] || die "RootMedic only supports Linux."
}

detect_distro() {
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    DISTRO_PRETTY="${PRETTY_NAME:-${NAME:-unknown}}"
    DISTRO_ID="${ID:-unknown}"
    DISTRO_FAMILY="${ID_LIKE:-${ID:-unknown}}"
  else
    DISTRO_PRETTY="unknown"; DISTRO_ID="unknown"; DISTRO_FAMILY="unknown"
  fi
  log "Detected: ${DISTRO_PRETTY}"
}

# ─── OS dependency install ─────────────────────────────────────────────────
install_os_deps() {
  log "Installing OS packages (python3, git, curl, jq, venv, ca-certificates)..."
  local pkgs=(python3 python3-venv python3-pip git curl jq ca-certificates)
  if command -v apt-get >/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${pkgs[@]}"
  elif command -v dnf >/dev/null; then
    dnf install -y -q python3 python3-pip git curl jq ca-certificates
  elif command -v pacman >/dev/null; then
    pacman -Sy --noconfirm --needed python python-pip git curl jq ca-certificates
  elif command -v apk >/dev/null; then
    apk add --no-cache python3 py3-pip git curl jq ca-certificates
  elif command -v zypper >/dev/null; then
    zypper --non-interactive install -y python3 python3-pip git curl jq ca-certificates
  else
    die "No supported package manager found (apt / dnf / pacman / apk / zypper)."
  fi
  ok "OS dependencies installed."
}

# ─── Repo + venv ──────────────────────────────────────────────────────────────
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
  ok "Python environment ready."
}

# ─── Loki ────────────────────────────────────────────────────────────────────
_loki_ready() {
  local url="$1"
  local code
  code=$(curl -fsS -o /dev/null -w "%{http_code}" \
    --connect-timeout 4 "${url}/ready" 2>/dev/null || echo "000")
  [[ "${code}" == "200" ]]
}

_detect_compose() {
  if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
    echo "docker compose"
  elif command -v docker-compose >/dev/null; then
    echo "docker-compose"
  elif command -v podman-compose >/dev/null; then
    echo "podman-compose"
  else
    echo ""
  fi
}

_start_loki_stack() {
  local compose_cmd
  compose_cmd=$(_detect_compose)

  if [[ -z "${compose_cmd}" ]]; then
    warn "Neither Docker Compose nor Podman Compose was found."
    echo "  Install Docker: https://docs.docker.com/engine/install/"
    doc_hint "troubleshooting/loki-not-reachable.md"
    return 1
  fi

  local container_runtime="podman"
  command -v podman >/dev/null || container_runtime="docker"

  # Write a self-contained compose file with fully-qualified image names and no
  # depends_on — avoids Fedora/Podman short-name resolution errors and the
  # podman-compose 1.5.x health-check hang bug on unmonitored containers.
  local compose_file="/tmp/rootmedic-loki-stack.yml"
  local loki_data_dir="${INSTALL_DIR}/Deployment/data/loki"
  local grafana_data_dir="${INSTALL_DIR}/Deployment/data/grafana"
  local grafana_prov_dir="${INSTALL_DIR}/Deployment/grafana-provisioning"
  local loki_cfg="${INSTALL_DIR}/Deployment/loki-config.yaml"
  local fb_cfg="/etc/fluent-bit/fluent-bit.conf"
  local fb_parsers="/etc/fluent-bit/parsers.conf"
  mkdir -p "${loki_data_dir}" "${grafana_data_dir}"

  # Write Fluent Bit config now (before compose) so the container can mount it.
  # Inside the compose network Loki is reachable as "loki" (the service name),
  # not "localhost" — pass the override so the mounted config is correct.
  _write_fluent_bit_config "loki"

  # Write Grafana provisioning files (datasource + dashboard).
  _write_grafana_provisioning "${grafana_prov_dir}"

  cat > "${compose_file}" <<COMPOSE_EOF
version: '3.8'
services:
  loki:
    image: docker.io/grafana/loki:2.9.0
    container_name: loki
    user: root
    ports:
      - "3100:3100"
    command: -config.file=/etc/loki/loki-config.yaml
    volumes:
      - ${loki_data_dir}:/loki:Z
      - ${loki_cfg}:/etc/loki/loki-config.yaml:ro,z
    restart: unless-stopped

  fluent-bit:
    image: docker.io/fluent/fluent-bit:latest
    container_name: fluent-bit
    user: root
    volumes:
      - /var/log/journal:/var/log/journal:ro,z
      - /etc/machine-id:/etc/machine-id:ro,z
      - ${fb_cfg}:/etc/fluent-bit/fluent-bit.conf:ro,z
      - ${fb_parsers}:/etc/fluent-bit/parsers.conf:ro,z
    command: /fluent-bit/bin/fluent-bit -c /etc/fluent-bit/fluent-bit.conf
    restart: unless-stopped

  grafana:
    image: docker.io/grafana/grafana:10.2.3
    container_name: grafana
    user: root
    ports:
      - "3000:3000"
    volumes:
      - ${grafana_data_dir}:/var/lib/grafana:Z
      - ${grafana_prov_dir}:/etc/grafana/provisioning:ro,Z
    environment:
      - GF_SECURITY_ADMIN_USER=admin
      - GF_SECURITY_ADMIN_PASSWORD=admin
    restart: unless-stopped
COMPOSE_EOF

  # Pull images explicitly before compose so the runtime never needs to
  # resolve short names interactively (Fedora enforces this strictly).
  log "Pulling container images (this may take a few minutes on first run)..."
  local images=("docker.io/grafana/loki:2.9.0" "docker.io/fluent/fluent-bit:latest" "docker.io/grafana/grafana:10.2.3")
  for img in "${images[@]}"; do
    log "  Pulling ${img} ..."
    if ! ${container_runtime} pull "${img}"; then
      warn "Failed to pull ${img} — check network connectivity."
      return 1
    fi
  done
  ok "Images ready."

  log "Starting Loki stack with: ${compose_cmd} ..."
  ${compose_cmd} -f "${compose_file}" up -d

  log "Waiting for Loki to become ready (up to 90 s)..."
  local i=0
  while (( i < 45 )); do
    sleep 2; (( i += 1 )) || true
    if _loki_ready "${LOKI_URL}"; then
      ok "Loki is up at ${LOKI_URL}"
      FLUENT_BIT_VIA_COMPOSE=1
      return 0
    fi
    printf "."
  done
  echo
  # One last check with a longer timeout
  if _loki_ready "${LOKI_URL}"; then
    ok "Loki is up at ${LOKI_URL}"
    FLUENT_BIT_VIA_COMPOSE=1
    return 0
  fi
  warn "Loki container logs:"
  podman logs loki 2>/dev/null | tail -10 || true
  return 1
}

configure_loki() {
  echo
  echo -e "${BOLD}── Loki Configuration ──────────────────────────────────────────${RESET}"

  ask "Loki URL" "${LOKI_URL}"
  LOKI_URL="${REPLY}"

  log "Testing Loki connectivity at ${LOKI_URL} ..."
  if _loki_ready "${LOKI_URL}"; then
    ok "Loki is reachable."
    return
  fi

  warn "Loki is not reachable at ${LOKI_URL}"

  local start_it="${START_LOKI_IF_DOWN}"
  if [[ "${start_it}" != "1" ]]; then
    if confirm "Start the bundled Loki + Fluent Bit + Grafana stack now?" "y"; then
      start_it="1"
    fi
  fi

  if [[ "${start_it}" == "1" ]]; then
    if _start_loki_stack; then
      ok "Loki stack started successfully."
      return
    else
      warn "Could not start the Loki stack automatically."
      doc_hint "troubleshooting/loki-not-reachable.md"
      die "Please start Loki manually and re-run the installer."
    fi
  fi

  warn "Skipping Loki startup. The agent will not receive logs until Loki is running."
  doc_hint "troubleshooting/loki-not-reachable.md"
  if ! confirm "Continue without a running Loki? (not recommended)" "n"; then
    die "Aborted. Start Loki first, then re-run the installer."
  fi
}

# ─── Fluent Bit ───────────────────────────────────────────────────────────────
_fluent_bit_container_running() {
  local rt
  for rt in podman docker; do
    command -v "${rt}" >/dev/null 2>&1 || continue
    ${rt} ps --format '{{.Names}}' 2>/dev/null | grep -q "^fluent-bit$" && return 0
  done
  return 1
}

install_fluent_bit() {
  # Skip when the compose stack launched Fluent Bit this session.
  if [[ "${FLUENT_BIT_VIA_COMPOSE}" == "1" ]]; then
    ok "Fluent Bit is running in the container stack — skipping native install."
    return
  fi

  # Skip when a fluent-bit container is already running (e.g. previous install).
  if _fluent_bit_container_running; then
    ok "Fluent Bit container already running — skipping native install."
    return
  fi

  log "Installing Fluent Bit log collector..."

  if command -v fluent-bit >/dev/null 2>&1; then
    ok "Fluent Bit already installed: $(fluent-bit --version 2>/dev/null | head -1)"
    _write_fluent_bit_config
    _enable_fluent_bit
    return
  fi

  local installed=0
  if command -v apt-get >/dev/null; then
    curl -fsSL https://packages.fluentbit.io/fluentbit.key \
      | gpg --dearmor -o /usr/share/keyrings/fluentbit-keyring.gpg 2>/dev/null
    local codename
    codename=$(. /etc/os-release && echo "${VERSION_CODENAME:-${UBUNTU_CODENAME:-}}")
    [[ -z "${codename}" ]] && codename="jammy"
    cat > /etc/apt/sources.list.d/fluentbit.list <<EOF
deb [signed-by=/usr/share/keyrings/fluentbit-keyring.gpg] https://packages.fluentbit.io/debian/${codename} stable main
EOF
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fluent-bit && installed=1

  elif command -v dnf >/dev/null; then
    local distro_id major_ver
    distro_id=$(. /etc/os-release && echo "${ID:-unknown}")
    major_ver=$(. /etc/os-release && echo "${VERSION_ID%%.*}")

    # Try the distro's own repo first (Fedora 38+ ships fluent-bit natively).
    if dnf install -y -q fluent-bit 2>/dev/null; then
      installed=1
    else
      # Fallback: upstream Fluent Bit repo for RHEL/CentOS (and Fedora as best-effort).
      local repo_ver="${major_ver}"
      [[ "${distro_id}" == "fedora" ]] && repo_ver=9
      log "Distro repo has no fluent-bit — trying upstream RHEL ${repo_ver} repo..."
      cat > /etc/yum.repos.d/fluentbit.repo <<EOF
[fluent-bit]
name=Fluent Bit
baseurl=https://packages.fluentbit.io/centos/${repo_ver}/\$basearch/
gpgcheck=1
gpgkey=https://packages.fluentbit.io/fluentbit.key
enabled=1
EOF
      if dnf install -y -q fluent-bit 2>/dev/null; then
        installed=1
      else
        warn "Fluent Bit install failed on ${distro_id} ${major_ver}."
        warn "Log collection will rely on the container stack if running."
        doc_hint "troubleshooting/fluent-bit.md"
        if ! confirm "Continue without native Fluent Bit?" "y"; then
          die "Aborted. See docs for manual Fluent Bit install."
        fi
        return
      fi
    fi

  elif command -v apk >/dev/null; then
    apk add --no-cache fluent-bit && installed=1

  else
    warn "Automatic Fluent Bit install is not supported on this distro."
    doc_hint "troubleshooting/fluent-bit.md"
    if ! confirm "Continue without Fluent Bit?" "n"; then
      die "Aborted. Install Fluent Bit first."
    fi
    return
  fi

  [[ "${installed}" == "1" ]] && ok "Fluent Bit installed."
  _write_fluent_bit_config
  _enable_fluent_bit
}

_write_grafana_provisioning() {
  local prov_dir="$1"
  log "Writing Grafana provisioning (datasource + dashboard)..."

  mkdir -p \
    "${prov_dir}/datasources" \
    "${prov_dir}/dashboards" \
    "${prov_dir}/dashboards-json"

  # Datasource: Loki reachable via compose service name
  cat > "${prov_dir}/datasources/loki.yaml" <<'DS_EOF'
apiVersion: 1
datasources:
  - name: Loki
    uid: loki
    type: loki
    access: proxy
    url: http://loki:3100
    isDefault: true
    editable: false
    jsonData:
      maxLines: 1000
DS_EOF

  # Dashboard provider: tells Grafana where to find JSON files
  cat > "${prov_dir}/dashboards/rootmedic.yaml" <<'DASH_EOF'
apiVersion: 1
providers:
  - name: RootMedic
    type: file
    disableDeletion: true
    updateIntervalSeconds: 30
    allowUiUpdates: false
    options:
      path: /etc/grafana/provisioning/dashboards-json
DASH_EOF

  # Copy dashboard JSON from cloned repo if available, else write inline minimal version
  local src_json="${INSTALL_DIR}/Deployment/grafana-provisioning/dashboards-json/rootmedic.json"
  local dst_json="${prov_dir}/dashboards-json/rootmedic.json"
  if [[ -f "${src_json}" ]]; then
    cp "${src_json}" "${dst_json}"
  else
    # Minimal inline fallback: one log stream panel
    cat > "${dst_json}" <<'JSON_EOF'
{
  "uid": "rootmedic-main",
  "title": "RootMedic — System Health",
  "tags": ["rootmedic"],
  "timezone": "browser",
  "schemaVersion": 38,
  "refresh": "30s",
  "time": { "from": "now-1h", "to": "now" },
  "panels": [{
    "id": 1, "type": "logs",
    "title": "Live Error Stream",
    "gridPos": { "h": 20, "w": 24, "x": 0, "y": 0 },
    "datasource": { "type": "loki", "uid": "loki" },
    "options": { "dedupStrategy": "none", "enableLogDetails": true,
                 "showTime": true, "sortOrder": "Descending", "wrapLogMessage": true },
    "targets": [{
      "refId": "A",
      "datasource": { "type": "loki", "uid": "loki" },
      "expr": "{job=~\"systemd-journal|fluent-bit\"} |~ \"(?i)error|warn|fail|kill|denied|critical\"",
      "legendFormat": ""
    }]
  }]
}
JSON_EOF
  fi

  ok "Grafana provisioning written → ${prov_dir}"
}

_write_fluent_bit_config() {
  # Optional first arg overrides the Loki host (used when Fluent Bit runs inside
  # a compose network where Loki is reachable by service name, not "localhost").
  local host_override="${1:-}"
  log "Writing Fluent Bit configuration..."
  local loki_host loki_port
  loki_host=$(echo "${LOKI_URL}" | sed -E 's|https?://([^:/]+).*|\1|')
  loki_port=$(echo "${LOKI_URL}" | sed -E 's|https?://[^:]+:([0-9]+).*|\1|')
  # If sed didn't strip anything (no port in URL), fall back to 3100
  [[ "${loki_port}" == "${LOKI_URL}" ]] && loki_port="3100"
  [[ -n "${host_override}" ]] && loki_host="${host_override}"

  mkdir -p /etc/fluent-bit
  cat > /etc/fluent-bit/fluent-bit.conf <<EOF
[SERVICE]
    Flush           5
    Log_Level       info
    Daemon          Off
    Parsers_File    /etc/fluent-bit/parsers.conf
    HTTP_Server     Off

[INPUT]
    Name              systemd
    Tag               host.*
    Read_From_Tail    On
    Strip_Underscores On

[FILTER]
    Name    grep
    Match   *
    Regex   PRIORITY ^[34]\$

[OUTPUT]
    Name            loki
    Match           *
    Host            ${loki_host}
    Port            ${loki_port}
    Labels          job=fluent-bit,host=\${HOSTNAME}
    Label_Keys      \$SYSLOG_IDENTIFIER,\$_SYSTEMD_UNIT
    Line_Format     json
    Auto_Kubernetes_Labels Off
EOF

  if [[ -f "${INSTALL_DIR}/Deployment/fluent-bit-parsers.conf" ]]; then
    cp "${INSTALL_DIR}/Deployment/fluent-bit-parsers.conf" /etc/fluent-bit/parsers.conf
  else
    cat > /etc/fluent-bit/parsers.conf <<'PARSERS'
[PARSER]
    Name   json
    Format json
PARSERS
  fi
  ok "Fluent Bit config written → ${loki_host}:${loki_port}"
}

_enable_fluent_bit() {
  if systemctl is-active --quiet fluent-bit 2>/dev/null; then
    systemctl restart fluent-bit
    ok "Fluent Bit restarted."
  else
    systemctl daemon-reload
    systemctl enable --now fluent-bit 2>/dev/null \
      && ok "Fluent Bit enabled and started." \
      || warn "Could not enable fluent-bit service. Start manually: systemctl start fluent-bit"
  fi
}

# ─── LLM ─────────────────────────────────────────────────────────────────────
# _precheck_ollama <base_url> <model> — reachability + model presence (warn-only).
# Gives LAN users a clear message instead of a cryptic HTTP 000 from _test_llm.
_precheck_ollama() {
  local base_url="$1" model="$2"
  local tags="/tmp/rm_ollama_tags.$$.json"
  local code
  code=$(curl -sS -o "${tags}" -w '%{http_code}' \
    --connect-timeout 5 --max-time 10 "${base_url}/api/tags" 2>/dev/null || true)
  [[ -z "${code}" ]] && code="000"

  if [[ "${code}" != "200" ]]; then
    warn "Cannot reach the Ollama API at ${base_url} (HTTP ${code})."
    if [[ "${code}" == "000" ]]; then
      echo "  Could not connect at all. For a remote (LAN) Ollama host, check:" >&2
      echo "    - Ollama binds to all interfaces: set OLLAMA_HOST=0.0.0.0:11434 and restart it" >&2
      echo "      (the default 127.0.0.1 binding refuses LAN clients)." >&2
      echo "    - The firewall allows the port, e.g.:" >&2
      echo "        firewall-cmd --add-port=11434/tcp --permanent && firewall-cmd --reload" >&2
      echo "    - The URL and port are correct." >&2
    fi
    rm -f "${tags}"
    doc_hint "providers/ollama-local.md"
    confirm "Continue without a reachable Ollama?" "n" || die "Fix Ollama connectivity and re-run."
    return 0
  fi

  if ! python3 -c "
import json,sys
d=json.load(open('${tags}'))
names=[m.get('name','') for m in d.get('models',[])]
bare={n.split(':')[0] for n in names}
m='${model}'
sys.exit(0 if (m in names or m in bare or m.split(':')[0] in bare) else 1)
" 2>/dev/null; then
    warn "Model '${model}' is not present on ${base_url}."
    local avail
    avail=$(python3 -c "import json;d=json.load(open('${tags}'));print(', '.join(x.get('name','?') for x in d.get('models',[])) or '(none)')" 2>/dev/null || echo "(unknown)")
    echo "  Models available there: ${avail}" >&2
    echo "  Pull it on the Ollama host:  ollama pull ${model}" >&2
    rm -f "${tags}"
    confirm "Continue with a missing model?" "n" || die "Pull the model and re-run."
    return 0
  fi

  rm -f "${tags}"
  ok "Ollama reachable at ${base_url}; model '${model}' is present."
}

_test_llm() {
  local base_url="$1" model="$2" api_key="${3:-dummy}"
  local tmp; tmp=$(mktemp)

  local payload
  payload=$(printf \
    '{"model":"%s","messages":[{"role":"user","content":"Reply with the single word: hello"}],"max_tokens":64,"stream":false}' \
    "${model}")

  # No -f: we want the real status and body even on 4xx/5xx, plus a clean single
  # status code (curl prints "000" via -w when it cannot connect at all).
  local code
  code=$(curl -sS -o "${tmp}" -w '%{http_code}' \
    --connect-timeout 8 --max-time 30 \
    -X POST "${base_url}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${api_key}" \
    -d "${payload}" 2>/dev/null || true)
  [[ -z "${code}" ]] && code="000"

  if [[ "${code}" != "200" ]]; then
    warn "LLM endpoint returned HTTP ${code}."
    case "${code}" in
      000)     echo "  Could not connect - check the URL, port, and firewall on the LLM host." >&2 ;;
      401|403) echo "  Auth rejected - check the API key." >&2 ;;
      404)     echo "  Not found - model '${model}' or the /v1/chat/completions path may be wrong." >&2 ;;
    esac
    [[ -s "${tmp}" ]] && { echo "  Response body:" >&2; head -c 400 "${tmp}" >&2; echo >&2; }
    rm -f "${tmp}"
    return 1
  fi

  # HTTP 200 - parse the OpenAI-compatible chat response.
  local parsed
  parsed=$(python3 -c "
import json,sys
try:
    d=json.load(open('${tmp}'))
except Exception as e:
    print('PARSE_ERROR:'+str(e)); raise SystemExit
try:
    msg=d['choices'][0]['message']
    print('OK:'+((msg.get('content') or msg.get('reasoning_content') or '').strip()[:120]))
except Exception:
    print('SHAPE_ERROR')
" 2>/dev/null || true)
  rm -f "${tmp}"

  case "${parsed}" in
    OK:?*)
      ok "LLM responded: \"${parsed#OK:}\""
      return 0 ;;
    OK:)
      # Valid OpenAI shape but empty text (e.g. a reasoning-only model that spent
      # its whole token budget thinking). The endpoint works - which is the test.
      ok "LLM endpoint healthy (HTTP 200, valid OpenAI-chat response)."
      return 0 ;;
    PARSE_ERROR:*)
      warn "HTTP 200 but the body was not valid JSON: ${parsed#PARSE_ERROR:}"
      return 1 ;;
    *)
      warn "HTTP 200 but the response was not OpenAI-chat shaped (no choices[0].message)."
      return 1 ;;
  esac
}

configure_llm() {
  echo
  echo -e "${BOLD}── LLM Configuration ───────────────────────────────────────────${RESET}"

  # Let env var skip the menu
  if [[ -z "${LLM_TYPE}" ]]; then
    menu "Where is your LLM running?" \
      "Local Ollama  (same machine, port 11434)"  "local_ollama" \
      "LAN  Ollama   (another machine on your network)"  "lan_ollama" \
      "External API  (OpenAI, Anthropic, OpenRouter, LiteLLM proxy, etc.)" "external" \
      "Hosted RootMedic LiteLLM  (https://litellm.saneax.in)"  "hosted"
    LLM_TYPE="${MENU_RESULT}"
  fi

  case "${LLM_TYPE}" in
    local_ollama)
      LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:11434}"
      ask "Ollama URL" "${LITELLM_BASE_URL}"
      LITELLM_BASE_URL="${REPLY}"
      ask "Model name" "${LITELLM_MODEL:-llama3.2}"
      LITELLM_MODEL="${REPLY}"
      LITELLM_API_KEY="${LITELLM_API_KEY:-ollama}"

      # Check Ollama is running
      local ollama_code
      ollama_code=$(curl -fsS -o /dev/null -w "%{http_code}" \
        --connect-timeout 4 "${LITELLM_BASE_URL}/api/tags" 2>/dev/null || echo "000")
      if [[ "${ollama_code}" != "200" ]]; then
        warn "Ollama is not responding at ${LITELLM_BASE_URL}"
        echo "  Start Ollama:  ollama serve"
        echo "  Pull a model:  ollama pull ${LITELLM_MODEL}"
        doc_hint "providers/ollama-local.md"
        die "Start Ollama and re-run the installer."
      fi

      # Check model exists
      if ! curl -fsS "${LITELLM_BASE_URL}/api/tags" 2>/dev/null \
           | python3 -c "import json,sys; d=json.load(sys.stdin); \
             names=[m['name'].split(':')[0] for m in d.get('models',d.get('data',[]))]; \
             sys.exit(0 if any('${LITELLM_MODEL}'==n or '${LITELLM_MODEL}'.startswith(n) for n in names) else 1)" \
           2>/dev/null; then
        warn "Model '${LITELLM_MODEL}' not found in Ollama."
        log "Attempting to pull '${LITELLM_MODEL}' ..."
        if ollama pull "${LITELLM_MODEL}" 2>/dev/null; then
          ok "Model pulled."
        else
          warn "Could not pull model automatically."
          echo "  Run:  ollama pull ${LITELLM_MODEL}"
          doc_hint "providers/ollama-local.md"
          if ! confirm "Continue anyway?" "n"; then
            die "Aborted. Pull the model first."
          fi
        fi
      fi
      ;;

    lan_ollama)
      ask "Ollama URL (e.g. http://192.168.1.50:11434)" "${LITELLM_BASE_URL:-}"
      LITELLM_BASE_URL="${REPLY}"
      [[ -z "${LITELLM_BASE_URL}" ]] && die "Ollama URL is required."
      ask "Model name" "${LITELLM_MODEL:-llama3.2}"
      LITELLM_MODEL="${REPLY}"
      LITELLM_API_KEY="${LITELLM_API_KEY:-ollama}"
      _precheck_ollama "${LITELLM_BASE_URL}" "${LITELLM_MODEL}"
      ;;

    external)
      ask "API base URL (e.g. https://api.openai.com or your LiteLLM proxy)" \
          "${LITELLM_BASE_URL:-}"
      LITELLM_BASE_URL="${REPLY}"
      [[ -z "${LITELLM_BASE_URL}" ]] && die "API base URL is required."
      ask "Model name (e.g. gpt-4o-mini, claude-3-haiku-20240307)" \
          "${LITELLM_MODEL:-gpt-4o-mini}"
      LITELLM_MODEL="${REPLY}"
      ask_secret "API key" "${LITELLM_API_KEY:-}"
      LITELLM_API_KEY="${REPLY}"
      [[ -z "${LITELLM_API_KEY}" ]] && die "API key is required for external providers."
      ;;

    hosted)
      LITELLM_BASE_URL="https://litellm.saneax.in"
      ask "Model name" "${LITELLM_MODEL:-smart}"
      LITELLM_MODEL="${REPLY}"
      ask_secret "LiteLLM API key" "${LITELLM_API_KEY:-}"
      LITELLM_API_KEY="${REPLY}"
      [[ -z "${LITELLM_API_KEY}" ]] && die "API key is required."
      ;;

    *)
      die "Unknown LLM_TYPE '${LLM_TYPE}'. Valid: local_ollama | lan_ollama | external | hosted"
      ;;
  esac

  log "Testing LLM at ${LITELLM_BASE_URL} (model: ${LITELLM_MODEL}) ..."
  local max_tries=3 attempt=0 success=0
  while (( attempt < max_tries )); do
    (( attempt += 1 )) || true
    if _test_llm "${LITELLM_BASE_URL}" "${LITELLM_MODEL}" "${LITELLM_API_KEY}"; then
      success=1; break
    fi
    (( attempt < max_tries )) && warn "Attempt ${attempt}/${max_tries} failed. Retrying..." && sleep 3
  done

  if [[ "${success}" != "1" ]]; then
    warn "LLM did not respond correctly after ${max_tries} attempts."
    case "${LLM_TYPE}" in
      local_ollama|lan_ollama)
        doc_hint "providers/ollama-local.md" ;;
      *)
        doc_hint "providers/external-api.md" ;;
    esac
    doc_hint "troubleshooting/llm-not-responding.md"
    if ! confirm "Continue with this LLM config anyway?" "n"; then
      die "Aborted. Fix LLM connectivity and re-run."
    fi
    warn "Continuing. The agent will report LLM errors at runtime."
  fi
}

configure_alerts() {
  echo
  echo -e "${BOLD}── Alerting (Optional) ─────────────────────────────────────────${RESET}"
  if [[ -z "${SLACK_WEBHOOK_URL}" ]]; then
    if confirm "Configure a Slack webhook for alerts?" "n"; then
      ask "Slack incoming webhook URL" ""
      SLACK_WEBHOOK_URL="${REPLY}"
    fi
  else
    log "Slack webhook configured via environment variable."
  fi
}

# ─── Write artefacts ──────────────────────────────────────────────────────────
write_config() {
  log "Writing config to ${CONFIG_FILE}..."
  mkdir -p "${CONFIG_DIR}" "${LOG_DIR}"
  umask 077
  cat > "${CONFIG_FILE}" <<EOF
# RootMedic configuration — written by install.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)
# Edit this file to change LLM or Loki settings, then restart rootmedic.service

litellm_base_url: "${LITELLM_BASE_URL}"
litellm_model:    "${LITELLM_MODEL}"
litellm_api_key:  "${LITELLM_API_KEY}"

loki_url: "${LOKI_URL}/loki/api/v1/query_range"

slack_webhook_url:          "${SLACK_WEBHOOK_URL}"
dedup_window_minutes:       15
escalation_after_minutes:   30
grafana_base_url:           "http://localhost:3000"

# Webhook receiver (cloud/Datadog complement mode — not started by default)
webhook_receiver_port: 9876
EOF
  chmod 600 "${CONFIG_FILE}"
  ok "Config written (${CONFIG_FILE}, mode 600)."
}

write_cli_shim() {
  log "Installing /usr/local/bin/rootmedic CLI..."
  cat > /usr/local/bin/rootmedic <<EOF
#!/usr/bin/env bash
cd "${INSTALL_DIR}"
exec "${INSTALL_DIR}/.venv/bin/python" "${INSTALL_DIR}/fetch_normalize_logs.py" "\$@"
EOF
  chmod +x /usr/local/bin/rootmedic
  ok "CLI shim installed: run 'rootmedic' to invoke the agent."
}

write_systemd_unit() {
  log "Installing ${SERVICE_FILE}..."
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
Environment=ROOTMEDIC_CONFIG=${CONFIG_FILE}

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now rootmedic.service
  ok "rootmedic.service enabled and started."
}

# ─── Final verification ───────────────────────────────────────────────────────
verify_install() {
  echo
  echo -e "${BOLD}── Quick Health Check ──────────────────────────────────────────${RESET}"

  local pass=0 warn_=0

  _chk() {
    local label="$1" ok_msg="$2" fail_msg="$3"
    shift 3
    printf "  ${BOLD}%-28s${RESET}" "${label}"
    if "$@" 2>/dev/null; then
      echo -e "${GREEN}${ok_msg}${RESET}"; (( pass += 1 )) || true
    else
      echo -e "${YELLOW}${fail_msg}${RESET}"; (( warn_ += 1 )) || true
    fi
  }

  _chk "rootmedic.service" "active" "inactive" \
    systemctl is-active --quiet rootmedic

  _chk "Loki (${LOKI_URL})" "reachable" "unreachable" \
    _loki_ready "${LOKI_URL}"

  _chk "LLM (${LITELLM_BASE_URL})" "responding" "not responding" \
    _test_llm "${LITELLM_BASE_URL}" "${LITELLM_MODEL}" "${LITELLM_API_KEY}"

  echo
  echo -e "  ${GREEN}${pass} healthy${RESET}  ${YELLOW}${warn_} warnings${RESET}"
}

finish() {
  local host_ip
  host_ip=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")

  echo
  echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════════════════╗${RESET}"
  echo -e "${GREEN}${BOLD}║          RootMedic installed and running ✓                   ║${RESET}"
  echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════════════════╝${RESET}"
  echo
  echo -e "  ${BOLD}Grafana dashboard${RESET}  →  http://${host_ip}:3000  ${YELLOW}(admin / admin)${RESET}"
  echo -e "  ${BOLD}Loki API${RESET}           →  http://${host_ip}:3100"
  echo -e "  ${BOLD}Service logs${RESET}       →  journalctl -u rootmedic -f"
  echo -e "  ${BOLD}Trigger agent${RESET}      →  rootmedic"
  echo -e "  ${BOLD}Config${RESET}             →  ${CONFIG_FILE}"
  echo
  echo -e "  ${BOLD}Run the live healing demo:${RESET}"
  # verify_install.sh is in the cloned repo; also copied by dev-deploy.sh
  local verify="${INSTALL_DIR}/verify_install.sh"
  [[ ! -f "${verify}" ]] && verify="$(cd "$(dirname "${BASH_SOURCE[0]:-/dev/null}")" 2>/dev/null && pwd)/verify_install.sh"
  echo -e "  ${CYAN}  sudo bash ${verify}${RESET}"
  echo
}

# ─── Main ─────────────────────────────────────────────────────────────────────
main() {
  banner
  require_root
  require_linux
  detect_distro

  # Phase 1: OS setup (no interaction yet — machine-level)
  install_os_deps
  clone_or_update_repo
  setup_venv

  # Phase 2: Interactive configuration
  configure_loki
  install_fluent_bit
  configure_llm
  configure_alerts

  # Phase 3: Write and start
  write_config
  write_cli_shim
  write_systemd_unit

  # Phase 4: Verify
  verify_install
  finish
}

main "$@"
