"""Bounded rendering for private authoring contracts."""

from __future__ import annotations

from marivo._authoring.model import AuthoringContract, AuthoringTransition
from marivo.introspection.live.render import enforce_budget


def _render_transition(transition: AuthoringTransition) -> str:
    flag = "available" if transition.available else "blocked"
    line = f"- {transition.kind} [{flag}] -> {transition.help_target.display}"
    if not transition.available and transition.blocked_by:
        line += f"  blocked_by={', '.join(transition.blocked_by)}"
    return line


def render_contract(
    contract: AuthoringContract,
    *,
    max_lines: int,
    max_codepoints: int,
) -> str:
    """Render every mechanical transition within a hard output budget."""
    subjects = ", ".join(contract.subject_refs) if contract.subject_refs else "(none)"
    lines = [
        f"Subject: {subjects}",
        f"States: {', '.join(state.id for state in contract.states) or '(none)'}",
        "Transitions:",
    ]
    if contract.transitions:
        lines.extend(_render_transition(transition) for transition in contract.transitions)
    else:
        lines.append("- no mechanically invokable continuation disclosed")
    return enforce_budget(
        "\n".join(lines),
        max_lines=max_lines,
        max_codepoints=max_codepoints,
    )
