#!/bin/bash
# rtk-signal-guard — vendored asset of `coworker rtk` plugin.
#
# Stands in front of `rtk hook claude` in ~/.claude/settings.json.
# Reads Claude PreToolUse JSON from stdin. Structurally matches Bash simple
# commands against the passthrough allowlist (~/.config/coworker/rtk-passthrough.json
# or COWORKER_RTK_PASSTHROUGH_PATH override). On match — emit
# `permissionDecision: allow` and exit (raw stdout reaches the agent). On
# no match — forward stdin to `rtk hook claude` so bulk commands stay
# token-reduced.
#
# Fail-safe contract:
#   * Missing jq               → forward to rtk (cannot parse hook JSON safely).
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
# The allowlist is prefix-matched on shell words (not regex), case-sensitive.
# Recognised git/gh global options are removed before matching so the true
# subcommand stays visible. Default patterns mirror
# coworker/plugins/rtk_passthrough.py DEFAULT_PATTERNS.

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
    if ! patterns=$(jq -re '.patterns[]? | strings | select(test("[[:cntrl:]]") | not)' "$STORE_PATH" 2>/dev/null); then
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

# Split the first simple command into shell words without executing it. This is
# intentionally a small lexer, not `eval`: quotes and backslash escaping affect
# word boundaries, while substitutions remain inert text. A malformed command
# fails closed to the RTK path.
split_first_command() {
    _split_src=$1
    SPLIT_WORDS=()
    SPLIT_REST=""
    _split_word=""
    _split_state=plain
    _split_started=0
    _split_i=0
    _split_len=${#_split_src}

    while [ "$_split_i" -lt "$_split_len" ]; do
        _split_ch=${_split_src:$_split_i:1}
        case "$_split_state" in
            plain)
                case "$_split_ch" in
                    " "|$'\t'|$'\r'|$'\n')
                        if [ "$_split_started" -eq 1 ]; then
                            SPLIT_WORDS+=("$_split_word")
                            _split_word=""
                            _split_started=0
                        fi
                        ;;
                    "'" ) _split_state=single; _split_started=1 ;;
                    '"' ) _split_state=double; _split_started=1 ;;
                    "\\")
                        _split_i=$((_split_i + 1))
                        [ "$_split_i" -lt "$_split_len" ] || return 1
                        _split_word="${_split_word}${_split_src:$_split_i:1}"
                        _split_started=1
                        ;;
                    ";"|"|"|"&")
                        if [ "$_split_started" -eq 1 ]; then
                            SPLIT_WORDS+=("$_split_word")
                            _split_started=0
                        fi
                        _split_next=${_split_src:$((_split_i + 1)):1}
                        _split_skip=1
                        case "${_split_ch}${_split_next}" in
                            "&&"|"||"|"|&"|";;") _split_skip=2 ;;
                        esac
                        SPLIT_REST=${_split_src:$((_split_i + _split_skip))}
                        break
                        ;;
                    "#")
                        if [ "$_split_started" -eq 0 ]; then
                            break
                        fi
                        _split_word="${_split_word}${_split_ch}"
                        ;;
                    '`') return 1 ;;
                    '$')
                        _split_next=${_split_src:$((_split_i + 1)):1}
                        [ "$_split_next" = "(" ] && return 1
                        _split_word="${_split_word}${_split_ch}"
                        _split_started=1
                        ;;
                    *)
                        _split_word="${_split_word}${_split_ch}"
                        _split_started=1
                        ;;
                esac
                ;;
            single)
                if [ "$_split_ch" = "'" ]; then
                    _split_state=plain
                else
                    _split_word="${_split_word}${_split_ch}"
                fi
                ;;
            double)
                case "$_split_ch" in
                    '"') _split_state=plain ;;
                    "\\")
                        _split_i=$((_split_i + 1))
                        [ "$_split_i" -lt "$_split_len" ] || return 1
                        _split_word="${_split_word}${_split_src:$_split_i:1}"
                        ;;
                    *) _split_word="${_split_word}${_split_ch}" ;;
                esac
                ;;
        esac
        _split_i=$((_split_i + 1))
    done

    [ "$_split_state" = plain ] || return 1
    if [ "$_split_started" -eq 1 ]; then
        SPLIT_WORDS+=("$_split_word")
    fi
    [ "${#SPLIT_WORDS[@]}" -gt 0 ]
}

