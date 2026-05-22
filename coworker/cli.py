"""Command-line entry point: argparse + ask/write/debug subcommand handlers."""

import argparse
import json
import os
import pathlib
import sys
import time

from .config import BLOBS_ROOT, load_providers
from .logger import get_cached_tokens, log_call
from .profiles import load_profile
from .providers import make_client, resolve_provider_and_model
from .stats import cmd_stats

# File-type gate (TUNE-0258). Default-deny: only text-doc inputs pass.
# Override via --allow-code flag or COWORKER_ALLOW_CODE=1 env var.
_ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".md", ".markdown", ".txt"})
_EXTENSIONLESS_NAME_ALLOW: frozenset[str] = frozenset({
    "readme", "license", "changelog", "authors",
})
GATE_BLOCKED_EXIT = 6


def _check_file_type(path: pathlib.Path) -> str | None:
    """Return None if path passes the content-type gate, else an error message."""
    ext = path.suffix.lower()
    if ext in _ALLOWED_EXTENSIONS:
        return None
    if ext == "" and path.stem.lower() in _EXTENSIONLESS_NAME_ALLOW:
        return None
    allowed = ", ".join(sorted(_ALLOWED_EXTENSIONS))
    return (
        f"file '{path}' (extension '{ext or '<none>'}') is not in the allowed "
        f"list ({allowed}). Use Claude's Read tool for code analysis, "
        f"or pass --allow-code / COWORKER_ALLOW_CODE=1 to override."
    )


def _resolve_allow_code(args) -> bool:
    """Combine --allow-code CLI flag with COWORKER_ALLOW_CODE=1 env var."""
    if getattr(args, "allow_code", False):
        return True
    return os.environ.get("COWORKER_ALLOW_CODE") == "1"


def _apply_gate(paths: list[str], allow_code: bool) -> tuple[list[str], list[str]]:
    """Run _check_file_type over every path; return (allowed, errors).

    `allow_code` does not change the classification — callers decide how
    to react to non-empty errors (override → WARN; default → ERROR + exit 6).
    """
    allowed: list[str] = []
    errors: list[str] = []
    for p in paths:
        msg = _check_file_type(pathlib.Path(p))
        if msg is None:
            allowed.append(p)
        else:
            errors.append(msg)
    return allowed, errors


def _emit_gate_decision(errors: list[str], allow_code: bool) -> bool:
    """Print gate errors to stderr. Return True if caller should abort with exit 6."""
    if not errors:
        return False
    if allow_code:
        for msg in errors:
            print(f"[coworker] WARNING (override): {msg}", file=sys.stderr)
        return False
    for msg in errors:
        print(f"[coworker] ERROR: {msg}", file=sys.stderr)
    return True


def _build_gate_log_extra(
    gate_errors: list[str],
    allow_code: bool,
    paths: list[str],
) -> dict | None:
    """Return log-record metadata for an override-active call, else None.

    Only emit when the gate actually fired AND override bypassed the block,
    i.e. the call sent non-text bytes to the provider. Keeps the log clean
    for plain text-doc calls.
    """
    if not gate_errors or not allow_code:
        return None
    overridden = [
        p for p in paths if _check_file_type(pathlib.Path(p)) is not None
    ]
    return {
        "coworker.gate_override": True,
        "coworker.gate_overridden_files": overridden,
    }


def build_messages(
    system_prompt: str,
    corpus: str,
    question: str,
    corpus_first: bool = True,
) -> list[dict]:
    """Build OpenAI-compat messages list."""
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    if corpus_first:
        messages.append({"role": "user", "content": f"<corpus>\n{corpus}\n</corpus>"})
        messages.append({"role": "user", "content": question})
    else:
        messages.append({"role": "user", "content": question})
        messages.append({"role": "user", "content": f"<corpus>\n{corpus}\n</corpus>"})
    return messages


def _build_corpus(paths: list[str]) -> str:
    docs = []
    for p in paths:
        path = pathlib.Path(p)
        try:
            content = path.read_text(errors="replace")
        except Exception as e:
            print(f"[coworker] cannot read {p}: {e}", file=sys.stderr)
            sys.exit(2)
        docs.append(f"<file path='{p}'>\n{content}\n</file>")
    if not paths and not sys.stdin.isatty():
        docs.append(f"<stdin>\n{sys.stdin.read()}\n</stdin>")
    return "\n\n".join(docs) if docs else "(no files provided)"


