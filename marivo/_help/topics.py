"""Bounded global help topics."""

from __future__ import annotations

from marivo.introspection.live.model import SURFACE_LIMITS
from marivo.introspection.live.render import enforce_budget


def _bounded(text: str, *, root: bool = False) -> str:
    return enforce_budget(
        text,
        max_lines=(
            SURFACE_LIMITS.root_help_max_lines if root else SURFACE_LIMITS.focused_help_max_lines
        ),
        max_codepoints=(
            SURFACE_LIMITS.root_help_max_codepoints
            if root
            else SURFACE_LIMITS.focused_help_max_codepoints
        ),
    )


def render_root() -> str:
    return _bounded(
        "\n".join(
            (
                "marivo.help",
                "  The one public coordinator for registered Marivo help.",
                "  It routes only; static contract content remains owned by:",
                "    datasource.* -> marivo.datasource capability registry",
                "    semantic.*   -> marivo.semantic capability registry",
                "    analysis.*   -> marivo.analysis capability registry",
                "",
                "  Start:",
                '    marivo.help("authoring")',
                '    marivo.help("load")',
                "",
                "  Datasource evidence:",
                '    marivo.help("datasource.inspect")',
                '    marivo.help("datasource.DiscoverySnapshot")',
                "",
                "  Semantic authoring:",
                '    marivo.help("semantic.metric")',
                '    marivo.help("semantic.readiness")',
                "",
                "  Analysis:",
                '    marivo.help("analysis")',
                "",
                "  Unique unqualified targets route automatically; qualify shared names.",
                "  md and ms execute their domain APIs; neither exposes a public .help alias.",
            )
        ),
        root=True,
    )


def render_authoring() -> str:
    return _bounded(
        "\n".join(
            (
                "authoring",
                "  Route from current live state; do not restart completed work.",
                "  Before data access or capability enumeration:",
                "    Ask only the earliest missing accountable input and stop.",
                "    Do not bundle owner with an independent business decision.",
                "    A user-named build target satisfies target-concept preflight",
                "    unless ambiguity would change the requested scope.",
                "",
                "  Read current project catalogs (zero business-row access):",
                "    import marivo.datasource as md",
                "    import marivo.semantic as ms",
                "    datasource_catalog = md.load()",
                "    semantic_catalog = ms.load()",
                "",
                "  Read the current result or error first:",
                "    result.show()",
                "    result.contract().show()",
                "",
                '  Datasource evidence and scope: marivo.help("datasource.authoring")',
                '  Semantic source and validation: marivo.help("semantic.authoring")',
            )
        )
    )


def render_load() -> str:
    return _bounded(
        "\n".join(
            (
                "load",
                "  Two distinct typed operations share this local name:",
                "",
                "  Datasource:",
                "    import marivo.datasource as md",
                "    datasource_catalog = md.load()",
                '    marivo.help("datasource.load")',
                "",
                "  Semantic:",
                "    import marivo.semantic as ms",
                "    semantic_catalog = ms.load()",
                '    marivo.help("semantic.load")',
                "",
                "  There is no marivo.load(kind=...) dispatcher.",
            )
        )
    )
