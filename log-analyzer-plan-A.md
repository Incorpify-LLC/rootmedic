# AI-Driven Log Analyzer. RCA and Remediation System

This document outlines the architecture and implementation plan for an agent-based system that collects logs and system metrics, analyzes issues via AI, and performs remediation actions with learning feedback loops.

---

## Architecture Overview

```text
      +---------------------+
      | journalctl warnings |
      | & errors (Alloy)    |
      +----------+----------+
                 |
                 v
    +------------+-------------+
    |  Log Analyzer Agent      |
    | (parses, filters context)|
    +------------+-------------+
                 |
 +---------------+-------------------+
 |                                   |
 v                                   v
 +------------+                    +-----------------+
 | Vector DB  | <--- retrieval --> |  OpenAI / LLM    |
 | (Qdrant)   |                    |  fallback (RAG)  |
 +------------+                    +-----------------+
        |                                 |
        |        +------------------------+
        |        |
        v        v
+--------------------------+           +---------------------------+
| Bash/Python Remediator   |           | Human-in-the-loop (Audit) |
| (restart, clean, revert) |           +---------------------------+
+--------------------------+
```

---

## ✅ Agent Workflow

### Phase 1: Log Collection and Normalization
- Use **Alloy** to collect `journalctl` logs (errors and warnings)
- Store logs in **Loki**
- Normalize logs into timestamp, host, unit, and message

### Phase 2: Vector DB Setup (Qdrant)
- Generate embeddings from normalized logs
- Store log + fix in Qdrant for future retrieval

### Phase 3: RCA + Remediation Decision
- Query Qdrant for known matches
- If low similarity, query OpenAI with context
- Receive recommended fix (script or command)

### Phase 4: Action & Remediator
- Execute safe actions (restart service, cleanup disk, etc.)
- Log all changes and prepare rollback

### Phase 5: Learning & Feedback
- Log successful RCA and fix in vector DB
- Improve future match rate and fix quality

### Phase 6: CLI or Web UI (optional)
- User interface to query RCA history, execute audits, and view actions

---

## Observability Stack Design

| Component       | Tool     | Purpose                                       |
|----------------|----------|-----------------------------------------------|
| Log Shipper     | Alloy    | Collects structured system logs               |
| Log Storage     | Loki     | Retains and indexes logs                      |
| Metric Agent    | Alloy    | Collects CPU/RAM/Disk/Net metrics             |
| Vector DB       | Qdrant   | Stores embedded logs and remediation matches  |
| AI Backend      | OpenAI   | Fallback LLM reasoning for root cause         |

---

## Why Alloy over Telegraf or Promtail?

| Feature              | Alloy           | Promtail / Telegraf |
|----------------------|------------------|----------------------|
| Logs collection      | ✅ Unified        | ✅ Separate tools     |
| System metrics       | ✅ Built-in       | ❌ Promtail only logs |
| Label enrichment     | ✅ Yes            | ❌ Basic              |
| Future support       | ✅ Promtail merge | ❌ Being phased out   |

**Use Alloy** as the single agent for both **logs** and **metrics**.

---

## Example Alloy Config Snippet which captures system metrics as well.

```yaml
integrations:
  node_exporter:
    enabled: true

logs:
  receivers:
    journal:
      type: journal
      include_priorities: ["err", "warning"]
  exporters:
    loki:
      endpoint: http://loki:3100/loki/api/v1/push
      labels:
        job: system-logs
        host: ${HOSTNAME}

metrics:
  configs:
    - name: node
      scrape_integrations: [node_exporter]
      remote_write:
        - url: http://prometheus:9090/api/v1/write
```

---

## RCA Intelligence Layer

- Normalize logs → create embeddings
- Store known errors and fixes in **Qdrant**
- For novel logs → query **OpenAI**
- Return remediation suggestion or action
- Store success back in Qdrant

---

## Roadmap

1. Loki + Alloy setup
2. Vector DB (Qdrant)
3. Python log parser + embedder
4. RCA fallback via OpenAI
5. CLI agent & Remediator script
6. UI & Audit dashboard

---

## License

- Alloy: Apache 2.0
- Qdrant: Apache 2.0
- OpenAI: API-licensed
