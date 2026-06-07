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
        "AiContextInput",
        "EvidenceRef",
        "EvidenceFact",
        "ColumnProfile",
        "SourceEvidencePack",
        "ColumnEvidence",
        "AssessmentIssue",
        "AuthoringQuestion",
        "AssessmentResult",
        "AuthoringEvidenceInput",
    ):
        assert hasattr(ms, name), name
        assert name in ms.__all__, name


def test_evidence_ref_is_the_new_authoring_shape():
    # the new EvidenceRef has id/kind/collected_at, not evidence_type/locator
    fields = ms.EvidenceRef.__dataclass_fields__
    assert "id" in fields and "collected_at" in fields
    assert "evidence_type" not in fields


def test_candidate_workflow_types_are_not_exported():
    for name in ("Candidate", "ProposalResult", "ResidualColumn", "Enrichment"):
        assert name not in ms.__all__, name


def test_help_lists_new_dtos(capsys):
    data = ms.help("TableSource", format="json")
    assert data["kind"] in ("class", "callable")
