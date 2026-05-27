# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RootMedic is an AI-driven log analysis and **recommend-only** remediation agent for Linux systems. It centralizes system logs, uses an LLM (with a vector-store cache in front of it) to diagnose root causes, and emits declarative `remediation.yaml` artifacts plus alerts. Execution of those remediations always requires explicit human approval — see `log-analyzer-plan-A.md`.

**Architecture:**

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

Logs flow from Linux hosts through Alloy or Promtail into Loki. The agent (`fetch_normalize_logs.py`) queries Loki, normalises and **redacts** each event, then tries the fingerprint-keyed known-issue cache (`vector_store.py`). On a miss it falls back to the rule-based planner and finally to the LLM. The resulting `RemediationPlan` is run through `remediation_engine.recommend()` which attaches a dry-run trace once an issue is past the occurrence gate, writes `remediation.yaml`, fans an alert out through every configured plugin (Slack / generic webhook), and archives the incident with tiered retention. **No commands are ever executed automatically** — `remediation_engine.apply()` is a separate entrypoint intended to be called from a CLI or web UI after operator review.

## Tech Stack

- **Log aggregation**: Loki, Promtail, Grafana Alloy
- **Visualization**: Grafana (port 3000, login admin/admin)
- **AI / LLM**: Ollama (local via `Modelfile`), OpenAI API
- **Agent runtime**: Python 3.13
- **Alerting**: Slack incoming webhooks (with dedup/escalation in `alerting.py`)
- **Data**: SQLite (`user_database.db`, `alerts_state.db`)
- **CI/CD**: Jenkins (`Jenkinsfile`)
- **Deployment**: Docker Compose, Ansible

## Build, Test, and Development Commands

Activate the virtual environment before running any script:

```bash
source .venv/bin/activate
```

### Install Dependencies

```bash
pip install -r requirements.txt   # installs requests, PyYAML, pytest
```

### Run Tests

```bash
# Run all tests (auto-discovers both tests/ and the root-level test_alerting.py)
python -m pytest -v

# Run only the tests/ directory (skips root-level test_alerting.py)
python -m pytest tests/ -v

# Run a specific test file
python -m pytest tests/test_remediation_engine.py -v

# Run a specific test function
python -m pytest tests/test_remediation_engine.py::test_dry_run_gate -v
```

There is no `pytest.ini`/`pyproject.toml` config, so pytest auto-discovers from the current directory. `test_alerting.py` lives at the repo root, not under `tests/` — pointing pytest at `tests/` alone will silently skip it.

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

### Pipeline modules

- **`fetch_normalize_logs.py`** — Agent orchestrator. Wires every pipeline stage together; never executes commands directly. Re-exports `fetch_logs` and `parse_and_normalize` for back-compat.

- **`ingest.py`** — Loki query + log normalization. Pure HTTP/parsing, no remediation dependency.

- **`fingerprint.py`** — Stable issue fingerprinting (used by the engine, the alert dedup state, and the vector store).

- **`redactor.py`** — Regex-based PII / secret scrubber. Runs on every event before it reaches the LLM, the alert channels, or the archive. Patterns cover JWTs, AWS keys, Bearer tokens, `password=`/`token=` style assignments, emails, DB connection strings, and long hex/base64 blobs.

- **`vector_store.py`** — Fingerprint-keyed known-issue cache. Exposes the same `lookup` / `store` / `forget` surface a Qdrant-backed implementation would; swapping in real embeddings is a contained change to those two methods. Persists to `known_issues.json`.

- **`llm_client.py`** — LiteLLM fallback. Consulted only when both the vector store and the rule-based planner come up empty.

- **`remediation_engine.py`** — Recommend-only engine with two autonomy levels:
  1. **RECOMMEND** — new or rarely-seen issue; emits `remediation.yaml`, no dry-run.
  2. **VALIDATED** — occurrence count past the gate; attaches a dry-run trace.
  Neither tier auto-applies. `apply(plan)` is the only path that actually runs subprocess and is intended to be invoked from a CLI/web UI after explicit operator approval; it owns the snapshot + rollback logic.

- **`alert_plugins.py`** — `AlertPlugin` base class, `SlackPlugin`, `WebhookPlugin`, and `build_default_plugins(config)` registry. Adding a new channel (email, IRC, PagerDuty) is a self-contained subclass.

- **`alerting.py`** — `AlertManager` plus SQLite-backed dedup state (`alerts_state.db`). Fans every alert out to every configured plugin; failures in one plugin do not block the others. Configured via `alerts.yml` (`slack_webhook_url`, `webhook_url`, `webhook_headers`, `dedup_window_minutes`, `escalation_after_minutes`, `grafana_base_url`) or the `SLACK_WEBHOOK_URL` / `ALERT_WEBHOOK_URL` environment variables.

- **`archive.py`** — Per-incident YAML archive under `archive/<client>/<YYYY-MM>/<fp>-<ts>/` (`incident.yaml`, `remediation.yaml`, `dry_run.log`). `prune_archive(tier=)` enforces 30d / 180d / 365d retention windows.

### Demo / data modules

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
- **`run_demo.sh`** — Local end-to-end simulation of the CI pipeline (runs tests, injects 3 faults sequentially, prints autonomous-recovery evidence). Useful for reproducing pipeline behavior without provisioning VMs.
- **`KNOWN_ISSUES.md`** — Documents an open libvirt URI mismatch (`qemu:///system` vs `qemu:///session`) that blocks VM provisioning in `provision-vms.yml`. Check here before debugging VM-provisioning failures.

### Tests

- **`tests/test_remediation_engine.py`** — Recommend/apply paths, dry-run gating, snapshot/rollback, fingerprinting.
- **`tests/test_fetch_normalize_logs.py`** — Log normalization, rule-based planner, vector-store-first resolution, agent pipeline.
- **`tests/test_redactor.py`** — Secret/PII patterns and event sanitization.
- **`tests/test_vector_store.py`** — Known-issue cache lookup, persistence, retention.
- **`tests/test_archive.py`** — Incident YAML output and tier-based pruning.
- **`test_alerting.py`** (repo root) — `AlertManager`, plugin registry, Slack/Webhook plugins, dedup and escalation.
- **`tests/conftest.py`** — Shared pytest fixtures and runtime-state cleanup (also clears `known_issues.json`, `archive/`, `remediation.yaml`).

## Key Conventions

- **Indentation**: 4 spaces (PEP 8).
- **Naming**: `snake_case` for functions and variables; `PascalCase` for classes.
- **Imports**: Standard library first, then third-party, separated by a blank line.
- No formatter or linter is currently configured; `pytest` is the test runner.

## Runtime Artifacts (gitignored)

These files are written by the agent at runtime and should not be committed or treated as source:

- `remediation_state.json` — issue-pattern occurrence counts and success/fail history. Deleting it resets every pattern to RECOMMEND.
- `dry_run.log` — most recent dry-run simulation output.
- `remediation.yaml` — most recent declarative plan emitted by `recommend()`. A per-incident copy also lives under `archive/`.
- `known_issues.json` — fingerprint-keyed known-issue cache (the MVP backend for `vector_store.py`).
- `archive/<client>/<YYYY-MM>/<fp>-<ts>/` — per-incident records (`incident.yaml`, `remediation.yaml`, optional `dry_run.log`). Pruned by `archive.prune_archive(tier=)`.
- `.rollback_snapshots/` — config snapshots captured before `apply()` runs; consumed by rollback logic.
- `user_database.db`, `alerts_state.db` — SQLite stores; regenerable via `create_sample_data.py` and the alerting module respectively.
