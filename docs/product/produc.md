# RootMedic — Product Decisions & MVP Definition

_Archived from product session on 2026-06-16. Use this as the canonical reference for scope, positioning, and architecture decisions made to date._

---

## What RootMedic Is

An AI-driven, **recommend-only** log analysis and remediation agent for Linux systems. It reads logs from any source, diagnoses root causes using an LLM (with a fingerprint-keyed cache in front of it), and emits a `remediation.yaml` artifact plus a Slack/webhook alert. **No command is ever executed without explicit operator approval.**

The `remediation_engine.apply()` path exists but is a separate, deliberate entrypoint — it is never called automatically.

---

## MVP Deployment Scenarios

Three scenarios are in scope for MVP. All three share the same codebase and the same pipeline internals; only the log ingestion path and LLM endpoint differ.

---

### Scenario 1 — Datacenter

**Who**: SRE / ops team managing physical or bare-metal Linux servers.

**Log collection**: Fluent Bit installed on every host, forwarding to a central Loki instance. One RootMedic agent per cluster pulls from that Loki endpoint.

**Agent topology**: Centralized. One agent per cluster/datacenter reads all hosts' logs through the single Loki endpoint.

**LLM topology**:
- _Internal_: A dedicated inference server (Ollama or compatible) shared across hosts. Rough sizing: one LLM server per ~100 managed machines.
- _External_: OpenAI, Anthropic, OpenRouter, etc. — routed via LiteLLM.

**Remediation output**: `remediation.yaml` + Slack/webhook alert. Operator applies commands manually via SSH or Ansible.

**Current status**: Works today. Needs a deployment config example (see Gaps section).

---

### Scenario 2 — Cloud (EC2 / EKS / GCP / Azure — Datadog complement)

**Who**: DevOps / platform engineer already running Datadog, CloudWatch, or similar.

**Positioning**: RootMedic does **not** replace Datadog. It adds an AI remediation layer on top of the existing alerting stack, with zero changes to the operator's current setup.

**How it works**:
1. Datadog (or any alertmanager) fires an alert webhook POST to RootMedic's HTTP endpoint.
2. RootMedic normalizes the payload, runs it through the existing pipeline (redact → cache → rule/LLM → recommend).
3. Output: Slack message + `remediation.yaml` with recommended commands for the specific host.
4. Operator copies and runs the commands — same recommend-only contract.

**Fluent Bit note**: Even though the cloud alert source is Datadog's webhook, Fluent Bit may still be installed as a lightweight sidecar on EC2 nodes to give RootMedic richer log context for its LLM prompt. This is optional for MVP.

**LLM topology**:
- _Internal_: Cloud-native inference — AWS Bedrock, GCP Vertex AI, Azure OpenAI. All supported via LiteLLM routing; zero code changes needed.
- _External_: OpenAI, Anthropic, OpenRouter, etc.

**Remediation scope**: Linux-node commands only for MVP (`systemctl`, disk cleanup, `sysctl`). Kubernetes-level primitives (`kubectl`, `helm`) are out of scope for MVP.

**Execution path**: Manual / copy-paste. No SSM Run Command, no Ansible push in MVP. Operator applies commands after reviewing the recommendation.

**Current status**: Blocked on the **webhook receiver** (see Gaps section).

---

### Scenario 3 — Single Node / Home Lab

**Who**: Developer, enthusiast, or small team with one or a few Linux machines.

**Log collection**: Fluent Bit installed locally, forwarding to a local Loki instance (deployed via `docker compose` or `podman-compose`).

**LLM topology**:
- _Internal_: Ollama running on the same machine or another LAN machine.
- _External_: Any API provider via LiteLLM.

**Current status**: Fully handled by `install.sh`. Ships now.

---

## Key Architecture Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Cloud positioning | Complement Datadog (webhook) | No disruption to existing stack; adds AI value on top |
| K8s scope | Linux-node only for MVP | Scope control; same `systemctl`/disk commands work on EC2 nodes |
| Agent topology | Centralized (one per cluster) | Already how Loki aggregation works; simpler to operate |
| Cloud execution | Manual / copy-paste | Stays recommend-only; no IAM/SSH complexity in MVP |
| Log collector | Fluent Bit (replaces Promtail and Alloy) | Lighter binary, native cloud/k8s support, Datadog awareness |
| LLM routing | LiteLLM proxy | Single abstraction covers Ollama, Bedrock, Vertex, Azure OpenAI, OpenAI |
| Business model | Not decided yet | Product completeness first |
| Alert formats (MVP) | Datadog webhook + Generic JSON | Covers the primary cloud target; Generic JSON covers everything else |
| Webhook receiver | Separate lightweight HTTP service | Clean separation from the polling loop |

---

## LLM Location Summary

