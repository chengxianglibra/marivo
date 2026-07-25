"""Bootstrap-only CLI rendering for the installed environment."""

from __future__ import annotations

from marivo.introspection.live.model import EnvironmentFingerprint
from marivo.introspection.live.render import render_fingerprint


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
            "  marivo.help()",
            '  marivo.help("analysis.observe")',
            "  marivo.help(entry)",
        )
    )
