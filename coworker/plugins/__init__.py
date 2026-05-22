"""Coworker plugins namespace (TUNE-0271).

First plugin: rtk (Rust Token Killer). Future plugins land alongside as
sibling modules; CLI registration happens via explicit import + register()
calls in coworker/cli.py. The dynamic discovery contract is deferred to
TUNE-0275 (formal plugin manifest).
"""
