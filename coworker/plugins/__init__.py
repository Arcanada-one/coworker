"""Coworker plugins namespace.

A plugin is a module in this package that publishes a ``MANIFEST``
(:class:`coworker.plugins.manifest.PluginManifest`) plus ``register(subparsers)``
and ``dispatch(args)`` callables, and optionally an ``install(args=None)``
lifecycle hook. The registry (:mod:`coworker.plugins.registry`) discovers such
modules automatically and wires them into the CLI, so a new plugin is added by
dropping a module here — no edit to ``coworker/cli.py`` is required.

First plugin: ``rtk`` (Rust Token Killer). ``coworker plugins list`` enumerates
discovered plugins; ``coworker plugins install <name>`` runs a plugin's install
hook (print-only — coworker never executes a network installer).
"""
