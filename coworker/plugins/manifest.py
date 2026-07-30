"""Plugin manifest schema — the declarative contract every coworker plugin exposes.

A plugin is any module under ``coworker.plugins`` that exposes a module-level
``MANIFEST`` (a :class:`PluginManifest`) plus two callables:

  * ``register(subparsers)`` — add exactly one top-level subparser named
    ``MANIFEST.name`` to the coworker CLI.
  * ``dispatch(args)``       — handle an invocation of that subcommand and
    return a process exit code.

Optional lifecycle hook (looked up by name, may be absent):

  * ``install(args=None)``   — surface the plugin's install step. By contract
    this MUST NOT execute a network installer (supply-chain safety); bundled
    plugins print instructions only. Invoked by ``coworker plugins install
    <name>``.

The registry (:mod:`coworker.plugins.registry`) discovers plugins by importing
sibling modules and checking for a ``MANIFEST`` attribute, so a new plugin is
added by dropping a module here — no edit to ``cli.py`` is required.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PluginManifest:
    """Declarative metadata a plugin module publishes as its ``MANIFEST``.

    Attributes:
        name: The top-level CLI subcommand the plugin owns (e.g. ``"rtk"``).
            Must match the single subparser the plugin's ``register()`` adds;
            the registry routes ``dispatch`` on this key.
        summary: One-line description shown by ``coworker plugins list``.
        version: The plugin's own contract/implementation version string.
        requires: Name of an external binary the plugin wraps, or ``None`` for
            a self-contained plugin. When set, ``coworker plugins list`` reports
            whether that binary is present on ``PATH``.
    """

    name: str
    summary: str
    version: str
    requires: str | None = None
