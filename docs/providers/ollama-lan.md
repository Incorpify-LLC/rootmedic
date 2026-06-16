# LLM Provider: LAN Ollama (another machine)

Use this when you have a more powerful machine on your network running Ollama, serving inference for multiple RootMedic instances. This is the recommended setup for home labs and small datacenters.

## On the Ollama machine

### Install Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### Allow LAN access

By default, Ollama only listens on `127.0.0.1`. Change it to accept connections from the network:

**Option A — environment variable (temporary):**
```bash
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

**Option B — systemd override (permanent):**
```bash
systemctl edit ollama --force
```
Add:
```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
```
Then:
```bash
systemctl daemon-reload && systemctl restart ollama
```

### Open the firewall

```bash
# UFW (Debian/Ubuntu)
sudo ufw allow from 192.168.1.0/24 to any port 11434

# firewalld (RHEL/CentOS)
sudo firewall-cmd --add-port=11434/tcp --permanent --zone=trusted
sudo firewall-cmd --reload
```

### Pull a model

```bash
ollama pull llama3.2
```

## On the RootMedic machine

Verify connectivity before running the installer:

```bash
OLLAMA_IP=192.168.1.50   # replace with your Ollama machine's IP
curl http://${OLLAMA_IP}:11434/api/tags
```

## Installer config values

When the installer asks:

- **LLM URL**: `http://192.168.1.50:11434` (your Ollama machine's IP)
- **Model name**: `llama3.2`
- **API key**: `ollama`

## Scaling: one Ollama server per ~100 nodes

For datacenter deployments, a single GPU machine running Ollama (with a 7B–13B model) can comfortably serve dozens of RootMedic agents. Larger models (34B+) can serve 50–100 agents depending on request concurrency.

To route multiple RootMedic agents to the same Ollama server, set the same `litellm_base_url` in each agent's `/etc/rootmedic/config.yaml`.
