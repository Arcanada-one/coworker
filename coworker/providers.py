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
    model = (
        getattr(args, "model", None)
        or (profile or {}).get("recommended_model")
        or prov_cfg["default_model"]
    )
    return prov_name, prov_cfg, model


def make_client(prov_cfg: dict):
    """Construct an OpenAI client pointed at the provider's base_url."""
    from openai import OpenAI

    api_key = os.environ.get(prov_cfg["env_key"])
    if not api_key:
        print(f"[coworker] env var '{prov_cfg['env_key']}' not set", file=sys.stderr)
        sys.exit(1)
    return OpenAI(api_key=api_key, base_url=prov_cfg["base_url"])


def classify_api_error(exc: Exception) -> str | None:
    """Return 'balance' when exc is a provider balance/credit-exhausted error.

    Primary signal: HTTP 402 (openai.APIStatusError.status_code). Secondary:
    a case-insensitive 'insufficient balance' / 'insufficient credit' /
    'out of credit' / quota token in the error text, for providers that do
    not use 402. Returns None for every other error (caller re-raises or
    maps to a generic API-error exit code — auth/generic errors must not be
    swallowed as a false balance message).
    """
    status = getattr(exc, "status_code", None)
    if status == 402:
        return "balance"
    text = str(getattr(exc, "message", "") or exc).lower()
    for needle in (
        "insufficient balance",
        "insufficient credit",
        "out of credit",
        "insufficient_quota",
        "exceeded your current quota",
    ):
        if needle in text:
            return "balance"
    return None
