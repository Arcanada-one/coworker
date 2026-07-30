"""Best-effort secret redaction for opt-in Layer 2 (corpus) blobs.

When `COWORKER_LOG_CORPUS=1`, the request body (delegated file contents + prompt)
and the model response are persisted as plaintext JSON. This module masks common
secret shapes *before* the payload is hashed and written, so a secret that rode
in with a delegated file does not land on disk in cleartext (Auth Arcana Mandate:
no secrets at rest). It is defence-in-depth, not a guarantee — patterns err toward
over-redaction, and the primary guidance ("don't enable corpus on secret files")
still stands. `COWORKER_NO_REDACT=1` bypasses it for deliberate raw-eval capture.
"""

import copy
import re
import sys
from collections.abc import Callable

from .config import REDACTION_YAML

Pattern = tuple[str, re.Pattern]

# Each entry: (label, compiled regex). An optional named group `keep` is preserved
# verbatim in front of the placeholder (e.g. `Bearer ` / `password = `), so the
# structure survives while only the secret value is masked.
BUILTIN_PATTERNS: list[Pattern] = [
    # OpenAI / DeepSeek / Moonshot / OpenRouter `sk-…` (sk-or-… is a superset)
    ("api-key", re.compile(r"sk-[A-Za-z0-9_\-]{16,}")),
    # Groq `gsk_…`
    ("api-key", re.compile(r"gsk_[A-Za-z0-9]{20,}")),
    # HTTP Authorization bearer token — keep the scheme word, mask the token
    ("bearer", re.compile(r"(?i)(?P<keep>\bBearer\s+)[A-Za-z0-9._\-]{8,}")),
    # AWS access key id
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    # PEM private-key block (any flavour), spanning newlines
    (
        "private-key",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    # Generic `api_key = <value>` / `token: "<value>"` assignments — keep the name
    (
        "assignment",
        re.compile(
            r"(?i)(?P<keep>\b(?:api[_-]?key|token|secret|password)\b\s*[:=]\s*['\"]?)"
            r"(?P<val>[^\s'\"]{6,})"
        ),
    ),
]

PLACEHOLDER = "[REDACTED:{label}]"


def _make_repl(label: str) -> Callable[[re.Match], str]:
    def repl(m: re.Match) -> str:
        keep = m.group("keep") if "keep" in m.re.groupindex else ""
        return f"{keep}{PLACEHOLDER.format(label=label)}"

    return repl


def redact_text(text: str, patterns: list[Pattern] | None = None) -> str:
    """Return `text` with every configured secret shape replaced by a placeholder."""
    if not text:
        return text
    if patterns is None:
        patterns = active_patterns()
    for label, rx in patterns:
        text = rx.sub(_make_repl(label), text)
    return text


def redact_payload(
    user_messages: list[dict],
    response_text: str,
    patterns: list[Pattern] | None = None,
) -> tuple[list[dict], str]:
    """Redact string `content` in each message and the response, without mutating inputs."""
    if patterns is None:
        patterns = active_patterns()
    redacted_msgs = copy.deepcopy(user_messages)
    for msg in redacted_msgs:
        content = msg.get("content")
        if isinstance(content, str):
            msg["content"] = redact_text(content, patterns)
    return redacted_msgs, redact_text(response_text, patterns)


def load_extra_patterns(path=REDACTION_YAML) -> list[Pattern]:
    """Load operator-supplied `{name, pattern}` entries from redaction.yaml.

    Fail-safe: a missing file, unparseable YAML, non-list top level, a
    non-mapping entry, or an uncompilable regex is skipped with a stderr
    warning — the built-in patterns always still apply.
    """
    import pathlib

    p = pathlib.Path(path)
    if not p.exists():
        return []
    try:
        import yaml

        data = yaml.safe_load(p.read_text()) or []
    except Exception as exc:  # noqa: BLE001 — fail-safe, never break logging
        print(f"[coworker] could not read redaction.yaml ({exc}); using built-ins only", file=sys.stderr)
        return []
    if not isinstance(data, list):
        print("[coworker] redaction.yaml must be a list of {name, pattern}; ignoring", file=sys.stderr)
        return []
    out: list[Pattern] = []
    for entry in data:
        if not isinstance(entry, dict) or "pattern" not in entry:
            print(f"[coworker] skipping malformed redaction entry: {entry!r}", file=sys.stderr)
            continue
        name = str(entry.get("name", "custom"))
        try:
            out.append((name, re.compile(entry["pattern"])))
        except re.error as exc:
            print(f"[coworker] skipping bad redaction regex {name!r} ({exc})", file=sys.stderr)
    return out


def active_patterns() -> list[Pattern]:
    """Built-in patterns plus any operator extensions from redaction.yaml."""
    return BUILTIN_PATTERNS + load_extra_patterns()
