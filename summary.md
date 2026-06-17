# RootMedic — Project Summary

## The Story

RootMedic is an AI-driven *medic* for Linux fleets that turns the 2 a.m. incident
scramble into a calm, reviewable workflow. Logs stream off every host through
**Fluent Bit** into a central **Loki** store; the agent queries that stream,
scrubs each event of secrets and PII, then asks three questions in order — *have
we seen this before?* (a fingerprint-keyed cache), *do we have a rule for it?*,
and only if both miss, *what does the LLM think?* Whatever the source, the answer
is a declarative **`remediation.yaml`**: a described fix with exact commands,
rollback steps, a confidence score, and — once an issue recurs past a gate — a
dry-run trace. **Nothing is ever executed automatically.** `apply()` is a
separate, operator-driven path guarded by config snapshots and rollback. Every
incident is archived with tiered retention (and, later, fanned out to alerting
channels). The whole thing installs in about a minute with a single `curl`,
registers as a long-running systemd service, ships a ready-made Grafana
dashboard, and runs entirely on your own infrastructure — including against a
local Ollama model with no outbound calls. It is, deliberately, **powerful but
never reckless**: autonomous in diagnosis, human-approved in action.

## Architecture at a glance

```
Linux Hosts ─▶ Fluent Bit ─▶ Loki ─▶ ingest.py ─▶ redactor.py
[Datadog/alerts] ──webhook──▶ webhook_receiver.py ─▶ redactor.py   (planned)
                                                       │
                                                       ▼
                                          vector_store.py (cache)
                                                       │ miss
                                                       ▼
                                       rule-based plan ─or─ llm_client.py
                                                       │
                                                       ▼
                                          remediation_engine.recommend
                                                  │       │
                                            alerting    archive.py
                                          (deferred)  (tiered retention)
```

Status legend: ✅ shipping · 🟡 partial / works with caveats · 🧪 demo-grade · ⏸️ deferred

---

## Components

Each component is independently owned and independently verifiable. Run the
"Verify" command from the repo root with the venv active
(`source .venv/bin/activate`) unless noted otherwise.

| # | Component | Key files | Status | Verify (one command) |
|---|-----------|-----------|--------|----------------------|
| 1 | Log ingestion & normalization | `ingest.py` | ✅ | `python -m pytest tests/test_fetch_normalize_logs.py -q` |
| 2 | Redaction (PII/secret scrubber) | `redactor.py` | ✅ | `python -m pytest tests/test_redactor.py -q` |
| 3 | Fingerprinting | `fingerprint.py` | ✅ | `python -m pytest tests/test_vector_store.py tests/test_remediation_engine.py -q` |
| 4 | Known-issue cache (vector store) | `vector_store.py` | 🟡 | `python -m pytest tests/test_vector_store.py -q` |
| 5 | Rule-based planner | `fetch_normalize_logs.py` | ✅ | `python -m pytest tests/test_fetch_normalize_logs.py -q` |
| 6 | LLM fallback (+ fail-fast) | `llm_client.py` | ✅ | see §6 below |
| 7 | Remediation engine (recommend/apply) | `remediation_engine.py` | ✅ | `python -m pytest tests/test_remediation_engine.py -q` |
| 8 | Agent orchestrator + loop mode | `fetch_normalize_logs.py` | ✅ | `python fetch_normalize_logs.py --help` |
| 9 | Archive (tiered retention) | `archive.py` | ✅ | `python -m pytest tests/test_archive.py -q` |
| 10 | Alerting (Slack/webhook fan-out) | `alerting.py`, `alert_plugins.py` | ⏸️ | `python -m pytest test_alerting.py -q` |
| 11 | Logging stack (Loki+Fluent Bit+Grafana) | `Deployment/docker-compose.yml` | ✅ | see §11 below |
| 12 | Grafana dashboard provisioning | `Deployment/grafana-provisioning/` | ✅ | see §12 below |
| 13 | Fluent Bit collector + Ansible | `Deployment/fluent-bit*`, `Deployment/fluent-bit/` | 🟡 | `bash -n` / ansible `--check` |
| 14 | Installer | `scripts/install.sh` | ✅ | `bash -n scripts/install.sh` + §14 |
| 15 | Post-install verifier / live demo | `scripts/verify_install.sh` | ✅ | `sudo NONINTERACTIVE=1 bash scripts/verify_install.sh` |
| 16 | Uninstaller | `scripts/cleanup.sh` | ✅ | `bash -n scripts/cleanup.sh` |
| 17 | Dev deploy utility | `scripts/dev-deploy.sh` | 🧪 | `bash scripts/dev-deploy.sh` (SCP to test VM) |
| 18 | End-to-end demo | `demo.py` | 🧪 | `python demo.py --dry-run` |
| 19 | CI/CD | `Jenkinsfile`, `ci/` | 🟡 | `docker compose -f ci/test-install/docker-compose.yml up` |
| 20 | Marketing site | `web/` | ✅ | open `web/index.html` in a browser |

