"""Authoring state, effect, and transition model contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from marivo._authoring.model import (
    AuthoringEffects,
    AuthoringStateRef,
)


def test_authoring_state_ref_defaults():
    ref = AuthoringStateRef(id="semantic.loaded")
    assert ref.id == "semantic.loaded"
    assert ref.subject_refs == ()
    assert ref.evidence_ids == ()


def test_authoring_state_ref_rejects_unknown_id():
    with pytest.raises(ValidationError):
        AuthoringStateRef(id="not.a.state")


def test_authoring_effects_orthogonal_axes():
    effects = AuthoringEffects(
        data_access="scoped_data_read",
        connection="opens_connection",
        mutations=("project_state",),
        flags=("requires_explicit_scope", "requires_positive_row_guard"),
    )
    assert effects.data_access == "scoped_data_read"
    assert effects.connection == "opens_connection"
    assert effects.mutations == ("project_state",)
    assert effects.flags == (
        "requires_explicit_scope",
        "requires_positive_row_guard",
    )


def test_authoring_effects_query_free_preview_has_no_data_access():
    # data_access="none" is a legal continuation effect (e.g. readiness from preview).
    effects = AuthoringEffects(data_access="none", connection="none")
    assert effects.data_access == "none"
    assert effects.mutations == ()
    assert effects.flags == ()


def test_authoring_effects_rejects_unknown_data_access():
    with pytest.raises(ValidationError):
        AuthoringEffects(data_access="reads_everything", connection="none")


def test_authoring_effects_rejects_unknown_flag():
    with pytest.raises(ValidationError):
        AuthoringEffects(
            data_access="none",
            connection="none",
            flags=("not_a_real_flag",),
        )
