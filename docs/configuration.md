# Configuration Reference

Config file: `/etc/rootmedic/config.yaml` — written by `install.sh`, owned by root, mode 600.

After editing this file, restart the agent:

```bash
systemctl restart rootmedic
```

## Full example

```yaml
# LLM endpoint
litellm_base_url: "http://localhost:11434"    # Ollama, or any OpenAI-compatible URL
litellm_model:    "llama3.2"
litellm_api_key:  "ollama"                    # empty string is OK for unauthenticated Ollama

# Loki (log pull mode — Scenarios 1 and 3)
loki_url: "http://localhost:3100/loki/api/v1/query_range"

# Alerting
slack_webhook_url:        ""      # Leave blank to disable Slack alerts
dedup_window_minutes:     15      # Suppress duplicate alerts within this window
escalation_after_minutes: 30      # Re-alert if issue persists longer than this
grafana_base_url:         "http://localhost:3000"

# Webhook receiver (Scenario 2 — Datadog / cloud complement)
webhook_receiver_port: 9876
webhook_field_map:          # Map generic JSON alert fields to internal event fields
  message:  "text"
  host:     "host"
  unit:     "service"
  severity: "level"
```

## Field reference

### LLM settings

| Field | Description | Example |
|---|---|---|
| `litellm_base_url` | Base URL of the LLM API (OpenAI-compatible). No trailing slash. | `https://api.openai.com` |
| `litellm_model` | Model identifier passed to the API. | `gpt-4o-mini`, `llama3.2`, `claude-3-haiku-20240307` |
| `litellm_api_key` | API key. Use `ollama` or any non-empty string for unauthenticated Ollama. | `sk-...` |

**LLM provider base URLs:**

| Provider | `litellm_base_url` |
|---|---|
| Local Ollama | `http://localhost:11434` |
| OpenAI | `https://api.openai.com` |
| OpenRouter | `https://openrouter.ai/api` |
| Anthropic (via LiteLLM) | `https://api.anthropic.com` |
| AWS Bedrock (via LiteLLM) | Depends on LiteLLM proxy config |
| RootMedic hosted | `https://litellm.saneax.in` |

### Loki settings

| Field | Description |
|---|---|
| `loki_url` | Full Loki query URL including path. Used by `fetch_normalize_logs.py`. |

### Alerting settings

| Field | Default | Description |
|---|---|---|
| `slack_webhook_url` | `""` | Slack incoming webhook. Leave blank to disable. |
| `dedup_window_minutes` | `15` | Suppress re-alerts for the same fingerprint within this window. |
| `escalation_after_minutes` | `30` | Send a follow-up alert if the issue is still unseen after this many minutes. |
| `grafana_base_url` | `http://localhost:3000` | Included in alert messages as a link to the dashboard. |

Alerts can also be configured via environment variables: `SLACK_WEBHOOK_URL`, `ALERT_WEBHOOK_URL`.

### Webhook receiver settings (cloud mode)

| Field | Default | Description |
|---|---|---|
| `webhook_receiver_port` | `9876` | Port the webhook HTTP service listens on. |
| `webhook_field_map` | See example | Maps incoming JSON fields to internal event fields. |

**Datadog webhook**: no `webhook_field_map` needed — the receiver auto-detects Datadog payloads by presence of the `alert_title` field.
