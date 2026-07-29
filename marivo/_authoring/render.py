"""Bounded rendering for private authoring contracts."""

from __future__ import annotations

from marivo._authoring.model import (
    AuthoringContract,
    AuthoringJudgmentRequirement,
    AuthoringTransition,
)
from marivo.introspection.live.render import enforce_budget
from marivo.render import _DEFAULT_MAX_OUTPUT_BYTES, Card


def _render_state_summaries(contract: AuthoringContract) -> tuple[str, ...]:
    """Summarize repeated per-subject states by state id."""
    subjects_by_state: dict[str, dict[str, None]] = {}
    for state in contract.states:
        subjects = subjects_by_state.setdefault(state.id, {})
        for subject_ref in state.subject_refs:
            subjects.setdefault(subject_ref, None)
    return tuple(
        state_id if len(subjects) == 1 else f"{state_id} (subjects={len(subjects)})"
        for state_id, subjects in subjects_by_state.items()
    )


def _render_transition(transition: AuthoringTransition) -> str:
    flag = "available" if transition.available else "blocked"
    line = f"- {transition.kind} [{flag}] -> {transition.help_target.display}"
    if not transition.available and transition.blocked_by:
        line += f"  blocked_by={', '.join(transition.blocked_by)}"
    return line


def _render_judgment(requirement: AuthoringJudgmentRequirement) -> str:
    subjects = ", ".join(requirement.subjects) or "(none)"
    evidence = ", ".join(requirement.evidence_ids) or "(none)"
    return (
        f"{requirement.id}: subjects={subjects}; evidence={evidence}; "
        f"authority={requirement.authority}"
    )


def render_contract(
    contract: AuthoringContract,
    *,
    max_lines: int,
    max_codepoints: int,
    max_output_bytes: int | None = _DEFAULT_MAX_OUTPUT_BYTES,
) -> str:
    """Render every mechanical transition within a hard output budget."""
    card = Card(
        identity=contract._repr_identity(),
        available=(
            ".states",
            ".transitions",
            ".judgment_requirements",
            ".model_dump()",
            ".render()",
            ".show()",
        ),
    ).field(
        "subjects",
        ", ".join(contract.subject_refs) if contract.subject_refs else "(none)",
    )
    card = card.listing("states", _render_state_summaries(contract))
    card = card.listing(
        "transitions",
        (_render_transition(transition).removeprefix("- ") for transition in contract.transitions)
        if contract.transitions
        else ("no mechanically invokable continuation disclosed",),
    )
    if contract.judgment_requirements:
        card = card.listing(
            "non-mechanical judgment requirements",
            (_render_judgment(requirement) for requirement in contract.judgment_requirements),
        )
    return enforce_budget(
        card.render(max_output_bytes=max_output_bytes),
        max_lines=max_lines,
        max_codepoints=max_codepoints,
    )
