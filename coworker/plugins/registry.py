"""Plugin registry — discovery, CLI registration, and the ``plugins`` meta-command.

Discovery contract: any module under ``coworker.plugins`` exposing a module-level
``MANIFEST`` (:class:`~coworker.plugins.manifest.PluginManifest`) plus callable
``register`` and ``dispatch`` attributes is a plugin. Modules are imported
defensively — a plugin that fails to import (or is malformed) is skipped with a
stderr warning and never crashes the CLI (graceful degradation). Adding a plugin
means dropping a module here; no ``cli.py`` edit is needed.
"""

from __future__ import annotations

import argparse
import functools
import importlib
import json
import pkgutil
import shutil
import sys
from dataclasses import dataclass

from .manifest import PluginManifest

# Sibling modules that are plugin infrastructure, not plugins themselves.
_NON_PLUGIN_MODULES = frozenset({"manifest", "registry"})


@dataclass(frozen=True)
class LoadedPlugin:
    """A successfully-discovered plugin: its manifest plus the imported module."""

    manifest: PluginManifest
    module: object

    @property
    def name(self) -> str:
        return self.manifest.name


def _plugins_package():
    return importlib.import_module(__package__)


def _load_one(modname: str) -> LoadedPlugin | None:
    """Import one sibling module and validate the plugin contract.

    Returns a :class:`LoadedPlugin` if the module is a well-formed plugin,
    otherwise ``None``. Import failure is caught (graceful degradation) and
    reported once to stderr; a module without a ``MANIFEST`` is silently a
    non-plugin (helper modules).
    """
    fq = f"{__package__}.{modname}"
    try:
        mod = importlib.import_module(fq)
    except Exception as exc:  # noqa: BLE001 — one bad plugin must not kill the CLI
        print(
            f"[coworker] plugin '{modname}' failed to load and was skipped: {exc}",
            file=sys.stderr,
        )
        return None
    manifest = getattr(mod, "MANIFEST", None)
    if not isinstance(manifest, PluginManifest):
        return None  # not a plugin (e.g. a shared helper module)
    if not callable(getattr(mod, "register", None)) or not callable(
        getattr(mod, "dispatch", None)
    ):
        print(
            f"[coworker] plugin '{modname}' has a MANIFEST but no callable "
            "register()/dispatch(); skipped",
            file=sys.stderr,
        )
        return None
    return LoadedPlugin(manifest=manifest, module=mod)


@functools.lru_cache(maxsize=1)
def discover() -> tuple[LoadedPlugin, ...]:
    """Discover all well-formed plugins under ``coworker.plugins`` (name-sorted).

    Cached per-process — plugin modules do not change during a CLI invocation.
    """
    pkg = _plugins_package()
    found: list[LoadedPlugin] = []
    for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        if info.name in _NON_PLUGIN_MODULES or info.name.startswith("_"):
            continue
        loaded = _load_one(info.name)
        if loaded is not None:
            found.append(loaded)
    return tuple(found)


def plugin_names() -> list[str]:
    return [lp.name for lp in discover()]


def is_plugin(name: str) -> bool:
    return any(lp.name == name for lp in discover())


def get(name: str) -> LoadedPlugin | None:
    for lp in discover():
        if lp.name == name:
            return lp
    return None


def register_all(subparsers: argparse._SubParsersAction) -> None:
    """Wire every discovered plugin's subcommand into the CLI parser."""
    for lp in discover():
        try:
            lp.module.register(subparsers)
        except Exception as exc:  # noqa: BLE001 — a bad register() must not kill the CLI
            print(
                f"[coworker] plugin '{lp.name}' register() failed and was skipped: {exc}",
                file=sys.stderr,
            )


def dispatch(args: argparse.Namespace) -> int:
    """Route a plugin subcommand invocation to its module's ``dispatch``."""
    lp = get(args.subcommand)
    if lp is None:
        print(f"[coworker] unknown plugin '{args.subcommand}'", file=sys.stderr)
        return 1
    return lp.module.dispatch(args)


# ---------- `coworker plugins` meta-command ----------


def register_meta(subparsers: argparse._SubParsersAction) -> None:
    """Wire the built-in ``coworker plugins {list,install}`` meta-command."""
    p = subparsers.add_parser(
        "plugins",
        help="List and install coworker plugins.",
        description=(
            "Manage optional coworker plugins. Plugins are discovered from the "
            "coworker.plugins package — a well-formed plugin module is picked up "
            "automatically, no CLI wiring required."
        ),
    )
    psub = p.add_subparsers(dest="plugins_action", required=True)

    p_list = psub.add_parser("list", help="List discovered plugins.")
    p_list.add_argument(
        "--format", choices=["text", "json"], default="text", dest="format"
    )

    p_install = psub.add_parser(
        "install",
        help="Run a plugin's install lifecycle hook (never executes a network installer).",
    )
    p_install.add_argument("name", help="Plugin name (see `coworker plugins list`).")


def _plugin_status(lp: LoadedPlugin) -> str:
    req = lp.manifest.requires
    if not req:
        return "ready"
    return "ready" if shutil.which(req) else f"needs '{req}' on PATH"


def _cmd_list(args: argparse.Namespace) -> int:
    plugins = discover()
    if getattr(args, "format", "text") == "json":
        payload = [
            {
                "name": lp.manifest.name,
                "version": lp.manifest.version,
                "summary": lp.manifest.summary,
                "requires": lp.manifest.requires,
                "status": _plugin_status(lp),
            }
            for lp in plugins
        ]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if not plugins:
        print("No plugins discovered.")
        return 0
    for lp in plugins:
        print(
            f"{lp.manifest.name}  (v{lp.manifest.version})  — "
            f"{lp.manifest.summary}  [{_plugin_status(lp)}]"
        )
    return 0


def _cmd_install(args: argparse.Namespace) -> int:
    lp = get(args.name)
    if lp is None:
        available = ", ".join(plugin_names()) or "(none)"
        print(
            f"[coworker] unknown plugin '{args.name}'. Available: {available}",
            file=sys.stderr,
        )
        return 1
    hook = getattr(lp.module, "install", None)
    if not callable(hook):
        print(f"[coworker] plugin '{lp.name}' has no install step.", file=sys.stderr)
        return 1
    return hook(args)


def cmd_plugins(args: argparse.Namespace) -> int:
    """Handler for the ``plugins`` meta-command (list / install)."""
    action = getattr(args, "plugins_action", None)
    if action == "list":
        return _cmd_list(args)
    if action == "install":
        return _cmd_install(args)
    print("[coworker plugins] internal error: no action bound", file=sys.stderr)
    return 1
