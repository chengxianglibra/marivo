from __future__ import annotations

import marivo.semantic as ms
from marivo.semantic.dtos import (
    AssessmentIssue,
    AuthoringAssessment,
    DatasetSource,
    FileSource,
    TableSource,
)


def test_new_evidence_dtos_are_importable():
    # Evidence DTOs are internal; importable from the submodule but not in __all__.
    for name, cls in (
        ("TableSource", TableSource),
        ("FileSource", FileSource),
        ("DatasetSource", DatasetSource),
        ("AssessmentIssue", AssessmentIssue),
        ("AuthoringAssessment", AuthoringAssessment),
    ):
        assert cls is not None, name

    # AuthoringQuestion is the one evidence DTO that remains public.
    assert "AuthoringQuestion" in ms.__all__


def test_candidate_workflow_types_are_not_exported():
    for name in ("Candidate", "ProposalResult", "ResidualColumn", "Enrichment"):
        assert name not in ms.__all__, name


def test_help_lists_remaining_dtos(capsys):
    from marivo.introspection.surface import render as surface_render
    from marivo.semantic.help import _surface

    data = surface_render(_surface(), "AuthoringQuestion", "json")
    assert data["kind"] in ("class", "callable")
