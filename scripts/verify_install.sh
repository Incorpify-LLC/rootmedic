#!/usr/bin/env bash
# verify_install.sh — RootMedic post-install verification + live healing demo.
#
# Two acts:
#   1. Health check — confirms every component (service, Loki, Grafana,
#      Fluent Bit, LLM) is up.
#   2. Live healing demo — injects three synthetic faults into Loki, runs the
#      agent, and shows the remediation plan it produced. Recommend-only:
#      nothing is executed on your system.
#
# Usage:
#   sudo bash verify_install.sh                  # interactive, pauses between steps
#   sudo NONINTERACTIVE=1 bash verify_install.sh # no pauses (CI / unattended)
#
# Env overrides:
#   INSTALL_DIR   RootMedic install root      (default: /opt/rootmedic)
#   LOKI_URL      Loki base URL               (default: from config, else http://localhost:3100)
#   GRAFANA_URL   Grafana base URL            (default: http://localhost:3000)
#
# NOTE: no `set -e` — this is a diagnostic; we want to report failures, not abort on them.
set -uo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/rootmedic}"
CONFIG_FILE="/etc/rootmedic/config.yaml"
GRAFANA_URL="${GRAFANA_URL:-http://localhost:3000}"
NONINTERACTIVE="${NONINTERACTIVE:-0}"

# ─── Colours ─────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  RED='\033[1;31m'; GREEN='\033[1;32m'; YELLOW='\033[1;33m'
  CYAN='\033[1;36m'; BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; CYAN=''; BOLD=''; DIM=''; RESET=''
fi
ok()   { echo -e "${GREEN}[✓]${RESET} $*"; }
warn() { echo -e "${YELLOW}[!]${RESET} $*"; }
info() { echo -e "${CYAN}[*]${RESET} $*"; }
hr()   { echo -e "${BOLD}── $* ${RESET}"; }

# ─── Config-derived values ────────────────────────────────────────────────────
_cfg() { [[ -r "${CONFIG_FILE}" ]] && grep -E "^\s*$1:" "${CONFIG_FILE}" \
           | sed -E 's/^[^:]+:\s*"?([^"]*)"?\s*$/\1/' | head -1; }

LOKI_BASE="${LOKI_URL:-}"
[[ -z "${LOKI_BASE}" ]] && LOKI_BASE="$(_cfg loki_url)"
# Strip any /loki/... path → scheme://host:port
LOKI_BASE="$(echo "${LOKI_BASE:-http://localhost:3100}" | sed -E 's|(https?://[^/]+).*|\1|')"

LLM_BASE="$(_cfg litellm_base_url)"
LLM_MODEL="$(_cfg litellm_model)"
LLM_KEY="$(_cfg litellm_api_key)"

PASS=0; FAILN=0

# ─── Helpers ──────────────────────────────────────────────────────────────────
_pause() {
  if [[ "${NONINTERACTIVE}" == "1" ]]; then sleep 1; return; fi
  echo -ne "${DIM}    ↵  press any key to continue…${RESET}"
  read -rn1 -s < /dev/tty || true
  echo -e "\r\033[K"
}

_status() {  # _status "Label" <command...>
  local label="$1"; shift
  printf "  %-32s" "${label}"
  if "$@" >/dev/null 2>&1; then
    echo -e "${GREEN}● up${RESET}";   PASS=$(( PASS + 1 ))
  else
    echo -e "${RED}● down${RESET}";   FAILN=$(( FAILN + 1 ))
  fi
}

_loki_ready()  { curl -fsS --connect-timeout 4 "${LOKI_BASE}/ready" ; }
_http_alive()  { local c; c=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 4 "$1" 2>/dev/null || echo 000); [[ "${c}" != "000" ]]; }
_fluent_up()   {
  local rt
  for rt in podman docker; do
    command -v "${rt}" >/dev/null 2>&1 || continue
    "${rt}" ps --format '{{.Names}}' 2>/dev/null | grep -q '^fluent-bit$' && return 0
  done
  systemctl is-active --quiet fluent-bit 2>/dev/null
}
_llm_up() {
  [[ -z "${LLM_BASE}" ]] && return 1
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 6 --max-time 25 \
    -X POST "${LLM_BASE}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${LLM_KEY:-ollama}" \
    -d "{\"model\":\"${LLM_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":8,\"stream\":false}" \
    2>/dev/null || echo 000)
  [[ "${code}" == "200" ]]
}

_push_fault() {  # _push_fault "title" "systemd_unit" "log line"
  local title="$1" unit="$2" line="$3" ts
  ts=$(date +%s%N)
  # job=fluent-bit matches the agent's Loki query; systemd_unit is the label
  # ingest.parse_and_normalize reads into event["unit"].
  local payload
  payload=$(python3 - "$line" "$ts" "$unit" <<'PY'
import json, sys
line, ts, unit = sys.argv[1], sys.argv[2], sys.argv[3]
print(json.dumps({"streams":[{
    "stream":{"job":"fluent-bit","host":"rootmedic-demo","systemd_unit":unit},
    "values":[[ts, line]]
}]}))
PY
)
  if curl -fsS --connect-timeout 5 -X POST "${LOKI_BASE}/loki/api/v1/push" \
       -H "Content-Type: application/json" -d "${payload}" >/dev/null 2>&1; then
    ok "injected: ${title}"
  else
    warn "failed to inject: ${title}"
  fi
}

