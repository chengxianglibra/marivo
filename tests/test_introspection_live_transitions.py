"""Authoring transition and contract model contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from marivo.introspection.live.model import (
    AuthoringContract,
    AuthoringEffects,
    AuthoringInputRequirement,
    AuthoringStateRef,
    AuthoringTransition,
    LiveHelpTarget,
)


def _preview_transition(available: bool) -> AuthoringTransition:
    return AuthoringTransition(
        kind="preview",
        help_target=LiveHelpTarget(surface="semantic", canonical_id="preview"),
        subject_refs=("metric:sales",),
        required_states=(AuthoringStateRef(id="semantic.loaded", subject_refs=("metric:sales",)),),
        produced_state=AuthoringStateRef(id="semantic.previewed", subject_refs=("metric:sales",)),
        effects=AuthoringEffects(
            data_access="scoped_data_read",
            connection="opens_connection",
            mutations=("project_state",),
            flags=("requires_existing_snapshot_binding",),
        ),
        available=available,
        input_requirements=(
            AuthoringInputRequirement(
                role="subject",
                family="MetricSemantic",
                subject_refs=("metric:sales",),
            ),
            AuthoringInputRequirement(
                role="evidence",
                family="DiscoverySnapshot",
                exact_keys=("snapshot:sales",),
            ),
        ),
    )


def test_authoring_transition_carries_effects_and_inputs():
    t = _preview_transition(available=True)
    assert t.kind == "preview"
    assert t.available is True
    assert t.effects.data_access == "scoped_data_read"
    assert t.input_requirements[1].exact_keys == ("snapshot:sales",)
    assert t.blocked_by == ()


def test_authoring_transition_blocked_carries_blocker_ids():
    t = AuthoringTransition(
        kind="readiness",
        help_target=LiveHelpTarget(surface="semantic", canonical_id="readiness"),
        subject_refs=("metric:sales",),
        effects=AuthoringEffects(data_access="none", connection="none"),
        available=False,
        blocked_by=("runtime_preview_missing",),
    )
    assert t.available is False
    assert t.blocked_by == ("runtime_preview_missing",)


def test_authoring_contract_groups_states_and_transitions():
    contract = AuthoringContract(
        subject_refs=("metric:sales",),
        states=(
            AuthoringStateRef(id="semantic.loaded", subject_refs=("metric:sales",)),
            AuthoringStateRef(id="semantic.previewed", subject_refs=("metric:sales",)),
        ),
        transitions=(_preview_transition(available=True),),
    )
    assert contract.subject_refs == ("metric:sales",)
    assert len(contract.states) == 2
    assert contract.transitions[0].kind == "preview"


def test_authoring_input_requirement_defaults():
    req = AuthoringInputRequirement(role="receiver", family="SemanticCatalog")
    assert req.subject_refs == ()
    assert req.exact_keys == ()
    assert req.min_count == 1
    assert req.max_count == 1


def test_authoring_input_requirement_rejects_unknown_role():
    with pytest.raises(ValidationError):
        AuthoringInputRequirement(role="whatever", family="MetricSemantic")