**Whole-suite gate:** `python -m pytest -q` → currently **129 passing**.

---

### 1. Log ingestion & normalization — `ingest.py`
Queries Loki over a 1-hour lookback for error/warning lines and flattens streams
into `{timestamp, host, unit, message}`. Loki URL is currently hard-coded to
`localhost:3100` (fine for single-node; config-driven URL is a known follow-up).
**Verify:** `python -m pytest tests/test_fetch_normalize_logs.py -q`

### 2. Redaction — `redactor.py`
Scrubs JWTs, AWS keys, Bearer/`password=`/`token=` assignments, emails, DB
connection strings, and long hex/base64 blobs from every event *before* it
reaches the LLM, an alert, or the archive.
**Verify:** `python -m pytest tests/test_redactor.py -q`

### 3. Fingerprinting — `fingerprint.py`
Stable, content-based issue IDs shared by the engine, alert dedup, and the cache.
**Verify:** `python -m pytest tests/test_remediation_engine.py -q`

### 4. Known-issue cache — `vector_store.py`
Fingerprint-keyed lookup/store/forget over `known_issues.json`, with the same
surface a Qdrant-backed embedding store would expose (swap is contained). 🟡 MVP
backend is JSON, not real embeddings yet.
**Verify:** `python -m pytest tests/test_vector_store.py -q`

### 5. Rule-based planner — `build_remediation_plan()` in `fetch_normalize_logs.py`
Deterministic, LLM-free fixes for common faults: connection-refused, OOM, disk
full, **filesystem errors (EXT4/XFS, read-only remount, I/O error)**, and
**service crashes (segfault, `code=killed`, failed state)**.
**Verify (rule coverage):**
```bash
python - <<'PY'
import fetch_normalize_logs as f; from remediation_engine import RemediationEngine
e=RemediationEngine()
for unit,msg in [("nginx.service","EXT4-fs error (device sda1): remounting read-only"),
                 ("mysqld.service","Out of memory: Killed process 4821 (mysqld)"),
                 ("nginx.service","nginx.service: Main process exited, code=killed")]:
    p=f.build_remediation_plan({"message":msg,"unit":unit,"host":"h","timestamp":"t"},e)
    print("MATCH" if p else "MISS", "—", (p.description if p else msg))
PY
```

### 6. LLM fallback — `llm_client.py`
OpenAI-compatible call to LiteLLM/Ollama, consulted only when cache and rules
miss. Raises `LLMUnavailable` on transport failure so the agent stops retrying a
slow/unreachable LLM for the rest of a run; timeout is tunable via
`ROOTMEDIC_LLM_TIMEOUT`; output bounded with `max_tokens`.
**Verify (live endpoint):**
```bash
curl -sS -o /dev/null -w 'HTTP %{http_code}\n' -X POST \
  "$LITELLM_BASE_URL/v1/chat/completions" -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $LITELLM_API_KEY" \
  -d '{"model":"'"$LITELLM_MODEL"'","messages":[{"role":"user","content":"ping"}],"max_tokens":8}'
```

### 7. Remediation engine — `remediation_engine.py`
Two autonomy levels (RECOMMEND → no dry-run; VALIDATED → dry-run trace past the
occurrence gate). Writes `remediation.yaml`. `apply()` is the **only** path that
runs subprocesses and owns snapshot + rollback — invoked separately after
operator approval.
**Verify:** `python -m pytest tests/test_remediation_engine.py -q`

### 8. Agent orchestrator + loop mode — `fetch_normalize_logs.py`
Wires the pipeline. `--loop --interval N` keeps the systemd service alive between
scans; a single failed scan never kills the daemon.
**Verify:** `python fetch_normalize_logs.py --help` (shows `--loop`/`--interval`);
on a host, `systemctl is-active rootmedic` → `active`.

### 9. Archive — `archive.py`
Per-incident YAML under `archive/<client>/<YYYY-MM>/<fp>-<ts>/`; `prune_archive`
enforces 30/180/365-day tiers.
**Verify:** `python -m pytest tests/test_archive.py -q`