# ═══════════════════════════════════════════════════════════════════════════════
clear 2>/dev/null || true
echo
echo -e "${BOLD}${CYAN}   RootMedic — Install Verification & Live Healing Demo${RESET}"
echo -e "   ${DIM}Loki: ${LOKI_BASE}    Install: ${INSTALL_DIR}${RESET}"
echo

# ─── ACT 1: Health check ──────────────────────────────────────────────────────
hr "1 / 2  ·  Component Health"
echo
_status "rootmedic.service"            systemctl is-active --quiet rootmedic
_status "Loki  (${LOKI_BASE})"         _loki_ready
_status "Grafana (${GRAFANA_URL})"     _http_alive "${GRAFANA_URL}"
_status "Fluent Bit collector"         _fluent_up
_status "LLM  (${LLM_BASE:-unset})"    _llm_up
echo
if (( FAILN == 0 )); then
  ok "All ${PASS} components healthy."
else
  warn "${PASS} up, ${FAILN} down. The demo may be limited — see 'journalctl -u rootmedic'."
fi
echo
_pause

# ─── ACT 2: Live healing demo ─────────────────────────────────────────────────
hr "2 / 2  ·  Live Healing Demo"
echo
echo -e "  RootMedic will now watch three faults appear and diagnose them."
echo -e "  ${BOLD}It only recommends${RESET} — no command is run against this host."
echo
_pause

info "Injecting synthetic faults into Loki…"
_push_fault "OOM kill (mysqld)" "mysqld.service" \
  "kernel: Out of memory: Killed process 4821 (mysqld) total-vm:9912340kB, anon-rss:8123400kB"
_push_fault "EXT4 filesystem error (sda1)" "systemd-journald.service" \
  "kernel: EXT4-fs error (device sda1): ext4_lookup:1602: inode #131073: comm nginx: deleted inode referenced — remounting read-only, write failed"
_push_fault "nginx service crash" "nginx.service" \
  "systemd: nginx.service: Main process exited, code=killed, status=11/SEGV; unit entered failed state"
echo
info "Waiting for Loki to index the events…"
sleep 4
_pause

# All three faults are matched by the rule-based planner, so the demo does not
# depend on the LLM. Cap the LLM timeout anyway so any unrelated journal noise
# that misses the rules can't stall this run on a slow LLM host.
export ROOTMEDIC_LLM_TIMEOUT="${ROOTMEDIC_LLM_TIMEOUT:-10}"
# Start from a clean slate so we display *this* run's plan.
rm -f "${INSTALL_DIR}/remediation.yaml" 2>/dev/null || true

info "Running the RootMedic agent (fetch → redact → diagnose → recommend)…"
echo -e "${DIM}"
if [[ -x "${INSTALL_DIR}/.venv/bin/python" ]]; then
  ( cd "${INSTALL_DIR}" && .venv/bin/python fetch_normalize_logs.py ) || true
elif command -v rootmedic >/dev/null 2>&1; then
  ( cd "${INSTALL_DIR}" 2>/dev/null && rootmedic ) || true
else
  ( cd "${INSTALL_DIR}" && python3 fetch_normalize_logs.py ) || true
fi
echo -e "${RESET}"

PLAN="${INSTALL_DIR}/remediation.yaml"
echo
if [[ -f "${PLAN}" ]]; then
  ok "Remediation plan generated → ${PLAN}"
  echo
  echo -e "${CYAN}${BOLD}  ── remediation.yaml ─────────────────────────────────${RESET}"
  sed 's/^/    /' "${PLAN}"
  echo -e "${CYAN}${BOLD}  ─────────────────────────────────────────────────────${RESET}"
  echo
  echo -e "  ${BOLD}This plan is recommend-only.${RESET} Review it, then apply via the"
  echo -e "  operator workflow after approval."
else
  warn "No remediation.yaml was produced."
  echo  "    • Confirm Loki has the events: ${LOKI_BASE}"
  echo  "    • Check agent output:          journalctl -u rootmedic -n 50"
fi
echo
_pause

# ─── Access points ────────────────────────────────────────────────────────────
HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}'); HOST_IP="${HOST_IP:-localhost}"
hr "Access Points"
echo
echo -e "  ${BOLD}Grafana dashboard${RESET}  →  http://${HOST_IP}:3000  ${YELLOW}(admin / admin)${RESET}"
echo -e "  ${BOLD}Loki API${RESET}           →  http://${HOST_IP}:3100"
echo -e "  ${BOLD}Service logs${RESET}       →  journalctl -u rootmedic -f"
echo -e "  ${BOLD}Run agent again${RESET}    →  rootmedic"
echo -e "  ${BOLD}Config${RESET}             →  ${CONFIG_FILE}"
echo
[[ "${FAILN}" -eq 0 && -f "${PLAN}" ]] \
  && ok "Verification complete — RootMedic is installed, healthy, and healing." \
  || warn "Verification finished with warnings — see notes above."
echo
