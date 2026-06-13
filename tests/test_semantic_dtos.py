from __future__ import annotations

from typing import get_args, get_type_hints

import pytest

import marivo.semantic as ms
from marivo.semantic.dtos import (
    AssessmentIssue,
    AuthoringAssessment,
    AuthoringQuestion,
    AuthoringSourceInput,
    FileSource,
    TableSource,
    derive_status,
)
from marivo.semantic.ir import (
    FileSourceIR,
    TableSourceIR,
)


def test_table_source_round_trips_through_ir():
    src = TableSource(table="orders", database="sales_mart")
    ir = src.to_ir()
    assert isinstance(ir, TableSourceIR)
    assert ir.table == "orders"
    assert ir.database == "sales_mart"


def test_file_source_round_trips_through_ir():
    src = FileSource(path="/data/orders.parquet", format="parquet")
    ir = src.to_ir()
    assert isinstance(ir, FileSourceIR)
    assert ir.path == "/data/orders.parquet"


def test_table_source_is_shared_datasource_ir_type():
    from marivo.datasource.ir import TableSourceIR
    from marivo.semantic.dtos import TableSource

    source = TableSource(table="orders", database="warehouse")

    assert isinstance(source, TableSourceIR)
    assert source.to_ir() is source
    assert source.to_dict() == {
        "kind": "table",
        "table": "orders",
        "database": "warehouse",
    }


def test_file_source_is_shared_datasource_ir_type():
    from marivo.datasource.ir import FileSourceIR
    from marivo.semantic.dtos import FileSource

    source = FileSource(path="orders.parquet", format="parquet")

    assert isinstance(source, FileSourceIR)
    assert source.to_ir() is source
    assert source.to_dict() == {
        "kind": "file",
        "path": "orders.parquet",
        "format": "parquet",
        "options": {},
    }


def test_file_source_supports_json():
    src = FileSource(path="/data/orders.json", format="json")
    ir = src.to_ir()
    assert isinstance(ir, FileSourceIR)
    assert ir.format == "json"


def test_file_source_json_dict_round_trips_through_semantic_ir_parser():
    from marivo.semantic.ir import source_from_dict

    src = FileSource(path="/data/orders.json", format="json")

    restored = source_from_dict(src.to_dict())

    assert isinstance(restored, FileSourceIR)
    assert restored.path == "/data/orders.json"
    assert restored.format == "json"


def test_dataset_source_cannot_mix_table_and_file_fields():
    with pytest.raises(TypeError):
        TableSource(table="orders", path="/data/orders.parquet")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        TableSource(table="orders", format="parquet")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        FileSource(path="/data/orders.parquet", format="parquet", table="orders")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        FileSource(path="/data/orders.parquet", format="parquet", database="sales_mart")  # type: ignore[call-arg]


def test_table_source_to_dict_is_json_safe():
    src = TableSource(table="orders", database=("a", "b"))
    assert src.to_dict() == {
        "kind": "table",
        "table": "orders",
        "database": ["a", "b"],
    }


def test_file_source_to_dict_is_json_safe():
    src = FileSource(path="/data/orders.parquet", format="parquet")
    assert src.to_dict() == {
        "kind": "file",
        "path": "/data/orders.parquet",
        "format": "parquet",
        "options": {},
    }


def test_authoring_source_input_to_dict_is_json_safe():
    src = AuthoringSourceInput(
        role="from",
        datasource="warehouse",
        source=TableSource(table="orders", database="sales_mart"),
        columns=("customer_id",),
    )
    assert src.to_dict() == {
        "role": "from",
        "datasource": "warehouse",
        "source": {"kind": "table", "table": "orders", "database": "sales_mart"},
        "columns": ["customer_id"],
    }


def test_authoring_source_role_is_finite_public_vocabulary():
    from marivo.semantic.dtos import AuthoringSourceRole

    assert get_args(AuthoringSourceRole) == ("primary", "from", "to", "component")
    assert get_type_hints(AuthoringSourceInput)["role"] == AuthoringSourceRole


