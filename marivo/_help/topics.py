"""Global help topics."""

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
                "Marivo",
                "  A pure Python library for governed, auditable analysis.",
                "  It separates reusable definitions from analytical work:",
                "    Datasource -> physical connections and source evidence",
                "    Semantic   -> governed business objects and stable refs",
                "    Analysis   -> typed artifacts, findings, evidence, and lineage",
                "  Marivo does not infer business meaning or choose conclusions;",
                "  the agent owns interpretation and judgment.",
                "",
                "  Choose one secondary root:",
                '    marivo.help("authoring")',
                "      Connect data, inspect sources, define and validate semantics.",
                '    marivo.help("analysis")',
                "      Use governed semantic inputs to create typed analysis artifacts.",
                "",
                "  Help is static and side-effect-free. Current results expose show()",
                "  and, when applicable, contract(); structured errors own repairs.",
                "  Domain modules expose no public .help alias; use marivo.help(...).",
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
                "",
                "  Full public capability maps:",
                '    marivo.help("datasource")',
                '    marivo.help("semantic")',
                '    marivo.help("ontology")',
            )
        )
    )
