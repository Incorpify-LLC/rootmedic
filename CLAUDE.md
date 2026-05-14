# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RootMedic is an AI-driven log analysis and autonomous remediation agent for Linux systems. It centralizes system logs, uses LLMs to detect issues and diagnose root causes, then applies fixes — optionally with human approval or fully autonomously.

**Architecture:**

```
Linux Hosts ──▶ Promtail ──▶ Loki ──▶ fetch_normalize_logs.py ──▶ AI Agent (LLM)
                                         │                              │
                                         ▼                              ▼
                                      Grafana                    Remediation
                                      (dashboards)               (restart, config fix, rollback)
```

Logs flow from Linux hosts through Promtail into Loki. The Python agent queries Loki for error/warning events, normalizes them into structured JSON, and feeds them to an LLM for root cause analysis. Remediation runs through a graduated autonomy model defined in `remediation_engine.py`.

## Tech Stack

- **Log aggregation**: Loki, Promtail, Grafana Alloy
- **Visualization**: Grafana (port 3000, login admin/admin)
- **AI / LLM**: Ollama (local via `Modelfile`), OpenAI API
- **Agent runtime**: Python 3.13
- **Data**: SQLite (`user_database.db`)
- **CI/CD**: Jenkins (`Jenkinsfile`)
- **Deployment**: Docker Compose, Ansible

## Build, Test, and Development Commands

Activate the virtual environment before running any script:

```bash
source .venv/bin/activate
```

### Install Dependencies

```bash
pip install -r requirements.txt   # installs requests, pytest
```

### Run Tests

```bash
# Run all tests
python -m pytest tests/ -v

# Run a specific test file
python -m pytest tests/test_remediation_engine.py -v

# Run a specific test function
python -m pytest tests/test_remediation_engine.py::test_dry_run_gate -v
```

### Run the Application

```bash
# Start the logging stack (Loki + Promtail + Grafana)
docker compose -f Deployment/docker-compose.yml up -d

# Fetch and normalize logs from Loki
python fetch_normalize_logs.py

# Generate sample SQLite data
python create_sample_data.py

# Run linked-list demo against SQLite
python linked-data.py
```

### Ollama Model (optional, for local LLM)

```bash
ollama create rootmedic -f Modelfile
```

## High-Level Code Architecture

### Core Modules

- **`fetch_normalize_logs.py`** — Agent entry point. Queries Loki for error/warning logs, normalizes them into structured JSON, and triggers the remediation engine. Contains the main loop that bridges observability data to the AI analysis layer.

- **`remediation_engine.py`** — Graduated autonomy engine with three tiers:
  1. **Recommend only** — human-in-the-loop for first N occurrences of a new issue type.
  2. **Semi-autonomous** — applies fix only after dry-run simulation or if confidence > 95%.
  3. **Full autonomous** — after pattern has been validated in production via canary deployments.
  Includes rollback logic and state tracking (`remediation_state.json`).

- **`linked-data.py`** — Standalone linked list implementation backed by SQLite. Used for data-structure demonstrations and testing SQLite connectivity.

- **`create_sample_data.py`** — Populates `user_database.db` with synthetic test rows for demos.

### Deployment Assets (`Deployment/`)

- **`docker-compose.yml`** — Local dev stack: Loki (3100), Promtail, Grafana (3000).
- **`loki-config.yaml`** — Loki server configuration (7-day retention).
- **`promtail-config.yml`** — Promtail scraper targeting systemd-journal, filtering priority 3 (error) and 4 (warning).
- **`alloy-deploy.yml`** — Ansible playbook for Grafana Alloy collector (metrics + logs).
- **`inventory.ini`** — Host inventory for Alloy playbook.
- **`promtail/`** — Ansible playbook, templates, and inventory for pushing Promtail onto remote Debian/Ubuntu hosts.

### CI/CD (`ci/`)

- **`Jenkinsfile`** — Full pipeline: checkout → install deps → unit tests → provision VMs → deploy logging → deploy agent → install Grafana dashboard → inject fault → collect remediation evidence.
- **`provision-vms.yml`** — Ansible playbook to spin up KVM VMs for CI demo.
- **`deploy-logging.yml`** — Deploys log aggregation stack to VM1.
- **`deploy-rootmedic.yml`** — Deploys RootMedic agent to VM2.
- **`inject-fault.yml`** — Injects a controlled fault for autonomous healing verification.
- **`rootmedic-dashboard.json`** — Grafana dashboard exported for CI import.

### Tests (`tests/`)

- **`test_remediation_engine.py`** — Tests for the graduated autonomy engine, dry-run gates, and rollback behavior.
- **`test_fetch_normalize_logs.py`** — Tests for log fetching and normalization logic.
- **`conftest.py`** — Shared pytest fixtures.

## Key Conventions

- **Indentation**: 4 spaces (PEP 8).
- **Naming**: `snake_case` for functions and variables; `PascalCase` for classes.
- **Imports**: Standard library first, then third-party, separated by a blank line.
- No formatter or linter is currently configured; `pytest` is the test runner.
