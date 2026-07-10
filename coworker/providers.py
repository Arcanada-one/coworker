"""Provider resolution and OpenAI-compatible client factory.

All providers (moonshot, deepseek, groq, openrouter, openai) speak OpenAI
chat-completions API; only base_url and env_key differ.
"""

import os
import sys
import time


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


def resolve_fallback_provider(
    profile: dict | None,
    providers: dict,
    primary_name: str,
) -> tuple[str, dict, str] | None:
    """Return (name, cfg, model) for the profile-declared fallback, else None.

    A profile MAY declare `fallback_provider: <name>` (optionally with
    `fallback_model: <model>`). The fallback is used only for a retryable
    error on the primary (see `classify_retryable_error`). Returns None when:
    no `fallback_provider` key, the declared provider is unknown, or it is
    the same as the primary (a same-provider hop would just fail again).
    """
    if not profile:
        return None
    fb_name = profile.get("fallback_provider")
    if not fb_name or fb_name == primary_name:
        return None
    if fb_name not in providers:
        print(
            f"[coworker] profile declares unknown fallback_provider "
            f"'{fb_name}'; ignoring",
            file=sys.stderr,
        )
        return None
    fb_cfg = providers[fb_name]
    fb_model = profile.get("fallback_model") or fb_cfg["default_model"]
    return fb_name, fb_cfg, fb_model


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


def classify_retryable_error(exc: Exception) -> str | None:
    """Return 'retryable' when exc is a rate-limit (429) or timeout error.

    These are the transient failures a fallback provider can recover from —
    unlike balance (402, see `classify_api_error`) or auth/generic errors,
    which would fail identically on any provider and MUST NOT trigger a hop.

    Primary signal: HTTP 429 (openai.RateLimitError.status_code). Timeouts
    (openai.APITimeoutError / socket / httpx read-timeout) do not carry a
    status_code, so we also match the class name and a case-insensitive
    'timed out' / 'timeout' / 'rate limit' / 'too many requests' token in
    the error text. Balance-shaped errors are excluded so a 402 never
    masquerades as retryable.
    """
    if classify_api_error(exc) == "balance":
        return None
    status = getattr(exc, "status_code", None)
    if status == 429:
        return "retryable"
    cls = type(exc).__name__.lower()
    if "timeout" in cls or "ratelimit" in cls:
        return "retryable"
    text = str(getattr(exc, "message", "") or exc).lower()
    for needle in (
        "timed out",
        "timeout",
        "rate limit",
        "too many requests",
    ):
        if needle in text:
            return "retryable"
    return None


def call_with_fallback(
    prov_name: str,
    prov_cfg: dict,
    model: str,
    profile: dict | None,
    providers: dict,
    create_kwargs: dict,
    *,
    client_factory=make_client,
):
    """Run chat.completions.create on the primary; on a retryable (429/timeout)
    error, hop once to the profile-declared fallback provider.

    Single-flight, at most ONE fallback hop (no unbounded retry loop). Only a
    retryable error triggers the hop AND only when the profile declares a
    valid `fallback_provider`. Balance / auth / generic errors are re-raised
    unchanged for the caller's existing classify_api_error handling — the
    fallback never swallows them.

    Returns (resp, eff_name, eff_cfg, eff_model, latency_ms). `latency_ms`
    times only the successful attempt. On the fallback hop a one-line notice
    is written to stderr so the operator sees which provider actually served.
    """
    client = client_factory(prov_cfg)
    t0 = time.monotonic()
    try:
        resp = client.chat.completions.create(model=model, **create_kwargs)
        return resp, prov_name, prov_cfg, model, (time.monotonic() - t0) * 1000
    except Exception as exc:  # noqa: BLE001 — re-raised unless retryable + fallback
        if classify_retryable_error(exc) != "retryable":
            raise
        fb = resolve_fallback_provider(profile, providers, prov_name)
        if fb is None:
            raise
        fb_name, fb_cfg, fb_model = fb
        print(
            f"[coworker] provider {prov_name} retryable error ({exc}); "
            f"falling back to {fb_name}",
            file=sys.stderr,
        )
        fb_client = client_factory(fb_cfg)
        t1 = time.monotonic()
        resp = fb_client.chat.completions.create(model=fb_model, **create_kwargs)
        return resp, fb_name, fb_cfg, fb_model, (time.monotonic() - t1) * 1000
