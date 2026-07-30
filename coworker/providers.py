"""Provider resolution and OpenAI-compatible client factory.

All providers (moonshot, deepseek, groq, openrouter, openai) speak OpenAI
chat-completions API; only base_url and env_key differ.
"""

import os
import shlex
import subprocess
import sys
import time

_KEY_COMMAND_TIMEOUT_DEFAULT = 10.0


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


def _key_command_timeout() -> float:
    """Timeout (seconds) for a key_command run; COWORKER_KEY_COMMAND_TIMEOUT, else 10s.

    A non-numeric / non-positive override is ignored (falls back to the default) so
    a fat-fingered env var can never make credential resolution hang or crash.
    """
    raw = os.environ.get("COWORKER_KEY_COMMAND_TIMEOUT")
    if raw is None:
        return _KEY_COMMAND_TIMEOUT_DEFAULT
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return _KEY_COMMAND_TIMEOUT_DEFAULT
    return val if val > 0 else _KEY_COMMAND_TIMEOUT_DEFAULT


def resolve_api_key(prov_cfg: dict) -> str:
    """Resolve a provider API key: `key_command` (scoped, call-time) else `env_key`.

    When `key_command` is set on the provider, coworker runs it (shell=False via
    shlex.split, timeout-bounded) and uses its stripped stdout as the key — this
    is the git/docker/aws-`credential_process` pattern that lets the operator wire
    a secret store (`vault kv get …`, `pass`, `op`, …) without coworker depending
    on any of them (Auth Arcana Mandate: scoped credentials, no long-lived env
    secret). A non-zero exit, timeout, or empty stdout fails loud (exit 1) — never
    a silent empty key. When `key_command` is absent, the historical `env_key`
    path is used unchanged. `key_command` wins when both are present.
    """
    key_command = prov_cfg.get("key_command")
    if key_command:
        try:
            proc = subprocess.run(
                shlex.split(key_command),
                capture_output=True,
                text=True,
                timeout=_key_command_timeout(),
            )
        except subprocess.TimeoutExpired:
            print(
                f"[coworker] key_command timed out after "
                f"{_key_command_timeout()}s: {key_command!r}",
                file=sys.stderr,
            )
            sys.exit(1)
        except (OSError, ValueError) as exc:
            print(f"[coworker] key_command failed to run ({exc}): {key_command!r}", file=sys.stderr)
            sys.exit(1)
        if proc.returncode != 0:
            detail = proc.stderr.strip() or f"exit {proc.returncode}"
            print(f"[coworker] key_command failed ({detail}): {key_command!r}", file=sys.stderr)
            sys.exit(1)
        api_key = proc.stdout.strip()
        if not api_key:
            print(f"[coworker] key_command produced no output: {key_command!r}", file=sys.stderr)
            sys.exit(1)
        return api_key

    api_key = os.environ.get(prov_cfg["env_key"])
    if not api_key:
        print(f"[coworker] env var '{prov_cfg['env_key']}' not set", file=sys.stderr)
        sys.exit(1)
    return api_key


def make_client(prov_cfg: dict):
    """Construct an OpenAI client pointed at the provider's base_url."""
    from openai import OpenAI

    return OpenAI(api_key=resolve_api_key(prov_cfg), base_url=prov_cfg["base_url"])


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