### 10. Alerting — `alerting.py`, `alert_plugins.py` ⏸️ deferred
Slack/webhook fan-out with SQLite dedup/escalation. Code ships and is tested, but
is **not wired into the installer** yet (installer no longer prompts for Slack).
**Verify:** `python -m pytest test_alerting.py -q`

### 11. Logging stack — `Deployment/docker-compose.yml`
Loki (3100) + Fluent Bit + Grafana (3000), SELinux-labelled, fully-qualified
images, runs under Docker or Podman.
**Verify:**
```bash
docker compose -f Deployment/docker-compose.yml up -d
curl -fsS http://localhost:3100/ready && echo " Loki OK"
curl -fsS -o /dev/null -w 'Grafana HTTP %{http_code}\n' http://localhost:3000
```

### 12. Grafana dashboard provisioning — `Deployment/grafana-provisioning/`
Auto-provisions the Loki datasource and the "RootMedic — System Health"
dashboard on first boot.
**Verify:** open `http://<host>:3000` (admin/admin) → Dashboards → *RootMedic — System Health* is present with panels populated.

### 13. Fluent Bit collector + Ansible — `Deployment/fluent-bit*`
Per-host collector config (systemd journal → priority filter → Loki) plus an
Ansible role for Debian/Ubuntu and RHEL/Fedora. 🟡 Ansible path is less exercised
than the container path.
**Verify:** `podman ps | grep fluent-bit` (container) or
`ansible-playbook Deployment/fluent-bit-deploy.yml --check`.

### 14. Installer — `scripts/install.sh`
One-command install: OS deps, repo clone, venv, Loki stack, Fluent Bit, LLM
config + connectivity test, config (mode 600), systemd unit (loop mode), CLI shim.
**Verify:** `bash -n scripts/install.sh`; full run:
`curl -fsSL https://raw.githubusercontent.com/Incorpify-LLC/rootmedic/main/scripts/install.sh | sudo bash`

### 15. Post-install verifier / live demo — `scripts/verify_install.sh`
Health-checks every component, then injects 3 synthetic faults into Loki, runs
the agent, and prints the resulting `remediation.yaml`. Recommend-only — runs
nothing against the host.
**Verify:** `sudo NONINTERACTIVE=1 bash scripts/verify_install.sh`

### 16. Uninstaller — `scripts/cleanup.sh`
Destructive teardown (service, config, containers, Fluent Bit) behind an explicit
confirmation token. Has placeholder hooks for future multi-node cluster cleanup.
**Verify:** `bash -n scripts/cleanup.sh`; on a host: `sudo bash scripts/cleanup.sh`.

### 17. Dev deploy utility — `scripts/dev-deploy.sh` 🧪
SCPs the `scripts/` files to a test VM (key-auth pre-flight); `--remote-install`
and `--verify` are opt-in.
**Verify:** `bash scripts/dev-deploy.sh` (prints the on-VM run steps).

### 18. End-to-end demo — `demo.py` 🧪
Drives the Podman stack and 4 scenarios (service_crash, oom_kill, disk_full,
connection_refused) through the full pipeline.
**Verify:** `python demo.py --dry-run`; unit tests: `python -m pytest tests/test_demo_scenarios.py -q`.

### 19. CI/CD — `Jenkinsfile`, `ci/` 🟡
Jenkins pipeline plus a containerized `ci/test-install` harness that runs the
installer non-interactively against a mock LLM + Loki. KVM-based VM provisioning
has a known libvirt URI issue (`ci/KNOWN_ISSUES.md`).
**Verify:** `docker compose -f ci/test-install/docker-compose.yml up --abort-on-container-exit`

### 20. Marketing site — `web/`
Static landing page (Tailwind) with hero, feature grid, live-terminal demo,
Trust & Safety section, distro support, and the install CTA (points at
`scripts/install.sh`).
**Verify:** open `web/index.html` in a browser; the install command and nav
anchors (incl. `#trust`) resolve.

---

## Known follow-ups (tracked, not yet done)

- ⏸️ **Webhook receiver** (`webhook_receiver.py`) for the Datadog/cloud push path — referenced in the architecture, not yet built.
- ⏸️ **Alerting wired into install** — modules exist (§10); reconnect once the channel set is decided.
- 🟡 **Config-driven Loki URL** — `ingest.py` hard-codes `localhost:3100`.
- 🟡 **Real embeddings** behind `vector_store.py` (currently JSON MVP).
- 🟡 **CI VM provisioning** — libvirt `qemu:///system` vs `qemu:///session` (`ci/KNOWN_ISSUES.md`).
