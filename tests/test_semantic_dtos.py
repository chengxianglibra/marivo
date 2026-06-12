from __future__ import annotations

from typing import get_args, get_type_hints

import pytest

import marivo.semantic as ms
from marivo.semantic.dtos import (
    AssessmentIssue,
    AuthoringAssessment,
    AuthoringQuestion,
    AuthoringSourceInput,
    BoundedProfilePolicy,
    ColumnProfile,
    FileSource,
    MetadataOnlyPolicy,
    SelectedColumnsPolicy,
    TableSource,
    derive_status,
)
from marivo.semantic.ir import (
    BoundedProfilePolicyIR,
    FileSourceIR,
    MetadataOnlyPolicyIR,
    SelectedColumnsPolicyIR,
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


def test_file_source_supports_json():
    src = FileSource(path="/data/orders.json", format="json")
    ir = src.to_ir()
    assert isinstance(ir, FileSourceIR)
    assert ir.format == "json"


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


def test_metadata_only_policy_round_trips_through_ir():
    policy = MetadataOnlyPolicy(timeout_seconds=30)
    ir = policy.to_ir()
    assert isinstance(ir, MetadataOnlyPolicyIR)
    assert ir.timeout_seconds == 30


def test_bounded_profile_policy_round_trips_through_ir():
    policy = BoundedProfilePolicy(limit=100, max_profiled_columns=10)
    ir = policy.to_ir()
    assert isinstance(ir, BoundedProfilePolicyIR)
    assert ir.limit == 100
    assert ir.max_profiled_columns == 10


def test_selected_columns_policy_round_trips_through_ir():
    policy = SelectedColumnsPolicy(limit=100, columns=("a", "b"))
    ir = policy.to_ir()
    assert isinstance(ir, SelectedColumnsPolicyIR)
    assert ir.columns == ("a", "b")


def test_selected_columns_policy_requires_columns():
    with pytest.raises(TypeError):
        SelectedColumnsPolicy(limit=10)  # type: ignore[call-arg]


def test_bounded_profile_policy_requires_limit():
    with pytest.raises(TypeError):
        BoundedProfilePolicy()  # type: ignore[call-arg]


def test_column_profile_to_dict_is_json_safe():
    profile = ColumnProfile(
        column="status",
        data_type="string",
        nullable=False,
        comment="Order status",
        null_count=0,
        empty_count=1,
        distinct_count=2,
        top_values=(("paid", 10), ("pending", 3)),
        min_value="paid",
        max_value="pending",
        observed_formats=("lowercase", "snake_case"),
        warnings=("sampled",),
        sample_scope="bounded_sample",
        approximate=True,
    )

    assert profile.to_dict() == {
        "column": "status",
        "data_type": "string",
        "nullable": False,
        "comment": "Order status",
        "null_count": 0,
        "empty_count": 1,
        "distinct_count": 2,
        "top_values": [["paid", 10], ["pending", 3]],
        "min_value": "paid",
        "max_value": "pending",
        "observed_formats": ["lowercase", "snake_case"],
        "warnings": ["sampled"],
        "sample_scope": "bounded_sample",
        "approximate": True,
    }


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
    assessment = AuthoringAssessment(status=status, facts=(), issues=(issue,), questions=())

    assert status == "needs_input"
    assert assessment.status == "needs_input"


def test_authoring_assessment_is_frozen():
    assessment = AuthoringAssessment(status="supported", facts=(), issues=(), questions=())
    with pytest.raises(AttributeError):
        assessment.status = "blocked"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Stepwise authoring: Brief DTO tests
# ---------------------------------------------------------------------------


def test_brief_status_replaces_review_status_for_stepwise_authoring() -> None:
    from marivo.semantic.dtos import BriefStatus

    assert set(get_args(BriefStatus)) == {"sufficient", "needs_input", "blocked"}


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