def cmd_ask(args) -> int:
    providers = load_providers()
    profile = load_profile(args.profile)
    prov_name, prov_cfg, model = resolve_provider_and_model(args, providers, profile)
    system_prompt = profile["system_prompt"]
    max_tokens = args.max_tokens or profile.get("default_max_tokens_ask", 16384)

    allow_code = _resolve_allow_code(args)
    paths = args.paths or []
    _, gate_errors = _apply_gate(paths, allow_code)
    if _emit_gate_decision(gate_errors, allow_code):
        return GATE_BLOCKED_EXIT
    corpus = _build_corpus(paths)
    messages = build_messages(system_prompt, corpus, args.question, corpus_first=True)

    client = make_client(prov_cfg)
    t0 = time.monotonic()
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
    )
    latency_ms = (time.monotonic() - t0) * 1000

    log_extra = _build_gate_log_extra(gate_errors, allow_code, paths)

    out = resp.choices[0].message.content or ""
    if not out.strip():
        print("[coworker] empty response - try raising --max-tokens.", file=sys.stderr)
        if not args.no_log:
            log_call(
                resp, prov_name, prov_cfg, model, args.profile, "ask",
                messages[1:], "", latency_ms, args.task_id, system_prompt,
                extra=log_extra,
            )
        return 3
    print(out)

    u = getattr(resp, "usage", None)
    if u:
        cached = get_cached_tokens(u)
        print(
            f"\n[coworker: model={model} prompt={u.prompt_tokens} "
            f"completion={u.completion_tokens} cached={cached}]",
            file=sys.stderr,
        )

    if not args.no_log:
        log_call(
            resp, prov_name, prov_cfg, model, args.profile, "ask",
            messages[1:], out, latency_ms, args.task_id, system_prompt,
            extra=log_extra,
        )
    return 0


