# Coworker plugins

Coworker ships a small set of **optional plugins** — self-contained subcommands
that are not part of the core `ask` / `write` / `stats` / `debug` surface. The
first (and currently only) bundled plugin is [`rtk`](rtk-plugin.md).

Plugins are discovered automatically from the `coworker.plugins` package, so a
new plugin is added by dropping a module in — no change to `coworker/cli.py` is
required.

## Managing plugins

```text
coworker plugins list                 # enumerate discovered plugins
coworker plugins list --format json   # machine-readable form
coworker plugins install <name>       # run a plugin's install lifecycle hook
```

`plugins list` reports each plugin's name, version, one-line summary, and a
status. When a plugin wraps an external binary, the status reflects whether that
binary is on your `PATH`:

```text
$ coworker plugins list
rtk  (v1.0)  — Opt-in Rust Token Killer (RTK) integration — compact bulk tool output.  [ready]
```

`plugins install <name>` invokes the named plugin's install hook. **Coworker
never runs a network installer itself** (supply-chain safety) — bundled plugins
print the install instructions for you to run, exactly like `coworker rtk
install`.

Every plugin also keeps its own top-level subcommand once discovered, e.g.
`coworker rtk enable`. `coworker plugins …` is the umbrella for cross-plugin
discovery and installation; the per-plugin subcommand is where the plugin's own
verbs live.

## Authoring a plugin

A plugin is any module under `coworker/plugins/` that publishes a module-level
`MANIFEST` plus two callables. Drop the module in the package and it is picked
up on the next run.

### 1. Declare a manifest

```python
from coworker.plugins.manifest import PluginManifest

MANIFEST = PluginManifest(
    name="rtk",          # the top-level subcommand this plugin owns
    summary="Opt-in Rust Token Killer (RTK) integration.",
    version="1.0",       # the plugin's own version string
    requires="rtk",      # external binary this plugin wraps, or None
)
```

`requires` is optional (defaults to `None`). When set, `plugins list` derives a
PATH-presence status from it; when `None`, the plugin reports `ready`.

### 2. Register the subcommand

```python
def register(subparsers):
    """Add exactly one subparser named MANIFEST.name."""
    p = subparsers.add_parser("rtk", help="Manage the RTK plugin.")
    sub = p.add_subparsers(dest="rtk_action", required=True)
    sub.add_parser("status", help="Report plugin status.")
    # …
```

The subparser you add **must** be named `MANIFEST.name` — the registry routes
dispatch on that key.

### 3. Dispatch

```python
def dispatch(args) -> int:
    """Handle an invocation of this plugin's subcommand; return an exit code."""
    ...
```

### 4. (Optional) install lifecycle hook

```python
def install(args=None) -> int:
    """Print install instructions. Must NOT execute a network installer."""
    print("…")
    return 0
```

If present, `coworker plugins install <name>` calls this hook. If absent, the
command reports that the plugin has no install step.

## Contract summary

| Symbol | Required | Purpose |
|--------|----------|---------|
| `MANIFEST` (`PluginManifest`) | yes | Discovery + metadata. |
| `register(subparsers)` | yes | Add one subparser named `MANIFEST.name`. |
| `dispatch(args) -> int` | yes | Handle the subcommand; return an exit code. |
| `install(args=None) -> int` | no | Print install steps (never run an installer). |

## Graceful degradation

Plugin modules are imported defensively. If a plugin fails to import — a syntax
error, a missing dependency, a malformed manifest — the registry logs a warning
to stderr and skips it. `coworker --help` and every other command keep working;
one broken plugin never takes the CLI down.
