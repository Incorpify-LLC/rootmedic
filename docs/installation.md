# Installation Walkthrough

The installer (`install.sh`) is an interactive Bash script that configures RootMedic end-to-end. Run it as root.

```bash
curl -fsSL https://raw.githubusercontent.com/Incorpify-LLC/rootmedic/main/scripts/install.sh | sudo bash
```

## What it installs and where

| Item | Path |
|---|---|
| RootMedic source | `/opt/rootmedic/` |
| Python virtualenv | `/opt/rootmedic/.venv/` |
| Config file | `/etc/rootmedic/config.yaml` (mode 600) |
| CLI shim | `/usr/local/bin/rootmedic` |
| Systemd unit | `/etc/systemd/system/rootmedic.service` |
| Fluent Bit config | `/etc/fluent-bit/fluent-bit.conf` |
| Logs | `journalctl -u rootmedic` |

## Step-by-step flow

### 1. OS dependencies

Installs: `python3`, `python3-venv`, `python3-pip`, `git`, `curl`, `jq`, `ca-certificates`.

Supported package managers: `apt` (Debian/Ubuntu), `dnf` (RHEL/CentOS), `pacman` (Arch), `apk` (Alpine), `zypper` (openSUSE).

**Fails if**: no supported package manager is found.

---

### 2. Clone or update the repository

Clones `https://github.com/Incorpify-LLC/rootmedic.git` into `/opt/rootmedic`. If the directory already exists, it does a `git fetch + reset --hard` to update to the latest.

**Fails if**: no network access, or the GitHub repository is unreachable.

---

### 3. Python virtualenv

Creates `.venv` inside the install directory and installs `requirements.txt` (`requests`, `PyYAML`, `pytest`).

---

### 4. Loki configuration (interactive)

The installer asks for the Loki URL (default: `http://localhost:3100`) and sends a request to `/ready`.

- **If Loki is up**: continues.
- **If Loki is down**: asks whether to start the bundled Docker/Podman Compose stack (`Deployment/docker-compose.yml`), which includes Loki, Fluent Bit, and Grafana. If you decline, you can continue without Loki (the agent will fail to ingest logs at runtime).

See [Loki not reachable](troubleshooting/loki-not-reachable.md) if this step fails.

---

### 5. Fluent Bit installation

Adds the official Fluent Bit package repository and installs `fluent-bit`. Writes `/etc/fluent-bit/fluent-bit.conf` configured to:
- Read from the systemd journal
- Keep only error (priority 3) and warning (priority 4) entries
- Forward to the Loki URL confirmed in step 4

Enables and starts `fluent-bit.service`.

Supported distros: Debian/Ubuntu (apt), RHEL/CentOS (dnf), Alpine (apk). Other distros prompt you to install Fluent Bit manually.

See [Fluent Bit troubleshooting](troubleshooting/fluent-bit.md) if this step fails.

---

### 6. LLM configuration (interactive)

Presents a menu:

1. **Local Ollama** — same machine, port 11434. Checks that Ollama is running and that the chosen model is available; offers to `ollama pull` the model if missing.
2. **LAN Ollama** — Ollama on another machine. Asks for the URL.
3. **External API** — OpenAI, Anthropic, OpenRouter, a self-hosted LiteLLM proxy, etc. Asks for base URL, model name, and API key.
4. **Hosted RootMedic LiteLLM** — `https://litellm.saneax.in`. Asks for your API key.

After collecting credentials, the installer sends a test prompt (`"Reply with the single word: hello"`) to `/v1/chat/completions` and verifies that a valid response comes back. It retries up to 3 times before warning you.

See [LLM not responding](troubleshooting/llm-not-responding.md) if this step fails.

---

### 7. Slack webhook (optional)

Asks whether to configure a Slack incoming webhook for alert notifications. Leave blank to skip.

---

### 8. Write config

Writes `/etc/rootmedic/config.yaml` (mode 600) with all collected values. The file is owned by root.

---

### 9. CLI shim + systemd unit

- Installs `/usr/local/bin/rootmedic` — a thin wrapper that activates the venv and runs `fetch_normalize_logs.py`.
- Registers and starts `rootmedic.service` (systemd). The service runs on boot, restarts on failure with a 30-second delay.

---

### 10. Final verification

Re-runs the Loki and LLM connectivity checks and confirms that `rootmedic.service` is active. Prints a summary with pass/warn counts.

---

## Non-interactive / CI mode

Set `ROOTMEDIC_NON_INTERACTIVE=1` and provide all values as environment variables:

```bash
sudo ROOTMEDIC_NON_INTERACTIVE=1 \
     LOKI_URL=http://loki:3100 \
     LLM_TYPE=external \
     LITELLM_BASE_URL=http://mock-llm:8080 \
     LITELLM_MODEL=mock-model \
     LITELLM_API_KEY=ci-key \
     START_LOKI_IF_DOWN=0 \
     bash /opt/rootmedic/scripts/install.sh
```

All prompts are skipped. Defaults are used for anything not specified.

## Re-running the installer

Safe to re-run. The repo update is idempotent (`git reset --hard`), the venv is recreated, and the config is overwritten. The `fluent-bit.service` and `rootmedic.service` are restarted.

## Uninstalling

```bash
systemctl disable --now rootmedic.service fluent-bit.service
rm -rf /opt/rootmedic /etc/rootmedic /etc/fluent-bit/fluent-bit.conf
rm /usr/local/bin/rootmedic /etc/systemd/system/rootmedic.service
systemctl daemon-reload
```
