"""Provider resolution and OpenAI-compatible client factory.

All providers (moonshot, deepseek, groq, openrouter, openai) speak OpenAI
chat-completions API; only base_url and env_key differ.
"""

import os
import sys


def resolve_provider_and_model(
    args,
    providers: dict,
    profile: dict | None = None,
) -> tuple[str, dict, str]:
    """Resolution chain: --provider flag -> profile.recommended_provider -> env -> 'moonshot'."""
    prov_name = (
        args.provider
        or (profile or {}).get("recommended_provider")
        or os.environ.get("COWORKER_DEFAULT_PROVIDER")
        or "moonshot"
    )
    if prov_name not in providers:
        print(f"[coworker] unknown provider '{prov_name}'", file=sys.stderr)
        sys.exit(1)
    prov_cfg = providers[prov_name]
    model = getattr(args, "model", None) or prov_cfg["default_model"]
    return prov_name, prov_cfg, model


def make_client(prov_cfg: dict):
    """Construct an OpenAI client pointed at the provider's base_url."""
    from openai import OpenAI

    api_key = os.environ.get(prov_cfg["env_key"])
    if not api_key:
        print(f"[coworker] env var '{prov_cfg['env_key']}' not set", file=sys.stderr)
        sys.exit(1)
    return OpenAI(api_key=api_key, base_url=prov_cfg["base_url"])
