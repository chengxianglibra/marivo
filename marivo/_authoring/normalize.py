"""Shared deterministic normalization for authoring contracts."""

from __future__ import annotations

from marivo._authoring.model import AuthoringContract, AuthoringTransition


def transition_sort_key(transition: AuthoringTransition) -> tuple[object, ...]:
    """Return the shared mechanical ordering key for one transition."""
    canonical_id = transition.help_target.canonical_id
    requirements = tuple(
        (
            requirement.role,
            requirement.family,
            requirement.subject_refs,
            requirement.exact_keys,
        )
        for requirement in transition.input_requirements
    )
    return (
        transition.help_target.surface,
        (0, "") if canonical_id is None else (1, canonical_id),
        transition.kind,
        transition.subject_refs,
        requirements,
    )


def normalize_contract(contract: AuthoringContract) -> AuthoringContract:
    """Deduplicate a contract using the shared datasource/semantic order."""
    return AuthoringContract(
        subject_refs=tuple(sorted(set(contract.subject_refs))),
        states=tuple(
            sorted(
                set(contract.states),
                key=lambda state: (state.id, state.subject_refs, state.evidence_ids),
            )
        ),
        transitions=tuple(sorted(contract.transitions, key=transition_sort_key)),
    )
