"""Bounded rendering for private authoring contracts."""

from __future__ import annotations

from marivo._authoring.model import (
    AuthoringContract,
    AuthoringInputRequirement,
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
    status = "available" if transition.available else "blocked"
    requirements = "; ".join(
        _render_input_requirement(requirement) for requirement in transition.input_requirements
    )
    output = transition.expected_output_family or "none"
    line = (
        f"{transition.public_entrypoint} -> {output}; status={status}; "
        f"inputs={requirements or 'none'}; help={_help_call(transition)}"
    )
    if not transition.available and transition.blocked_by:
        line += f"; blocked_by={', '.join(transition.blocked_by)}"
    return line


def _render_input_requirement(requirement: AuthoringInputRequirement) -> str:
    parts = [f"{requirement.role}={requirement.family}"]
    if requirement.min_count != 1 or requirement.max_count != 1:
        if requirement.max_count is None:
            parts.append(f"count>={requirement.min_count}")
        elif requirement.min_count == requirement.max_count:
            parts.append(f"count={requirement.min_count}")
        else:
            parts.append(f"count={requirement.min_count}..{requirement.max_count}")
    if requirement.subject_refs:
        parts.append(f"subjects={len(requirement.subject_refs)}")
    if requirement.exact_keys:
        parts.append(f"keys={len(requirement.exact_keys)}")
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]}({', '.join(parts[1:])})"


def _help_call(transition: AuthoringTransition) -> str:
    target = transition.help_target
    suffix = f".{target.canonical_id}" if target.canonical_id is not None else ""
    return f'marivo.help("{target.surface}{suffix}")'


def _receiver_group(transition: AuthoringTransition) -> str:
    return transition.public_entrypoint.partition(".")[0]


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
            ".show()",
        ),
    ).field(
        "subjects",
        ", ".join(contract.subject_refs) if contract.subject_refs else "(none)",
    )
    card = card.listing("states", _render_state_summaries(contract))
    if not contract.transitions:
        card = card.field("continuations", "none")
    else:
        card = card.field("continuations", "mechanical, unranked")
        groups: dict[str, list[AuthoringTransition]] = {}
        for transition in contract.transitions:
            groups.setdefault(_receiver_group(transition), []).append(transition)
        for receiver, transitions in groups.items():
            card = card.listing(
                receiver,
                (_render_transition(transition) for transition in transitions),
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
