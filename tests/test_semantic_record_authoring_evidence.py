from __future__ import annotations

from marivo.semantic.evidence import AuthoringEvidenceInput
from marivo.semantic.evidence_store import content_fingerprint
from marivo.semantic.reader import SemanticProject


def _project(tmp_path) -> SemanticProject:
    root = tmp_path / ".marivo" / "semantic"
    root.mkdir(parents=True)
    return SemanticProject(root=root)


def test_record_source_sql_returns_ref_with_content_fingerprint(tmp_path):
    project = _project(tmp_path)
    ref = project.record_authoring_evidence(
        AuthoringEvidenceInput(
            kind="source_sql",
            subject_refs=("sales.revenue",),
            content="select sum(amount) as revenue from orders where paid = 1",
            source_dialect="trino",
            source_document="bi://revenue-dashboard",
        )
    )
    assert ref.kind == "source_sql"
    assert ref.content_fingerprint == content_fingerprint(
        "select sum(amount) as revenue from orders where paid = 1"
    )
    assert ref.id.startswith("doc:")


def test_recorded_user_confirmation_is_retrievable_by_subject(tmp_path):
    project = _project(tmp_path)
    ref = project.record_authoring_evidence(
        AuthoringEvidenceInput(
            kind="user_confirmation",
            subject_refs=("sales.order_date",),
            content="Use dt as the reporting time axis.",
        )
    )
    refs = project.list_evidence(subject_refs=("sales.order_date",))
    assert [r.id for r in refs] == [ref.id]
