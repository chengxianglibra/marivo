"""Global help topics."""

from __future__ import annotations

from marivo._help.model import GLOBAL_HELP_RENDER_BUDGETS, GlobalHelpRenderClass
from marivo.introspection.live.render import enforce_budget


def _bounded(
    text: str,
    *,
    render_class: GlobalHelpRenderClass,
    outgoing_routes: tuple[str, ...],
    examples_or_snippets: int = 0,
) -> str:
    budget = GLOBAL_HELP_RENDER_BUDGETS[render_class]
    if len(outgoing_routes) > budget.max_outgoing_routes:
        raise RuntimeError(
            f"render budget exceeded: {len(outgoing_routes)} routes > {budget.max_outgoing_routes}"
        )
    if examples_or_snippets > budget.max_examples_or_snippets:
        raise RuntimeError(
            f"render budget exceeded: {examples_or_snippets} examples/snippets > "
            f"{budget.max_examples_or_snippets}"
        )
    return enforce_budget(
        text,
        max_lines=budget.max_lines,
        max_codepoints=budget.max_codepoints,
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
        render_class="root",
        outgoing_routes=("authoring", "analysis"),
    )


def render_authoring() -> str:
    return _bounded(
        "\n".join(
            (
                "authoring",
                "  Choose the surface that owns the unresolved authoring need.",
                "",
                "  Physical source definitions and evidence:",
                '    marivo.help("datasource.authoring")',
                "",
                "  Executable reusable business semantics:",
                '    marivo.help("semantic.authoring")',
                "",
                "  Optional non-executable contextual relations:",
                '    marivo.help("ontology.authoring")',
                "",
                "  Current result or structured error:",
                "    marivo.help(result_or_error)",
                "",
                "  Surface roots when an exact target is already known:",
                '    marivo.help("datasource")',
                '    marivo.help("semantic")',
                '    marivo.help("ontology")',
                "",
                "  Physical evidence constrains encodings; it does not establish",
                "  reusable business meaning or approve an analytical conclusion.",
            )
        ),
        render_class="decision_hub",
        outgoing_routes=(
            "datasource.authoring",
            "semantic.authoring",
            "ontology.authoring",
            "datasource",
            "semantic",
            "ontology",
        ),
    )
