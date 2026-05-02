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

Logs flow from Linux hosts through Promtail into Loki. The Python agent queries Loki for error/warning events, normalizes them into structured JSON, and feeds them to an LLM for root cause analysis and remediation recommendations.

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
├── fetch_normalize_logs.py   # Queries Loki, normalizes logs to JSON
├── linked-data.py            # Linked list demo with SQLite backend
├── create_sample_data.py     # Generates sample data in user_database.db
├── Modelfile                 # Ollama model definition for local inference
├── Deployment/               # Docker Compose, Ansible playbooks, configs
│   ├── docker-compose.yml    # Loki + Promtail + Grafana
│   ├── files/                # Alloy & Loki YAML configs
│   └── promtail/             # Ansible playbook, packages, templates
└── log-analyzer-plan-A.md    # Design document and project rationale
```

## Contributing

See [`AGENTS.md`](AGENTS.md) for coding conventions, commit guidelines, and development commands.
