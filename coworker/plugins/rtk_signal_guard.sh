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
#
# The allowlist is substring-matched (not regex), case-sensitive. Default
# patterns mirror coworker/plugins/rtk_passthrough.py DEFAULT_PATTERNS.

set -u

STORE_PATH="${COWORKER_RTK_PASSTHROUGH_PATH:-$HOME/.config/coworker/rtk-passthrough.json}"

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
    printf '%s' "$input" | rtk hook claude
    exit $?
fi

cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // ""' 2>/dev/null)
if [ -z "$cmd" ]; then
    printf '%s' "$input" | rtk hook claude
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
printf '%s' "$input" | rtk hook claude
