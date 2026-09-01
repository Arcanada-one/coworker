#!/bin/bash
# rtk-signal-guard — vendored asset of `coworker rtk` plugin.
#
# Stands in front of `rtk hook claude` in ~/.claude/settings.json.
# Reads Claude PreToolUse JSON from stdin. Substring-matches the Bash
# command against the passthrough allowlist (~/.config/coworker/rtk-passthrough.json
# or COWORKER_RTK_PASSTHROUGH_PATH override). On match — emit
# `permissionDecision: allow` and exit (raw stdout reaches the agent). On
# no match — forward stdin to `rtk hook claude` so bulk commands stay
# token-reduced.
#
# Fail-safe contract:
#   * Missing jq               → use embedded default allowlist.
#   * Missing store file       → use embedded default allowlist.
#   * Malformed JSON in store  → stderr WARN + use embedded default allowlist.
#   * Empty command            → forward to rtk (let upstream decide).
#   * rtk binary not on PATH    → stderr WARN once + emit a plain `allow` so
#                                 the agent keeps working un-reduced.
#
# That last case is not hypothetical. Claude Code runs PreToolUse hooks with a
# stripped environment, so `~/.local/bin` (Linux) or Homebrew's bin (macOS) is
# often absent from PATH even though an interactive shell finds `rtk` fine.
# Calling it by bare name then dies with 127 and an empty stdout: the agent sees
# `rtk: command not found` on every bulk command, and token reduction is silently
# off while the hook still reports itself as installed (measured on three hosts,
# 2026-08-31). Resolving the path explicitly, and degrading to `allow` when it
# genuinely cannot be found, keeps the failure loud in stderr and harmless to the
# agent.
#
# The allowlist is substring-matched (not regex), case-sensitive. Default
# patterns mirror coworker/plugins/rtk_passthrough.py DEFAULT_PATTERNS.

set -u

STORE_PATH="${COWORKER_RTK_PASSTHROUGH_PATH:-$HOME/.config/coworker/rtk-passthrough.json}"

# Resolve rtk before anything else. A hook inherits whatever PATH the agent
# runtime hands it, which routinely excludes the user bin directories, so a bare
# `rtk` is not a safe call here. COWORKER_RTK_BIN lets an operator pin it.
RTK_PIN_SET=0
if [ "${COWORKER_RTK_BIN+x}" = x ]; then
    RTK_PIN_SET=1
    RTK_BIN="${COWORKER_RTK_BIN}"
else
    RTK_BIN=""
fi
if [ "$RTK_PIN_SET" -eq 1 ]; then
    if [ -n "$RTK_BIN" ] && [ ! -x "$RTK_BIN" ]; then
        RTK_BIN=""
    fi
else
    for _candidate in \
        "$(command -v rtk 2>/dev/null || true)" \
        "$HOME/.local/bin/rtk" \
        "/opt/homebrew/bin/rtk" \
        "/usr/local/bin/rtk" \
        "$HOME/.cargo/bin/rtk"
    do
        if [ -n "$_candidate" ] && [ -x "$_candidate" ]; then
            RTK_BIN="$_candidate"
            break
        fi
    done
fi

# Forward stdin to rtk, or degrade to a plain allow if rtk cannot be found.
# Never exit non-zero on account of a missing binary: this hook sits in front of
# every Bash call the agent makes, and failing it closed would break the agent
# over a token-saving optimisation that is, by design, optional.
forward_to_rtk() {
    if [ -z "$RTK_BIN" ]; then
        echo "[rtk-signal-guard] WARN: rtk not found on PATH or in the usual bin directories; passing through without token reduction. Set COWORKER_RTK_BIN to pin it." >&2
        printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"rtk unavailable — passthrough without reduction"}}\n'
        return 0
    fi
    printf '%s' "$input" | "$RTK_BIN" hook claude
}

# Embedded defaults — single source of truth in rtk_passthrough.py.
# Keep in sync via the seed_default() call at `coworker rtk enable`.
DEFAULT_PATTERNS='git push
git pull
git fetch
git merge
git status
git remote
git rev-parse
git branch
gh pr
gh issue
gh release
gh api
gh run'

load_patterns() {
    if [ ! -f "$STORE_PATH" ]; then
        printf '%s\n' "$DEFAULT_PATTERNS"
        return
    fi
    if ! command -v jq >/dev/null 2>&1; then
        printf '%s\n' "$DEFAULT_PATTERNS"
        return
    fi
    # `jq -e` returns non-zero on null/false/parse-error → fall back.
    if ! patterns=$(jq -re '.patterns[]?' "$STORE_PATH" 2>/dev/null); then
        echo "[rtk-signal-guard] WARN: ${STORE_PATH} unreadable; using defaults" >&2
        printf '%s\n' "$DEFAULT_PATTERNS"
        return
    fi
    if [ -z "$patterns" ]; then
        printf '%s\n' "$DEFAULT_PATTERNS"
        return
    fi
    printf '%s\n' "$patterns"
}

input=$(cat)

# Resolve command from stdin JSON. Missing jq ⇒ forward (we cannot
# classify safely without parsing input).
if ! command -v jq >/dev/null 2>&1; then
    forward_to_rtk
    exit $?
fi

cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // ""' 2>/dev/null)
if [ -z "$cmd" ]; then
    forward_to_rtk
    exit $?
fi

# Substring scan. Iterate patterns one per line, case-match against the
# command. Patterns are operator-controlled (coworker rtk passthrough add) —
# trusted local data, not untrusted network input.
patterns=$(load_patterns)
match=0
while IFS= read -r pat; do
    [ -z "$pat" ] && continue
    case "$cmd" in
        *"$pat"*) match=1; break ;;
    esac
done <<EOF
$patterns
EOF

if [ "$match" -eq 1 ]; then
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"signal command — rtk passthrough"}}\n'
    exit 0
fi

# Bulk path — forward to rtk for token reduction.
forward_to_rtk
