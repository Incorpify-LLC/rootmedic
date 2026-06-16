# RootMedic Documentation

RootMedic is an autonomous AI medic for Linux systems. It reads your logs, diagnoses root causes with an LLM, and tells you exactly what to run to fix the problem — without touching anything automatically.

## Quickstart

```bash
curl -fsSL https://raw.githubusercontent.com/Incorpify-LLC/rootmedic/main/install.sh | sudo bash
```

The installer will walk you through every step interactively.

## What the installer does

See [Installation walkthrough](installation.md) for a full step-by-step breakdown of what `install.sh` does and what it installs.

## Configuration

See [Configuration reference](configuration.md) for all settings in `/etc/rootmedic/config.yaml`.

## Troubleshooting

If the installer fails, look up the error you got:

| Symptom | Guide |
|---|---|
| "Loki is not reachable" | [Loki not reachable](troubleshooting/loki-not-reachable.md) |
| "LLM did not respond" | [LLM not responding](troubleshooting/llm-not-responding.md) |
| Fluent Bit not sending logs | [Fluent Bit troubleshooting](troubleshooting/fluent-bit.md) |
| Agent running but no events detected | [No events detected](troubleshooting/no-events-detected.md) |

## LLM provider setup

| Provider | Guide |
|---|---|
| Local Ollama (same machine) | [Ollama local](providers/ollama-local.md) |
| LAN Ollama (another machine) | [Ollama LAN](providers/ollama-lan.md) |
| OpenAI / Anthropic / OpenRouter | [External API](providers/external-api.md) |
