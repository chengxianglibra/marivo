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
                "  One help surface for datasource evidence, semantic authoring, and analysis.",
                "",
                "  Start:",
                '    marivo.help("authoring")',
                '    marivo.help("load")',
                "",
                "  Datasource evidence:",
                '    marivo.help("datasource.inspect")',
                '    marivo.help("datasource.snapshot")',
                "",
                "  Semantic authoring:",
                '    marivo.help("semantic.metric")',
                '    marivo.help("semantic.readiness")',
                "",
                "  Analysis:",
                '    marivo.help("analysis")',
                "",
                "  Unique unqualified targets route automatically; qualify shared names.",
            )
        ),
        root=True,
    )


def render_authoring() -> str:
    return _bounded(
        "\n".join(
            (
                "authoring",
                "  End-to-end governed authoring workflow:",
                "    1. declare datasource",
                "    2. inspect physical metadata",
                "    3. sample one explicit bounded scope",
                "    4. project evidence",
                "    5. author semantic objects",
                "    6. verify",
                "    7. preview",
                "    8. check readiness",
                "    9. hand ready entries to analysis",
                "",
                '  Datasource state machine: marivo.help("datasource.authoring")',
                '  Semantic state machine: marivo.help("semantic.authoring")',
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