| Scenario | Internal LLM | External LLM |
|---|---|---|
| Datacenter | Dedicated Ollama/inference server (~1 per 100 nodes); configured as `litellm_base_url` in `/etc/rootmedic/config.yaml` | OpenAI, Anthropic, OpenRouter |
| Cloud | AWS Bedrock, GCP Vertex AI, Azure OpenAI — all via LiteLLM | Same external providers |
| Single node | Ollama on the same machine or LAN host | Same external providers |

All three scenarios use the same `litellm_base_url` / `litellm_model` / `litellm_api_key` config fields. Switching LLM is a one-line config change.

---

## What Is Built vs. What Needs Building

### Already built and working

- Loki pull ingestion (`ingest.py`)
- Log normalization, redaction (`redactor.py`)
- Fingerprinting and known-issue cache (`vector_store.py`, `fingerprint.py`)
- Rule-based planner + LLM fallback (`llm_client.py`)
- Recommend-only remediation engine with dry-run gating (`remediation_engine.py`)
- Slack + generic webhook alerting with dedup/escalation (`alerting.py`, `alert_plugins.py`)
- Tiered archive (`archive.py`)
- Single-node production installer (`install.sh` → systemd service)
- End-to-end demo with Podman stack (`demo.py`)

### Gaps for MVP

#### 1. Webhook receiver service (Scenario 2 blocker)

A separate lightweight HTTP service (`webhook_receiver.py`) that:
- Accepts `POST /webhook` from Datadog monitors or any generic JSON alertmanager
- Normalizes the payload to the internal event format (`host`, `unit`, `message`, `timestamp`, `severity`)
- Feeds through the existing pipeline (redact → cache → rule/LLM → recommend → alert)
- Returns `200 OK` immediately; processing is async

**Datadog webhook field mapping:**

| Datadog field | Internal field | Notes |
|---|---|---|
| `host` | `host` | Direct map |
| `alert_title` | `message` | Primary error description |
| `body` | appended to `message` | Additional context |
| `tags` (e.g. `service:nginx`) | `unit` | Parse `service:` tag value |
| `alert_status` | `severity` | `error` / `warning` |
| `date` | `timestamp` | Unix epoch |

**Generic JSON**: user configures field mapping in `alerts.yml` under `webhook_field_map`.

#### 2. Fluent Bit deployment (replaces Promtail and Alloy)

- `Deployment/fluent-bit.conf` — main config (systemd input → priority filter → Loki output)
- `Deployment/fluent-bit-parsers.conf` — standard parsers
- `Deployment/fluent-bit-deploy.yml` — Ansible playbook for all three scenarios
- `Deployment/docker-compose.yml` updated to use Fluent Bit instead of Promtail
- **Status**: In progress.

#### 3. Datacenter deployment guide

A `deployment-scenarios.md` covering:
- Hub Loki + centralized agent config example
- Fluent Bit on N hosts pointing at one Loki
- Internal LLM server config example
- External LLM failover pattern

---

## Future Scope (Post-MVP)

- **Kubernetes primitives**: `kubectl restart`, `helm rollback`, pod-level remediation for EKS/GKE/AKS
- **AWS SSM Run Command**: Approved remediations applied via SSM (no SSH, audit-logged)
- **Alertmanager / CloudWatch SNS webhook support**: Additional ingest formats beyond Datadog + Generic JSON
- **Multi-LLM failover**: Internal LLM down → fall back to external provider automatically
- **Business model**: Open source + hosted LiteLLM proxy, open core, or SaaS — not yet decided
- **Qdrant-backed vector store**: Replace `known_issues.json` with real embeddings for semantic similarity
- **Web UI**: Operator approval interface for reviewing and applying `remediation.yaml`
- **Multi-tenancy**: Multiple clients, isolated incident archives, per-client config

---

## Configuration Reference (All Scenarios)

Config file: `/etc/rootmedic/config.yaml` (written by `install.sh`, mode 600)

```yaml
# LLM endpoint — point at Ollama, Bedrock, Vertex, Azure OpenAI, or external API
litellm_base_url: "https://litellm.saneax.in"   # or http://internal-gpu-box:11434
litellm_model: "smart"                            # or ollama/llama3.2, bedrock/claude-3-haiku, etc.
litellm_api_key: "sk-..."

# Loki endpoint (Scenario 1 + 3; not required in Scenario 2 webhook mode)
loki_url: "http://localhost:3100/loki/api/v1/query_range"

# Alerting (all scenarios)
slack_webhook_url: ""
dedup_window_minutes: 15
escalation_after_minutes: 30
grafana_base_url: "http://localhost:3000"

# Webhook receiver (Scenario 2)
webhook_receiver_port: 9876
webhook_field_map:                # generic JSON field mapping
  message: "text"
  host: "host"
  unit: "service"
  severity: "level"
```
