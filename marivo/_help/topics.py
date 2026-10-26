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
                "    Analysis   -> source observations and calculations over current data",
                "  Marivo does not infer business meaning or choose conclusions;",
                "  the agent owns interpretation and judgment.",
                "",
                "  Choose one secondary root:",
                '    marivo.help("authoring")',
                "      Connect data, inspect sources, define and validate semantics.",
                '    marivo.help("analysis")',
                "      Answer questions with governed inputs, Frames and saved results.",
                "",
                "  Help is static and side-effect-free. Current results expose show()",
                "  for captured facts; structured errors own concrete repairs.",
                "  Domain modules expose no public .help alias; use marivo.help(...).",
            )
        ),
        render_class="root",
        outgoing_routes=("authoring", "analysis"),
    )


def render_authoring() -> str:
    from marivo.introspection.live.model import EnvironmentFingerprint
    from marivo.introspection.live.render import render_fingerprint

    routes = (
        ("Start or load this project", "semantic.load"),
        ("Find existing definitions", "semantic.catalog"),
        ("Configure a connection", "datasource.connections"),
        ("Inspect physical evidence", "datasource.inspection"),
        ("Define reusable business meaning", "semantic.model"),
        ("Compose governed expressions", "semantic.expressions"),
        ("Choose validation or handoff", "semantic.validation"),
        ("Optional contextual relations", "ontology"),
    )
    return _bounded(
        "\n".join(
            (
                "authoring",
                render_fingerprint(EnvironmentFingerprint.current(), reveal=True),
                "  Imports: import marivo.datasource as md; import marivo.semantic as ms",
                "  Python files hold definitions; load returns their current Catalog.",
                "  Enter known targets directly; otherwise choose the missing information.",
                "",
                *(f'  {label}: marivo.help("{target}")' for label, target in routes),
                "",
                "  Current result or structured error:",
                "    result.show(); marivo.help(type(result))",
                "",
                "  Physical evidence constrains encodings; it does not establish",
                "  reusable business meaning or approve an analytical conclusion.",
            )
        ),
        render_class="decision_hub",
        outgoing_routes=tuple(target for _, target in routes),
    )
