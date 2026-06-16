# LLM Provider: Local Ollama (same machine)

Ollama runs an LLM inference server locally. It's the simplest setup for a single-node deployment with no external API dependency.

## Install Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

This installs `ollama` and registers `ollama.service`. Verify:

```bash
systemctl status ollama
curl http://localhost:11434/api/tags
```

## Choose a model

| Model | RAM required | Quality | Command |
|---|---|---|---|
| `llama3.2` (3B) | ~3 GB | Good for common issues | `ollama pull llama3.2` |
| `llama3.2:1b` | ~1.5 GB | Faster, less accurate | `ollama pull llama3.2:1b` |
| `mistral` | ~5 GB | Strong reasoning | `ollama pull mistral` |
| `phi3` | ~3 GB | Good at structured output | `ollama pull phi3` |

For machines with <4 GB RAM, use `llama3.2:1b`. For machines with a GPU, any 7B+ model works well.

## Pull the model

```bash
ollama pull llama3.2
```

Check it loaded:

```bash
ollama list
```

## Test the model

```bash
curl http://localhost:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ollama" \
  -d '{"model":"llama3.2","messages":[{"role":"user","content":"say hello"}],"max_tokens":10}'
```

## Installer config values

When the installer asks:

- **LLM URL**: `http://localhost:11434`
- **Model name**: `llama3.2` (or whichever you pulled)
- **API key**: `ollama` (any non-empty string)

## Keep Ollama updated

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Re-running the install script updates Ollama in place.
