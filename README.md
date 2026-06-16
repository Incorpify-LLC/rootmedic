# RootMedic

AI-driven log analysis and **recommend-only** remediation agent for Linux systems. Centralizes system logs, uses an LLM (with a fingerprint-keyed cache in front of it) to diagnose root causes, and emits declarative `remediation.yaml` artifacts plus alerts. Execution always requires explicit human approval — see [`docs/product/log-analyzer-plan-A.md`](docs/product/log-analyzer-plan-A.md).

## Quickstart

**Prerequisites:** Python 3.13, Docker, Ollama (optional, for local LLM)

```bash
git clone <repo-url> && cd rootmedic
source .venv/bin/activate
pip install requests

# Start the logging stack (Loki + Promtail + Grafana)
docker compose -f Deployment/docker-compose.yml up -d

# Fetch and normalize logs from Loki
python fetch_normalize_logs.py
```

## Architecture

```
Linux Hosts ─▶ Alloy/Promtail ─▶ Loki ─▶ ingest.py ─▶ redactor.py
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
                                          (plugins)   (tiered retention)
```

The agent queries Loki for error/warning events, normalises and **redacts** them, then tries a fingerprint-keyed known-issue cache. On a cache miss it falls back to the rule-based planner and finally to the LLM. The resulting plan goes through `remediation_engine.recommend()` — which attaches a dry-run trace once an issue is past the occurrence gate, writes `remediation.yaml`, fans an alert through every configured plugin (Slack, generic webhook), and archives the incident with tiered retention.

No commands are executed automatically. `remediation_engine.apply()` is a separate entrypoint intended to be invoked from a CLI or web UI after an operator reviews the generated `remediation.yaml`; it handles config snapshots and rollback on failure.

## Deployment

The `Deployment/` directory contains three strategies for shipping logs to the central Loki instance.

### 1. Docker Compose (local dev)

Launches the full stack locally in containers. Ideal for development and testing.

```bash
docker compose -f Deployment/docker-compose.yml up -d
```

| Service | Port | Purpose |
|---|---|---|
| Loki | `3100` | Log aggregation and query API |
| Promtail | — | Reads host journald, forwards to Loki |
| Grafana | `3000` | Dashboards (login: `admin` / `admin`) |

### 2. Ansible + Promtail (push to Linux targets)

Pushes Promtail onto remote Debian/Ubuntu hosts so they ship systemd-journal logs to the central Loki instance. Filters priority level 3 (error) and 4 (warning) only.

```bash
cd Deployment/promtail
# Edit inventory.ini with your target hosts, then:
ansible-playbook -i inventory.ini playbook.yml
```

What it does:
- Downloads and installs the Promtail binary
- Deploys `promtail-config.yml` with your Loki endpoint
- Installs a systemd service so Promtail survives reboots

### 3. Ansible + Alloy (push Grafana Alloy collector)

An alternative collector using [Grafana Alloy](https://grafana.com/docs/alloy/latest/), which also ships host-level metrics alongside journald logs. Prefer Alloy for richer observability (metrics + logs in one agent).

```bash
ansible-playbook -i Deployment/inventory.ini Deployment/alloy-deploy.yml
```

## Tech Stack

| Layer | Technology |
|---|---|
| Log aggregation | Loki, Promtail |
| Visualization | Grafana |
| AI / LLM | Ollama (local), OpenAI API |
| Agent runtime | Python 3.13 |
| Deployment | Docker Compose, Ansible |
| Data | SQLite |

## Repository Structure

```
.
├── fetch_normalize_logs.py   # Agent orchestrator + entrypoint (wires pipeline stages)
├── ingest.py                 # Loki query + log normalization
├── fingerprint.py            # Stable issue fingerprinting
├── redactor.py               # PII / secret scrubber (regex)
├── vector_store.py           # Fingerprint-keyed known-issue cache
├── llm_client.py             # LiteLLM / Ollama fallback
├── remediation_engine.py     # Recommend-only engine (RECOMMEND/VALIDATED), apply() for human-approved exec
├── alert_plugins.py          # AlertPlugin base + SlackPlugin + WebhookPlugin (alerting deferred — not yet wired into install)
├── alerting.py               # AlertManager (SQLite dedup, fan-out)
├── archive.py                # Per-incident YAML + tiered retention
├── demo.py                   # End-to-end healing demo (Podman + synthetic faults)
├── create_sample_data.py     # Generates sample data in user_database.db
├── linked-data.py            # Linked list demo with SQLite backend
├── Modelfile                 # Ollama model definition for local inference
├── install.sh                # Production install (systemd service + Loki stack) — at root for a clean curl URL
├── scripts/                  # Operator & developer shell scripts
│   ├── verify_install.sh     #   Post-install health check + live healing demo
│   ├── cleanup.sh            #   Destructive uninstaller
│   └── dev-deploy.sh         #   Push install.sh + helpers to a test VM (developer utility)
├── Deployment/               # Local dev logging stack
│   ├── docker-compose.yml    #   Loki + Fluent Bit + Grafana
│   ├── loki-config.yaml      #   Loki server config (7-day retention)
│   ├── fluent-bit.conf       #   Fluent Bit collector config
│   ├── grafana-provisioning/ #   Auto-provisioned datasource + dashboard
│   └── fluent-bit/           #   Ansible role (templates, files)
├── docs/                     # Installation, providers, troubleshooting
│   └── product/              #   produc.md, log-analyzer-plan-A.md (design + rationale)
├── ci/                       # Jenkins pipeline + Ansible CI playbooks
├── tests/                    # pytest suite
└── web/                      # Static marketing landing page
```

## Contributing

See [`AGENTS.md`](AGENTS.md) for coding conventions, commit guidelines, and development commands.
