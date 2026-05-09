"""Two-tier logger: JSONL metadata always, sha256 corpus blob only when COWORKER_LOG_CORPUS=1."""

import datetime
import hashlib
import json
import os
import pathlib
import sys
import tempfile
from typing import Any

from .config import BLOBS_ROOT, LOG_DIR
from .pricing import calc_cost


def _system_hash(system_prompt: str) -> str:
    return "sha256:" + hashlib.sha256(system_prompt.encode()).hexdigest()[:16]


def build_corpus_payload(
    user_messages: list[dict],
    response_text: str,
) -> tuple[dict, str]:
    """Serialize corpus+response and compute deterministic sha256 hash."""
    payload = {
        "user_messages": user_messages,
        "response_text": response_text,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    blob_hash = hashlib.sha256(serialized.encode()).hexdigest()
    return payload, blob_hash


def write_blob(payload: dict, blob_hash: str, blobs_root: pathlib.Path = BLOBS_ROOT) -> None:
    """Atomically write payload JSON to blobs_root/<hash[:2]>/<hash[2:]>.json."""
    target_dir = blobs_root / blob_hash[:2]
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / (blob_hash[2:] + ".json")
    if target.exists():
        return
    with tempfile.NamedTemporaryFile(
        mode="w", dir=target_dir, delete=False, suffix=".tmp"
    ) as tf:
        json.dump(payload, tf, sort_keys=True, separators=(",", ":"))
        tmp_path = tf.name
    os.rename(tmp_path, target)


def write_jsonl_metadata(record: dict, log_dir: pathlib.Path = LOG_DIR) -> None:
    """Append one JSON record to today's JSONL log (O_APPEND for atomicity)."""
    log_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    log_file = log_dir / f"{date_str}.jsonl"
    line = json.dumps(record, separators=(",", ":")) + "\n"
    fd = os.open(str(log_file), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)


def get_cached_tokens(resp_usage: Any) -> int:
    """Extract cached token count from response usage (provider-dependent field)."""
    if resp_usage is None:
        return 0
    details = getattr(resp_usage, "prompt_tokens_details", None)
    if details is not None:
        val = getattr(details, "cached_tokens", 0)
        return int(val) if isinstance(val, (int, float)) else 0
    val = getattr(resp_usage, "cached_tokens", 0)
    return int(val) if isinstance(val, (int, float)) else 0


def log_call(
    resp: Any,
    provider_name: str,
    provider_cfg: dict,
    model: str,
    profile_name: str,
    subcommand: str,
    user_messages: list[dict],
    response_text: str,
    latency_ms: float,
    task_id: str | None,
    system_prompt: str = "",
    log_dir: pathlib.Path = LOG_DIR,
    blobs_root: pathlib.Path = BLOBS_ROOT,
) -> None:
    """Two-tier log: JSONL metadata always; blob only if COWORKER_LOG_CORPUS=1."""
    if os.environ.get("COWORKER_NO_LOG") == "1":
        return
    try:
        usage = getattr(resp, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        cached_tokens = get_cached_tokens(usage)
        finish_reason = str(
            resp.choices[0].finish_reason if resp.choices else "unknown"
        )
        cost = calc_cost(provider_cfg, model, input_tokens, output_tokens, cached_tokens)

        record: dict = {
            "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "gen_ai.system": provider_name,
            "gen_ai.request.model": model,
            "coworker.profile": profile_name,
            "gen_ai.usage.input_tokens": input_tokens,
            "gen_ai.usage.output_tokens": output_tokens,
            "gen_ai.usage.cached_tokens": cached_tokens,
            "coworker.cost_usd": cost,
            "latency_ms": latency_ms,
            "gen_ai.response.finish_reason": finish_reason,
            "coworker.system_hash": _system_hash(system_prompt) if system_prompt else "",
            "coworker.exit_code": 0,
            "coworker.task_id": task_id,
            "coworker.subcommand": subcommand,
        }

        if os.environ.get("COWORKER_LOG_CORPUS") == "1":
            payload, blob_hash = build_corpus_payload(user_messages, response_text)
            write_blob(payload, blob_hash, blobs_root=blobs_root)
            record["coworker.corpus_hash"] = blob_hash

        write_jsonl_metadata(record, log_dir=log_dir)
    except Exception as e:
        print(f"[coworker] logger error (non-fatal): {e}", file=sys.stderr)
