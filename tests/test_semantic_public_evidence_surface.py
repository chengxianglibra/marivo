from __future__ import annotations

import marivo.semantic as ms


def test_new_evidence_dtos_are_exported():
    for name in (
        "TableSource",
        "FileSource",
        "DatasetSource",
        "MetadataOnlyPolicy",
        "BoundedProfilePolicy",
        "SelectedColumnsPolicy",
        "SamplePolicy",
        "EvidenceFact",
        "ColumnProfile",
        "SourceEvidencePack",
        "ColumnEvidence",
        "AssessmentIssue",
        "AuthoringQuestion",
        "AuthoringAssessment",
    ):
        assert hasattr(ms, name), name
        assert name in ms.__all__, name


def test_candidate_workflow_types_are_not_exported():
    for name in ("Candidate", "ProposalResult", "ResidualColumn", "Enrichment"):
        assert name not in ms.__all__, name


def test_help_lists_new_dtos(capsys):
    data = ms.help("TableSource", format="json")
    assert data["kind"] in ("class", "callable")
