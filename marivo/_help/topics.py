"""Global help topics and the complete canonical target inventory."""

from __future__ import annotations

import marivo
from marivo._help.route import canonical_string_target_groups
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
                "    ontology.*   -> marivo.ontology capability registry",
                "",
                "  Start:",
                '    marivo.help("authoring")',
                '    marivo.help("load")',
                '    marivo.help("targets")',
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
                "  Optional ontology guidance:",
                '    marivo.help("ontology.authoring")',
                "",
                "  Unique unqualified targets route automatically; qualify shared names.",
                "  Domain modules expose no public .help alias; use marivo.help(...).",
            )
        ),
        root=True,
    )


def render_targets() -> str:
    """Render the complete finite inventory without focused-help truncation."""
    lines = ["Marivo help targets", f"Version: {marivo.__version__}", ""]
    groups = canonical_string_target_groups()
    for index, (heading, targets) in enumerate(groups):
        lines.append(heading)
        lines.extend(f"- {target}" for target in targets)
        if index != len(groups) - 1:
            lines.append("")
    return "\n".join(lines)


def render_authoring() -> str:
    return _bounded(
        "\n".join(
            (
                "authoring",
                "  Route from current live state; do not restart completed work.",
                "  Inspect authoritative metadata before asking for physical facts.",
                "  Choose inspection, optional bounded sampling, or governed raw SQL",
                "  according to the unresolved question.",
                "",
                "  Read current project catalogs (zero business-row access):",
                "    import marivo.datasource as md",
                "    import marivo.semantic as ms",
                "    datasource_catalog = md.load()",
                "    semantic_catalog = ms.load()",
                "",
                "  Read the current result or error first:",
                "    result.show()",
                "    result.contract().show() when the result owns mechanical read facts",
                "",
                '  Datasource evidence and scope: marivo.help("datasource.authoring")',
                '  Semantic source and validation: marivo.help("semantic.authoring")',
                '  Optional ontology context: marivo.help("ontology.authoring")',
            )
        )
    )


def render_load() -> str:
    return _bounded(
        "\n".join(
            (
                "load",
                "  Typed loading operations have separate owners:",
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
                "  Optional ontology:",
                "    import marivo.ontology as mo",
                "    ontology = mo.load(semantic=semantic_catalog)",
                '    marivo.help("ontology.authoring")',
                "",
                "  There is no marivo.load(kind=...) dispatcher.",
            )
        )
    )