def _strip_code_fences(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def cmd_write(args) -> int:
    providers = load_providers()
    profile = load_profile(args.profile)
    prov_name, prov_cfg, model = resolve_provider_and_model(args, providers, profile)
    system_prompt = profile["system_prompt"]
    max_tokens = args.max_tokens or profile.get("default_max_tokens_write", 24000)

    allow_code = _resolve_allow_code(args)
    context_paths = args.context or []
    _, gate_errors = _apply_gate(context_paths, allow_code)
    if _emit_gate_decision(gate_errors, allow_code):
        return GATE_BLOCKED_EXIT

    refs = []
    for p in context_paths:
        path = pathlib.Path(p)
        try:
            refs.append(f"<file path='{p}'>\n{path.read_text(errors='replace')}\n</file>")
        except Exception as e:
            print(f"[coworker] cannot read {p}: {e}", file=sys.stderr)
            return 2
    refs_block = "\n\n".join(refs) if refs else "(no reference files)"

    user_msg_corpus = {"role": "user", "content": f"<reference>\n{refs_block}\n</reference>"}
    user_msg_spec = {
        "role": "user",
        "content": (
            f"Target path: {args.target}\n"
            f"Specification: {args.spec}\n\nOutput ONLY the final file contents."
        ),
    }
    messages = [
        {"role": "system", "content": system_prompt},
        user_msg_corpus,
        user_msg_spec,
    ]

    client = make_client(prov_cfg)
    t0 = time.monotonic()
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
    )
    latency_ms = (time.monotonic() - t0) * 1000

    body = (resp.choices[0].message.content or "").strip()
    if not body:
        print("[coworker] empty response - raise --max-tokens.", file=sys.stderr)
        return 3

    body = _strip_code_fences(body)

    if args.stdout:
        print(body)
    else:
        target = pathlib.Path(args.target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
        print(f"[coworker] wrote {target} ({len(body)} bytes)")

    u = getattr(resp, "usage", None)
    if u:
        print(
            f"[coworker: model={model} prompt={u.prompt_tokens} "
            f"completion={u.completion_tokens}]",
            file=sys.stderr,
        )

    if not args.no_log:
        log_call(
            resp, prov_name, prov_cfg, model, args.profile, "write",
            messages[1:], body, latency_ms, args.task_id, system_prompt,
            extra=_build_gate_log_extra(gate_errors, allow_code, context_paths),
        )
    return 0


def cmd_debug(args) -> int:
    prefix = args.hash
    if len(prefix) < 2:
        print("[coworker] --hash prefix must be at least 2 characters", file=sys.stderr)
        return 1
    blob_dir = BLOBS_ROOT / prefix[:2]
    if not blob_dir.exists():
        print(f"[coworker] no blob dir for prefix '{prefix[:2]}'", file=sys.stderr)
        return 4
    rest_prefix = prefix[2:]
    matches = sorted(blob_dir.glob(f"{rest_prefix}*.json"))
    if not matches:
        print(f"[coworker] no blob found for hash prefix '{prefix}'", file=sys.stderr)
        return 4
    data = json.loads(matches[0].read_text())
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="coworker",
        description="Vendor-neutral CLI to delegate bulk I/O off your reasoning model.",
    )
    sub = ap.add_subparsers(dest="subcommand", required=True)

    p_ask = sub.add_parser("ask", help="Ask a question about a corpus of files.")
    p_ask.add_argument("--provider", default=None, help="Provider override (else profile.recommended_provider).")
    p_ask.add_argument("--model", default=None, help="Override model name.")
    p_ask.add_argument("--profile", default="code", help="Profile name (code, datarim, social, write).")
    p_ask.add_argument("--paths", nargs="*", default=[], metavar="FILE", help="Files to include as corpus.")
    p_ask.add_argument("--question", required=True, help="Question to ask.")
    p_ask.add_argument("--max-tokens", type=int, default=None, dest="max_tokens")
    p_ask.add_argument("--task-id", default=None, dest="task_id")
    p_ask.add_argument("--no-log", action="store_true", dest="no_log")
    p_ask.add_argument(
        "--allow-code",
        action="store_true",
        dest="allow_code",
        help=(
            "Bypass the default text-only file gate (.md/.markdown/.txt only). "
            "Equivalent to COWORKER_ALLOW_CODE=1. Overrides are logged with "
            "coworker.gate_override=true."
        ),
    )

    p_write = sub.add_parser("write", help="Generate a file from a spec + context.")
    p_write.add_argument("--provider", default=None)
    p_write.add_argument("--model", default=None)
    p_write.add_argument("--profile", default="write")
    p_write.add_argument("--spec", required=True, help="What to generate.")
    p_write.add_argument("--context", nargs="*", default=[], metavar="FILE")
    p_write.add_argument("--target", required=True, help="Output path.")
    p_write.add_argument("--max-tokens", type=int, default=None, dest="max_tokens")
    p_write.add_argument("--task-id", default=None, dest="task_id")
    p_write.add_argument("--no-log", action="store_true", dest="no_log")
    p_write.add_argument("--stdout", action="store_true")
    p_write.add_argument(
        "--allow-code",
        action="store_true",
        dest="allow_code",
        help=(
            "Bypass the default text-only file gate on --context paths. "
            "Equivalent to COWORKER_ALLOW_CODE=1. Overrides are logged with "
            "coworker.gate_override=true."
        ),
    )

    p_stats = sub.add_parser("stats", help="Show usage statistics.")
    p_stats.add_argument("--since", default="7d", help="Duration: 7d, 30d, all")
    p_stats.add_argument("--by", default="provider", choices=["provider", "profile", "model", "combined"])
    p_stats.add_argument("--profile", default=None)
    p_stats.add_argument("--provider", default=None)
    p_stats.add_argument("--format", default="text", choices=["text", "json"])

    p_debug = sub.add_parser("debug", help="Inspect a corpus blob by hash prefix.")
    p_debug.add_argument("--hash", required=True, help="Hash prefix (min 2 chars).")

    from .plugins import rtk
    rtk.register(sub)

    return ap


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.subcommand == "ask":
        return cmd_ask(args)
    if args.subcommand == "write":
        return cmd_write(args)
    if args.subcommand == "stats":
        return cmd_stats(args)
    if args.subcommand == "debug":
        return cmd_debug(args)
    if args.subcommand == "rtk":
        from .plugins import rtk
        return rtk.dispatch(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
