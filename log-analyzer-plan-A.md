# AI-Driven Log Analyzer. RCA and Remediation System

This document outlines a secure, scalable design for per-client agent-based system log analysis, AI-driven RCA, plugin-based alerting, and long-term RCA archival with strong security practices.

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

## Key Principles

- RCA (Root Cause Analysis) is the **core outcome**, not auto-remediation
- Remediation is **recommended only** — never run without user approval (Rollback is also provided)
- Alerts are delivered via **plugin-based channels**
- All core services are **containerized** and optionally **Kubernetes-deployable**
- Designed for **per-client deployments** (no shared SaaS)
- Log, RCA, and rollback records are archived with **tiered retention**
- All data transfers and storage are **secured with encryption**

---

## Secure Design & Encryption

| Concern                 | Practice                                   |
|-------------------------|--------------------------------------------|
| Logs in transit         | 🔐 TLS/mTLS for Alloy → Loki, RCA → Qdrant |
| Secrets in logs         | ✂️ Redacted via regex or LLM sanitizer     |
| AuthN/AuthZ             | 🔐 Certs or JWT tokens per agent           |
| Data at rest            | 🔐 Encrypted volumes + object storage      |
| Config secrets          | 🔐 Stored via Vault/sops/SealedSecrets     |
| API access              | 🔐 Scoped keys, periodic rotation          |

---

### TLS & mTLS Deployment Model

```text
+-----------------------+
|    Client Node        |
|  [Alloy Agent]        |
|  TLS → Log Gateway    |
+----------+------------+
           |
           v (TLS/mTLS)
+-----------------------+
|  Log Aggregator (Loki)|
|  RCA + Qdrant         |
|  Secret Manager (Vault|
+-----------------------+
           |
     +-----+-----+
     |   Alerting  |
     |  (via plugins |
     +-------------+
           |
           v (HTTPS)
     Slack, Webhook, Email
```

---

## Where Each Component Runs

```text

| Component                       | Host / Mode                         | Notes                                        |
|---=-----------------------------|-------------------------------------|--------=-------------------------------------|
| Alloy Agent                     | Every node (DC or cloud)            | Logs + system metrics + BMC/IPMI optional    |
| RCA Stack (Loki, Qdrant, Agent) | Central VM or K8s deployment        | Scales horizontally, supports failover       |
| Remediator Output               | YAML or Web Portal                  | Requires human approval                      |
| OpenAI LLM                      | Cloud (configurable)                | Optional fallback; can be replaced by Ollama |
| Long-Term Archive               | Object Storage (S3, MinIO, etc.)    | Tiered retention: 30d / 6mo / 12mo           |

```
---

## Plugin-Based Alerting System

Each plugin receives:
- Incident summary
- RCA details
- Suggested fix
- Optional link to full history

Implementation details will be followed
---

## RCA + Agent Workflow
1. Normalize logs from Alloy
2. Embed via OpenAI or local model
3. Search Qdrant for similar issues
4. If no match, fallback to OpenAI
5. Save RCA result and suggestion
6. Generate `remediation.yaml` (not auto-executed)

---

## Retention & Audit Trail

- Logs + RCA + rollback saved in structured format
- Archived to object storage:
  ```
  /client-id/yyyy-mm/incident-UUID/
  ```
- Configurable tiers:
  - Free: 30 days
  - Pro: 6 months
  - Enterprise: 12 months

---

## Deployment Flexibility

| Mode               | Tools                         | Usage                          |
|--------------------|-------------------------------|--------------------------------|
| Simple install     | Docker Compose                | Default for most users         |
| Scalable install   | Kubernetes + Helm chart       | For large clients              |
| Failover-ready     | K8s StatefulSets, Services    | Elastic, HA possible           |
| Air-gapped option  | Replace OpenAI with Ollama    | Fully offline RCA capability   |

All services (Qdrant, Loki, RCA, Alerting) are containerized.

---

## Metric Sources

| Source        | Method                           |
|---------------|----------------------------------|
| CPU, RAM, Disk| Alloy + node_exporter            |
| BMC/IPMI      | Redfish, `ipmitool`, sensors     |
| NIC           | ethtool, /sys/class/net          |
| Thermal       | lm-sensors, `/sys/class/thermal` |
| RAID/Disks    | smartctl, nvme-cli               |

---

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
|-----------------|----------|-----------------------------------------------|
| Log Shipper     | Alloy    | Collects structured system logs               |
| Log Storage     | Loki     | Retains and indexes logs                      |
| Metric Agent    | Alloy    | Collects CPU/RAM/Disk/Net metrics             |
| Vector DB       | Qdrant   | Stores embedded logs and remediation matches  |
| AI Backend      | OpenAI   | Fallback LLM reasoning for root cause         |

---

## Why Alloy over Telegraf or Promtail?

| Feature              | Alloy             | Promtail / Telegraf   |
|----------------------|-------------------|-----------------------|
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

1. ✅ Basic agent + Loki log shipper
2. ✅ Qdrant + embedding setup
3. ✅ RCA agent with fallback logic
4. ✅ Remediator YAML generator
5. ✅ Plugin alerting system
6. ✅ Object storage archiver
7. ✅ TLS/mTLS configuration and redactor
8. ⏳ Optional dashboard
9. ⏳ Helm/K8s packaging

---

## License

## License

- Alloy: Apache 2.0  
- Loki: AGPL  
- Qdrant: Apache 2.0  
- Custom Code: Apache/MIT recommended  