# Set NORMALIZED_WORDS to the executable, true subcommand, and remaining argv.
# Only documented global options with valid non-empty values are skipped;
# unknown or malformed options fail closed rather than guessing.
normalize_invocation() {
    [ "$#" -gt 0 ] || return 1
    _normal_tool=$1
    shift
    case "$_normal_tool" in
        git|*/git)
            _normal_tool=git
            while [ "$#" -gt 0 ]; do
                case "$1" in
                    -C|--git-dir|--work-tree|--namespace|--super-prefix|--attr-source)
                        [ "$#" -ge 2 ] && [ -n "$2" ] || return 1
                        shift 2
                        ;;
                    -c)
                        [ "$#" -ge 2 ] || return 1
                        case "$2" in ?*=*) ;; *) return 1 ;; esac
                        shift 2
                        ;;
                    --config-env)
                        [ "$#" -ge 2 ] || return 1
                        case "$2" in ?*=?*) ;; *) return 1 ;; esac
                        shift 2
                        ;;
                    -C?*|-c?*=*|--git-dir=?*|--work-tree=?*|--namespace=?*|--super-prefix=?*|--attr-source=?*)
                        shift
                        ;;
                    --config-env=?*=?*)
                        shift
                        ;;
                    -c?*|--git-dir=*|--work-tree=*|--namespace=*|--super-prefix=*|--config-env=*|--attr-source=*) return 1 ;;
                    -p|-P|--paginate|--no-pager|--no-replace-objects|--bare|--literal-pathspecs|--glob-pathspecs|--noglob-pathspecs|--icase-pathspecs|--no-optional-locks|--no-lazy-fetch)
                        shift
                        ;;
                    --) shift; break ;;
                    -*) return 1 ;;
                    *) break ;;
                esac
            done
            ;;
        gh|*/gh)
            _normal_tool=gh
            while [ "$#" -gt 0 ]; do
                case "$1" in
                    --repo|-R|--hostname)
                        [ "$#" -ge 2 ] && [ -n "$2" ] || return 1
                        shift 2
                        ;;
                    --repo=?*|-R?*|--hostname=?*) shift ;;
                    --repo=*|--hostname=*) return 1 ;;
                    --) shift; break ;;
                    -*) return 1 ;;
                    *) break ;;
                esac
            done
            ;;
    esac

    [ "$#" -gt 0 ] || return 1
    NORMALIZED_WORDS=("$_normal_tool")
    while [ "$#" -gt 0 ]; do
        NORMALIZED_WORDS+=("$1")
        shift
    done
}

pattern_matches_normalized() {
    PATTERN_WORDS=()
    IFS=" " read -r -a PATTERN_WORDS <<< "$1"
    _pattern_len=${#PATTERN_WORDS[@]}
    [ "$_pattern_len" -gt 0 ] || return 1
    [ "$_pattern_len" -le "${#NORMALIZED_WORDS[@]}" ] || return 1
    _pattern_i=0
    while [ "$_pattern_i" -lt "$_pattern_len" ]; do
        [ "${PATTERN_WORDS[$_pattern_i]}" = "${NORMALIZED_WORDS[$_pattern_i]}" ] || return 1
        _pattern_i=$((_pattern_i + 1))
    done
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

# Structural prefix scan. Patterns are compared literally word by word, so
# glob characters or command-substitution text in operator-owned
# custom patterns remain data and signal text inside an argument cannot match.
patterns=$(load_patterns)
match=0
remaining_cmd=$cmd
while [ -n "$remaining_cmd" ]; do
    # Dynamic or malformed shell text is deliberately unclassified.
    split_first_command "$remaining_cmd" || break
    if normalize_invocation "${SPLIT_WORDS[@]}"; then
        while IFS= read -r pat; do
            [ -z "$pat" ] && continue
            if pattern_matches_normalized "$pat"; then
                match=1
                break
            fi
        done <<EOF
$patterns
EOF

    fi
    [ "$match" -eq 0 ] || break
    [ -n "$SPLIT_REST" ] || break
    remaining_cmd=$SPLIT_REST
done

if [ "$match" -eq 1 ]; then
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"signal command — rtk passthrough"}}\n'
    exit 0
fi

# Bulk path — forward to rtk for token reduction.
forward_to_rtk
