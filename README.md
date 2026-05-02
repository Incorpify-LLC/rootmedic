# RootMedic

AI-driven log analysis and autonomous remediation agent for Linux systems. Centralizes system logs, uses LLMs to detect issues and diagnose root causes, then applies fixes — optionally with human approval or fully autonomously.

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
Linux Hosts ──▶ Promtail ──▶ Loki ──▶ fetch_normalize_logs.py ──▶ AI Agent (LLM)
                                         │                              │
                                         ▼                              ▼
                                      Grafana                    Remediation
                                      (dashboards)               (restart, config fix, rollback)
```

Logs flow from Linux hosts through Promtail into Loki. The Python agent queries Loki for error/warning events, normalizes them into structured JSON, and feeds them to an LLM for root cause analysis and remediation recommendations. Remediation runs through a [graduated autonomy model](remediation_engine.py) — human approval for new issues, dry-run gates for semi-trusted patterns, and full auto-apply once validated.

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
├── fetch_normalize_logs.py   # Agent: fetches logs, triggers remediation
├── remediation_engine.py     # Graduated autonomy + rollback logic
├── linked-data.py            # Linked list demo with SQLite backend
├── create_sample_data.py     # Generates sample data in user_database.db
├── Modelfile                 # Ollama model definition for local inference
├── Deployment/
│   ├── docker-compose.yml    # Loki + Promtail + Grafana (local dev)
│   ├── alloy-deploy.yml      # Ansible playbook for Alloy collector
│   ├── inventory.ini         # Host inventory for Alloy playbook
│   ├── loki-config.yaml      # Loki server config (7-day retention)
│   ├── promtail-config.yml   # Promtail scraper config
│   ├── files/                # Alloy YAML config
│   └── promtail/             # Ansible playbook, templates, inventory
└── log-analyzer-plan-A.md    # Design document and project rationale
```

## Contributing

See [`AGENTS.md`](AGENTS.md) for coding conventions, commit guidelines, and development commands.