def test_derive_status_blocked_on_blocker_issue():
    issue = AssessmentIssue(
        kind="missing_column",
        severity="blocker",
        refs=("sales.revenue",),
        message="x",
        rule_id="r1",
    )
    assert derive_status((issue,), ()) == "blocked"


def test_derive_status_blocked_on_blocking_question():
    q = AuthoringQuestion(
        id="q1",
        decision_kind="metric_provenance_status",
        subject_refs=("sales.revenue",),
        prompt="p",
        reason="r",
        readiness_effect="blocks",
    )
    assert derive_status((), (q,)) == "blocked"


def test_derive_status_needs_input_then_supported():
    needs = AssessmentIssue(
        kind="missing_evidence",
        severity="warning",
        refs=("sales.revenue",),
        message="x",
        rule_id="r1",
    )
    assert derive_status((needs,), ()) == "needs_input"
    assert derive_status((), ()) == "supported"


def test_authoring_assessment_status_uses_needs_input():
    issue = AssessmentIssue(
        kind="missing_source",
        severity="warning",
        refs=("sales.revenue",),
        message="source context is missing",
        rule_id="source_context_present",
    )
    status = derive_status((issue,), ())
    assessment = AuthoringAssessment(status=status, issues=(issue,), questions=())

    assert status == "needs_input"
    assert assessment.status == "needs_input"


def test_authoring_assessment_is_frozen():
    assessment = AuthoringAssessment(status="supported", issues=(), questions=())
    with pytest.raises(AttributeError):
        assessment.status = "blocked"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Stepwise authoring: Brief DTO tests
# ---------------------------------------------------------------------------


_BRIEF_NAMES = [
    "DomainBrief",
    "EntityBrief",
    "DimensionBrief",
    "TimeDimensionBrief",
    "MetricBrief",
    "RelationshipBrief",
    "CrossEntityMetricBrief",
    "DerivedMetricBrief",
]


def test_brief_status_replaces_review_status_for_stepwise_authoring() -> None:
    from marivo.semantic.dtos import BriefStatus

    assert set(get_args(BriefStatus)) == {"sufficient", "needs_input", "blocked"}


def test_every_brief_field_has_a_description() -> None:
    import dataclasses

    import marivo.semantic as ms

    missing: list[str] = []
    for name in _BRIEF_NAMES:
        cls = getattr(ms, name)
        for f in dataclasses.fields(cls):
            if not f.metadata.get("description"):
                missing.append(f"{name}.{f.name}")
    assert not missing, f"Brief fields without a description: {missing}"


def test_help_emits_brief_field_descriptions() -> None:
    import dataclasses

    import marivo.semantic as ms
    from marivo.introspection.surface import render
    from marivo.semantic.help import _surface

    for name in _BRIEF_NAMES:
        cls = getattr(ms, name)
        data = render(_surface(), name, "json")
        fields = {f["name"]: f for f in data.get("fields", [])}
        missing = [
            f.name for f in dataclasses.fields(cls) if not fields.get(f.name, {}).get("description")
        ]
        assert not missing, f"Help fields without a description for {name}: {missing}"

    data = render(_surface(), "CrossEntityMetricBrief", "json")
    fields = {f["name"]: f for f in data.get("fields", [])}
    assert fields["entities"]["description"] == "Target entity refs to join from the root entity."


def test_registered_match_is_explainable_not_fuzzy() -> None:
    from marivo.semantic.dtos import RegisteredMatch

    match = RegisteredMatch(ref="sales.orders", basis="same_source")

    assert match.ref == "sales.orders"
    assert match.basis == "same_source"
    # basis is an enum-like literal, not a fuzzy keyword match
    assert "keyword" not in set(get_args(type(match).__annotations__["basis"]))


def test_verify_result_is_public_result_object() -> None:
    from marivo.semantic.dtos import VerifyResult

    result = VerifyResult(
        status="passed",
        ref="sales.orders",
        kind="entity",
        issues=(),
        warnings=(),
        scan=None,
        auto_recorded=(),
    )

    assert repr(result) == "<VerifyResult status=passed ref=sales.orders kind=entity>"
    assert "VerifyResult status=passed" in result.render()
    assert ms.VerifyResult is VerifyResult
