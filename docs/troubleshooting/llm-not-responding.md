# Troubleshooting: LLM Not Responding

**Installer error**: `LLM did not respond correctly after 3 attempts`

The installer sends `POST <base_url>/v1/chat/completions` with a short test prompt and expects a JSON response with a `choices` array. This page covers the most common causes by provider type.

---

## Local or LAN Ollama

### 1. Ollama is not running

```bash
# Check if Ollama is running
systemctl status ollama 2>/dev/null || pgrep -a ollama

# Start it
ollama serve &   # or: systemctl start ollama
```

### 2. Model not pulled

```bash
# List available models
ollama list

# Pull the model you configured (e.g. llama3.2)
ollama pull llama3.2
```

### 3. Ollama is not accessible from this machine (LAN mode)

By default, Ollama binds to `127.0.0.1`. To allow LAN access:

```bash
# Edit Ollama service or start with env var
export OLLAMA_HOST=0.0.0.0
ollama serve
```

Or add to the systemd unit:

```bash
systemctl edit ollama --force
```
Add under `[Service]`:
```ini
Environment="OLLAMA_HOST=0.0.0.0:11434"
```
Then:
```bash
systemctl daemon-reload && systemctl restart ollama
```

### 4. Port 11434 is blocked

```bash
# On the machine running Ollama
sudo ufw allow 11434/tcp

# Verify connectivity from the RootMedic machine
curl http://<ollama-ip>:11434/api/tags
```

### 5. Test the connection manually

```bash
BASE_URL="http://localhost:11434"   # or your LAN URL
MODEL="llama3.2"

curl -X POST "${BASE_URL}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ollama" \
  -d "{\"model\":\"${MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"hello\"}],\"max_tokens\":5}"
```

Expected: JSON with `choices[0].message.content`.

---

## External API (OpenAI, Anthropic, OpenRouter, LiteLLM proxy)

### 1. Wrong API key

Check your key is correct and not expired:

```bash
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer ${LITELLM_API_KEY}"
# Should return a list of models, not a 401 error
```

### 2. Wrong base URL

The installer expects an **OpenAI-compatible** `/v1/chat/completions` endpoint. Common base URLs:

| Provider | `litellm_base_url` |
|---|---|
| OpenAI | `https://api.openai.com` |
| OpenRouter | `https://openrouter.ai/api` |
| Anthropic (direct) | Not OpenAI-compat — use a LiteLLM proxy |
| LiteLLM proxy | URL of your proxy |

### 3. Wrong model name

Check the model name matches what the provider accepts. For OpenAI: `gpt-4o`, `gpt-4o-mini`, `gpt-3.5-turbo`. For OpenRouter: `openai/gpt-4o`, `anthropic/claude-3-haiku`.

### 4. Rate limit or quota exceeded

The test sends a minimal 5-token prompt. If you hit a rate limit, wait and re-run.

### 5. Test manually

```bash
curl -X POST "${LITELLM_BASE_URL}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${LITELLM_API_KEY}" \
  -d "{\"model\":\"${LITELLM_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"hello\"}],\"max_tokens\":5}"
```

---

## After fixing

Update `/etc/rootmedic/config.yaml` with the correct values, then restart the agent and re-verify:

```bash
nano /etc/rootmedic/config.yaml
systemctl restart rootmedic
journalctl -u rootmedic -f
```

Or re-run the full installer: `sudo bash /opt/rootmedic/install.sh`
