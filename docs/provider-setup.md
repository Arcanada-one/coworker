# Provider setup

Each provider needs (1) an API key in an environment variable and (2) a section in `providers.yaml`. The shipped `examples/providers.yaml.example` already covers all five — copy it and fill keys via env vars.

## Resolution chain

When you run `coworker ask` (or `write`), the provider is chosen in this order:

1. `--provider <name>` CLI flag, if given.
2. `profile.recommended_provider` from `profiles.yaml`.
3. `$COWORKER_DEFAULT_PROVIDER` env var.
4. Built-in fallback: `moonshot`.

If the resolved name is not a key in `providers.yaml`, `coworker` prints `unknown provider 'X'` and exits with code 1.

## Per-provider notes

### Moonshot

- **API key:** https://platform.moonshot.ai/console/api-keys (set `MOONSHOT_API_KEY`).
- **Default model:** `kimi-k2.6`. Long context, strong on reading-comprehension. Pricing in `providers.yaml.example` matches public pricing as of 2026-05-08.
- **Prefix cache:** automatic for shared system prompts. The `cache_input` price applies to cached tokens.

### DeepSeek

- **API key:** https://platform.deepseek.com (set `DEEPSEEK_API_KEY`).
- **Default model:** `deepseek-chat`. Cheapest mainstream provider; good for `code` and `social` profiles.
- **Prefix cache:** automatic. `cache_input` discount roughly 10× over uncached input.
- For reasoning-heavy work, override with `--model deepseek-reasoner`.

### Groq

- **API key:** https://console.groq.com/keys (set `GROQ_API_KEY`).
- **Default model:** `llama-3.3-70b-versatile`. Fast — useful when latency matters more than cost.
- **Prefix cache:** not currently supported.

### OpenRouter

- **API key:** https://openrouter.ai/keys (set `OPENROUTER_API_KEY`).
- **Default model:** `deepseek/deepseek-chat-v3.5` (configurable per request — OpenRouter exposes hundreds of models).
- **Pricing:** OpenRouter applies its own markup; the example file leaves `pricing: null` (cost tracking disabled). Set explicit per-model prices if you need accurate stats.

### OpenAI

- **API key:** https://platform.openai.com/api-keys (set `OPENAI_API_KEY`).
- **Default model:** `gpt-5-mini`. Useful when you specifically need OpenAI's quality/feature set; usually the most expensive option in this list.
- **Prefix cache:** automatic on supported models.

## Custom providers

Anything that exposes an OpenAI-compatible `chat/completions` endpoint can be added as a new section in `providers.yaml`:

```yaml
my-local-vllm:
  base_url: http://localhost:8000/v1
  env_key: VLLM_API_KEY     # required even if your server ignores auth — set to any non-empty value
  default_model: meta-llama/Llama-3.3-70B-Instruct
  pricing: null
  prefix_cache: false
```

Then call: `coworker ask --provider my-local-vllm --question "..."`.

## Testing connectivity

The smallest reasonable smoke probe (will charge you for ~5 input tokens):

```bash
coworker ask --provider deepseek \
             --question "Say only the word 'pong' and nothing else." \
             --no-log
```

If the response is `pong`, your key + base_url are working. Watch stderr for the `[coworker: model=… prompt=… completion=… cached=…]` line — confirms tokens were billed.
