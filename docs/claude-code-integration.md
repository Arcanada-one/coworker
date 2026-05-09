# Using coworker with Claude Code

`coworker` was built to pair with reasoning agents — Claude Code in particular — where the harness can shell out for bulk I/O instead of spending its own context window. This document describes the delegation pattern.

## The principle

> **Reasoning model = thinking. Coworker = I/O.**

You pay top-tier prices for your reasoning model's tokens. Spending them on tasks like "summarise this 600-line file" or "draft a boilerplate config" is wasteful — the work is structurally identical on a 1¢-per-Mtok model.

`coworker` makes the delegation explicit, observable, and switchable per call.

## Triggers — when to delegate

Worth delegating:

- Reading > ~600 lines total across one or more files for one question.
- Drafting any artifact you'll edit manually afterwards (READMEs, archive docs, boilerplate code, social-media posts).
- Reading anything from a corpus that's not where the reasoning lives (e.g. raw transcripts, third-party docs, vendor SDK source).
- Pre-processing diffs / logs > ~200 lines before deciding what to do with them.

Worth keeping in the reasoning model:

- Anything < ~2000 tokens (the delegation overhead exceeds the savings).
- Debugging, root-cause analysis, race conditions, safety-critical logic.
- Architectural decisions and trade-offs.
- Cases where you need exact line numbers for surgical edits — `coworker` summaries lose them.
- Reasoning about user intent.

## A minimal `CLAUDE.md` snippet

Drop something like this into your project's `CLAUDE.md` (or your global one) so the agent knows when to reach for `coworker`:

```markdown
## Coworker delegation

CLI tool `coworker` (https://github.com/Arcanada-one/coworker) routes bulk I/O
to a cheaper provider (DeepSeek / Moonshot / Groq / OpenRouter / OpenAI).

Rule of thumb: thinking stays here, I/O goes to coworker.

Delegate via `coworker ask` if any trigger fires:
- Total lines to read > 600 (sum across files).
- ≥ 3 files for one question.
- diff/log output > 200 lines.
- Bootstrap reads of multiple long config docs.

Delegate via `coworker write` for first drafts of:
- README, install docs, configuration reference.
- Social media post drafts.
- Standard boilerplate (LICENSE, .gitignore, CI yaml).

After `coworker write`: read the output and edit judgement-parts manually.
Never accept blindly.

Do NOT delegate:
- Tasks under ~2000 tokens (overhead exceeds savings).
- Debugging, root-cause, race conditions, safety-critical logic.
- Architectural decisions and trade-offs.
- Cases where exact line numbers are needed for `Edit`.
- Reasoning about user intent.
```

## Using `--task-id` for traceability

Every Claude Code task has an identifier. Pass it through:

```bash
coworker ask --task-id "FEAT-0001" \
             --paths Projects/Auth/spec.md \
             --question "Summarise the OAuth flow in 10 bullets."
```

The task ID lands in the JSONL log under `coworker.task_id`. Then:

```bash
coworker stats --since 30d --by combined --format json | \
  jq '[.[] | {task: .task_id, cost: .sum_cost_usd}]'
```

…and you have a per-task cost ledger of what was delegated, by which model, at what cost.

## Optional: PreToolUse hook

If you want to **enforce** the delegation policy (rather than just suggest it), Claude Code's `PreToolUse` hook can refuse a `Read` of a >400-line file and tell the agent to retry with `coworker ask`. The hook is a tiny shell script outside `coworker` itself — `coworker` does not ship one. Pattern:

```bash
# In your Claude Code hooks directory: coworker-guard.sh
file="$CLAUDE_TOOL_INPUT_path"
lines=$(wc -l < "$file" 2>/dev/null || echo 0)
if [ "$lines" -gt 400 ]; then
  echo "File $file is $lines lines (>400). Use: coworker ask --paths \"$file\" --question \"...\"" >&2
  exit 2  # PreToolUse blocking-error exit code
fi
```

Wire that to `PreToolUse:Read` in your Claude Code settings. When it fires, the agent sees the message in its tool-result stream and reroutes.

## Cost shape — what you actually save

A reasoning-model session that reads ten 600-line files would cost on the order of $X. Doing the reading on DeepSeek + sending only the 100-line summary back to the reasoning model typically costs an order of magnitude less. The exact ratio depends on which provider you delegate to (`coworker stats` will show you concretely).

This isn't theoretical: `coworker stats --by combined` is the honest readout. If the numbers don't add up for your workflow, don't delegate — the rule of thumb is a heuristic, not a gospel.
