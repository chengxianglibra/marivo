"""Bootstrap-only CLI rendering for the installed environment."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys

from marivo.introspection.live.model import EnvironmentFingerprint
from marivo.introspection.live.render import render_fingerprint


def _same_interpreter_command(code: str) -> str:
    argv = (sys.executable, "-c", code)
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def render_focused_help_rejection(arguments: tuple[str, ...]) -> str:
    """Redirect an invalid CLI target to the public Python help coordinator."""
    target = ".".join(arguments)
    invocation = f"import marivo; marivo.help({target!r})"
    return "\n".join(
        (
            "error: CLI help is bootstrap-only and accepts no surface or target.",
            f"Python call: {invocation}",
            "Run focused help through the same interpreter:",
            f"  {_same_interpreter_command(invocation)}",
        )
    )


def render_bootstrap_help() -> str:
    """Render interpreter identity and the canonical Python help handoff."""
    return "\n".join(
        (
            "Marivo help bootstrap",
            render_fingerprint(EnvironmentFingerprint.current(), reveal=True),
            "",
            "Supported execution imports:",
            "  import marivo",
            "  import marivo.datasource as md",
            "  import marivo.semantic as ms",
            "  import marivo.analysis as mv",
            "",
            "Focused help is available only through Python:",
            "  Do not append a surface or target to `python -m marivo help`.",
            "  marivo.help()",
            '  marivo.help("authoring")',
            '  marivo.help("analysis.observe")',
            "  marivo.help(entry)",
        )
    )
