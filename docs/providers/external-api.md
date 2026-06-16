# LLM Provider: External API

Use an external API provider when you don't want to run your own model. All providers are accessed via the OpenAI-compatible `/v1/chat/completions` endpoint.

## Supported providers

| Provider | Base URL | Notes |
|---|---|---|
| OpenAI | `https://api.openai.com` | Best quality; paid |
| OpenRouter | `https://openrouter.ai/api` | Aggregates many models; pay-per-token |
| Anthropic (via LiteLLM) | Use a LiteLLM proxy | Direct Anthropic API is not OpenAI-compat |
| AWS Bedrock | Via a LiteLLM proxy | Requires IAM credentials |
| GCP Vertex AI | Via a LiteLLM proxy | Requires GCP credentials |
| Azure OpenAI | Via a LiteLLM proxy | Requires Azure credentials |
| RootMedic hosted LiteLLM | `https://litellm.saneax.in` | Managed proxy; requires API key |

## OpenAI

1. Get an API key: https://platform.openai.com/api-keys
2. Installer values:
   - **Base URL**: `https://api.openai.com`
   - **Model**: `gpt-4o-mini` (cost-effective) or `gpt-4o`
   - **API key**: `sk-...`

Test manually:
```bash
curl https://api.openai.com/v1/chat/completions \
  -H "Authorization: Bearer sk-..." \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hello"}],"max_tokens":5}'
```

## OpenRouter

1. Get an API key: https://openrouter.ai/keys
2. Installer values:
   - **Base URL**: `https://openrouter.ai/api`
   - **Model**: `openai/gpt-4o-mini` or `anthropic/claude-3-haiku`
   - **API key**: `sk-or-...`

## LiteLLM proxy (self-hosted)

If you're running your own LiteLLM proxy that fronts Bedrock, Vertex AI, Azure OpenAI, or other providers:

1. Point `litellm_base_url` at your proxy: `http://my-litellm-proxy:4000`
2. Set `litellm_model` to whatever model alias your proxy defines (e.g., `smart`, `claude`, `gpt4`)
3. Use your proxy's API key in `litellm_api_key`

LiteLLM setup guide: https://docs.litellm.ai/docs/proxy/quick_start

## Updating credentials after install

Edit `/etc/rootmedic/config.yaml`:

```bash
nano /etc/rootmedic/config.yaml
systemctl restart rootmedic
```

Test the new credentials:

```bash
curl -X POST "${LITELLM_BASE_URL}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${LITELLM_API_KEY}" \
  -d "{\"model\":\"${LITELLM_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"hello\"}],\"max_tokens\":5}"
```
