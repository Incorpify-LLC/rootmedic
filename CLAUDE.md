# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RootMedic is an AI-driven log analysis and **recommend-only** remediation agent for Linux systems. It centralizes system logs, uses an LLM (with a vector-store cache in front of it) to diagnose root causes, and emits declarative `remediation.yaml` artifacts plus alerts. Execution of those remediations always requires explicit human approval — see `docs/product/log-analyzer-plan-A.md`.

**Architecture:**

```
Linux Hosts ─▶ Fluent Bit ─▶ Loki ─▶ ingest.py ─▶ redactor.py
[Datadog/alerts] ──webhook──▶ webhook_receiver.py ─▶ redactor.py
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

Logs flow from Linux hosts through **Fluent Bit** into Loki (Scenario 1 and 3), or arrive as webhook POSTs from Datadog or any alertmanager (Scenario 2 — cloud). The agent (`fetch_normalize_logs.py`) queries Loki; `webhook_receiver.py` handles the push path. Both paths then **redact** each event, try the fingerprint-keyed known-issue cache (`vector_store.py`), fall back to the rule-based planner and finally to the LLM. The resulting `RemediationPlan` is run through `remediation_engine.recommend()` which attaches a dry-run trace once an issue is past the occurrence gate, writes `remediation.yaml`, fans an alert out through every configured plugin (Slack / generic webhook), and archives the incident with tiered retention. **No commands are ever executed automatically** — `remediation_engine.apply()` is a separate entrypoint intended to be called from a CLI or web UI after operator review.

## Tech Stack

- **Log aggregation**: Loki + **Fluent Bit** (collector on each host; replaces Promtail and Alloy)
- **Visualization**: Grafana (port 3000, login admin/admin)
- **AI / LLM**: LiteLLM proxy (`https://litellm.saneax.in`, model `smart`) in production; Ollama (local via `Modelfile`) for dev; configured via `/etc/rootmedic/config.yaml` after `install.sh`
- **Agent runtime**: Python 3.13
- **Alerting**: Slack/webhook fan-out in `alerting.py` / `alert_plugins.py` (dedup/escalation) — **deferred: not yet wired into `install.sh`; to be built out later**
- **Data**: SQLite (`user_database.db`, `alerts_state.db`)
- **CI/CD**: Jenkins (`Jenkinsfile`)
- **Deployment**: Docker Compose / Podman Compose, Ansible

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
# Start the logging stack (Loki + Fluent Bit + Grafana) — Docker or Podman
docker compose -f Deployment/docker-compose.yml up -d
# or with Podman:
podman-compose -f Deployment/docker-compose.yml up -d

# Fetch and normalize logs from Loki
python fetch_normalize_logs.py

# Generate sample SQLite data
python create_sample_data.py

# Run linked-list demo against SQLite
python linked-data.py
```

### End-to-End Demo (`demo.py`)

`demo.py` manages the Podman stack, injects synthetic log scenarios into Loki, runs the full agent pipeline, and verifies healing. It uses `podman-compose` (not `docker compose`).

```bash
# List available scenarios without running
python demo.py --dry-run

# Run all scenarios (starts/stops stack automatically)
python demo.py

# Run a single scenario
python demo.py --scenario service_crash

# Force auto-apply even for RECOMMEND-level issues (demo mode)
python demo.py --force-apply

# Assume stack is already running
python demo.py --no-stack
```

Scenarios: `service_crash`, `oom_kill`, `disk_full`, `connection_refused`. Set `DEMO_FORCE_APPLY=1` as an environment variable to trigger apply without the `--force-apply` flag.

### Operator & developer scripts (`scripts/`)

All shell scripts live under `scripts/`:

- **`scripts/install.sh`** — production install (below).
- **`scripts/verify_install.sh`** — post-install health check + live healing demo: checks every component, injects 3 synthetic faults into Loki, runs the agent, and prints the resulting `remediation.yaml`. Honors `NONINTERACTIVE=1`.
- **`scripts/cleanup.sh`** — destructive uninstaller (service, config, containers, Fluent Bit). Requires an explicit confirmation token.
- **`scripts/dev-deploy.sh`** — developer utility that SCPs the `scripts/` files to a test VM. SCP-only by default; `--remote-install` / `--verify` are opt-in.

### Production Install (`scripts/install.sh`)

Installs RootMedic as a systemd service on any Linux host:

```bash
curl -fsSL https://raw.githubusercontent.com/Incorpify-LLC/rootmedic/main/scripts/install.sh | sudo bash
# or with API key pre-set:
LITELLM_API_KEY=sk-... curl ... | sudo -E bash
```

Installs to `/opt/rootmedic`, writes config to `/etc/rootmedic/config.yaml` (mode 600), registers a `rootmedic.service` systemd unit, and installs a `/usr/local/bin/rootmedic` CLI shim. Key env overrides: `LITELLM_BASE_URL`, `LITELLM_MODEL`, `INSTALL_DIR`. Verify afterward with `sudo bash /opt/rootmedic/scripts/verify_install.sh`. **Alerting (Slack/webhook) is deferred** — the installer no longer prompts for it.

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

- **`demo.py`** — End-to-end autonomous healing demo. Manages the Podman stack via `podman-compose`, injects synthetic error logs into Loki, drives the full agent pipeline (ingest → redact → resolve → recommend/apply), and verifies that expected remediation commands were run. Uses `DEMO_FORCE_APPLY=1` or `--force-apply` to bypass the VALIDATED gate for demo purposes.

- **`linked-data.py`** — Standalone linked list implementation backed by SQLite. Used for data-structure demonstrations and testing SQLite connectivity.

- **`create_sample_data.py`** — Populates `user_database.db` with synthetic test rows for demos.

### Web (`web/`)

- **`web/index.html`**, **`web/style.css`** — Static marketing landing page for RootMedic. Not part of the agent runtime; served separately.

### Deployment Assets (`Deployment/`)

- **`docker-compose.yml`** — Local dev stack: Loki (3100), Fluent Bit, Grafana (3000).
- **`loki-config.yaml`** — Loki server configuration (7-day retention).
- **`fluent-bit.conf`** — Fluent Bit collector config: systemd input → priority 3/4 filter → Loki output. Uses `LOKI_HOST` / `LOKI_PORT` env vars.
- **`fluent-bit-parsers.conf`** — Standard syslog and JSON parsers for Fluent Bit.
- **`fluent-bit-deploy.yml`** — Ansible playbook to install and configure Fluent Bit on Debian/Ubuntu and RHEL/CentOS hosts. Requires `loki_host` and `loki_port` vars.
- **`fluent-bit/`** — Ansible role structure: `templates/fluent-bit.conf.j2` (Jinja2 config template), `files/fluent-bit-parsers.conf`.
- **`inventory.ini`** / **`inventory.yaml`** — Host inventory files for Ansible playbooks.
- **`alloy-deploy.yml`**, **`promtail/`** — Legacy Alloy and Promtail deployment assets; superseded by Fluent Bit but retained for reference.

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
- **`tests/test_demo_scenarios.py`** — Unit tests for `demo.py` with mocked Loki/agent dependencies. Covers `SCENARIOS` dict structure, `verify_healing`, `push_log_to_loki`, `run_scenario`, `run_all_scenarios`, and CLI argument parsing.
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
