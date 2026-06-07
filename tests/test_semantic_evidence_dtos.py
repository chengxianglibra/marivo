from __future__ import annotations

import pytest

from marivo.semantic.evidence import (
    AssessmentIssue,
    AssessmentResult,
    AuthoringQuestion,
    BoundedProfilePolicy,
    ColumnProfile,
    FileSource,
    MetadataOnlyPolicy,
    SelectedColumnsPolicy,
    TableSource,
    _dataset_source_from_ir,
    _sample_policy_from_ir,
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
    assert _dataset_source_from_ir(ir) == src


def test_file_source_round_trips_through_ir():
    src = FileSource(path="/data/orders.parquet", format="parquet")
    ir = src.to_ir()
    assert isinstance(ir, FileSourceIR)
    assert ir.path == "/data/orders.parquet"
    assert _dataset_source_from_ir(ir) == src


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


def test_table_source_to_dict_round_trips_through_from_dict():
    from marivo.semantic.evidence_store import _dataset_source_from_dict

    src = TableSource(table="orders", database="sales_mart")
    restored = _dataset_source_from_dict(src.to_dict())
    assert restored == src


def test_file_source_to_dict_round_trips_through_from_dict():
    from marivo.semantic.evidence_store import _dataset_source_from_dict

    src = FileSource(path="/data/orders.csv", format="csv")
    restored = _dataset_source_from_dict(src.to_dict())
    assert restored == src


def test_dataset_source_from_dict_reads_old_format_with_null_fields():
    """Old DatasetSource.to_dict() included null keys for the other variant."""
    from marivo.semantic.evidence_store import _dataset_source_from_dict

    old_table_dict = {
        "kind": "table",
        "table": "orders",
        "database": None,
        "path": None,
        "format": None,
    }
    assert _dataset_source_from_dict(old_table_dict) == TableSource(table="orders")

    old_file_dict = {
        "kind": "file",
        "table": None,
        "database": None,
        "path": "/data/orders.parquet",
        "format": "parquet",
    }
    assert _dataset_source_from_dict(old_file_dict) == FileSource(
        path="/data/orders.parquet", format="parquet"
    )


def test_sample_policy_to_dict_round_trips_through_from_dict():
    from marivo.semantic.evidence_store import _sample_policy_from_dict

    for policy in (
        MetadataOnlyPolicy(timeout_seconds=30, redact=False),
        BoundedProfilePolicy(limit=100, max_profiled_columns=10),
        SelectedColumnsPolicy(limit=50, columns=("a", "b")),
    ):
        assert _sample_policy_from_dict(policy.to_dict()) == policy


def test_metadata_only_policy_round_trips_through_ir():
    policy = MetadataOnlyPolicy(timeout_seconds=30, redact=False)
    ir = policy.to_ir()
    assert isinstance(ir, MetadataOnlyPolicyIR)
    assert ir.timeout_seconds == 30
    assert ir.redact is False
    assert _sample_policy_from_ir(ir) == policy


def test_bounded_profile_policy_round_trips_through_ir():
    policy = BoundedProfilePolicy(limit=100, max_profiled_columns=10)
    ir = policy.to_ir()
    assert isinstance(ir, BoundedProfilePolicyIR)
    assert ir.limit == 100
    assert ir.max_profiled_columns == 10
    assert _sample_policy_from_ir(ir) == policy


def test_selected_columns_policy_round_trips_through_ir():
    policy = SelectedColumnsPolicy(limit=100, columns=("a", "b"))
    ir = policy.to_ir()
    assert isinstance(ir, SelectedColumnsPolicyIR)
    assert ir.columns == ("a", "b")
    assert _sample_policy_from_ir(ir) == policy


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
        evidence_refs=(),
    )
    assert derive_status((issue,), ()) == "blocked"


def test_derive_status_blocked_on_blocking_question():
    q = AuthoringQuestion(
        id="q1",
        decision_kind="metric_provenance_status",
        subject_refs=("sales.revenue",),
        prompt="p",
        reason="r",
        evidence_refs=(),
        readiness_effect="blocks",
    )
    assert derive_status((), (q,)) == "blocked"


def test_derive_status_needs_evidence_then_supported():
    needs = AssessmentIssue(
        kind="missing_evidence",
        severity="warning",
        refs=("sales.revenue",),
        message="x",
        rule_id="r1",
        evidence_refs=(),
    )
    assert derive_status((needs,), ()) == "needs_evidence"
    assert derive_status((), ()) == "supported"


def test_assessment_result_is_frozen():
    result = AssessmentResult(status="supported", facts=(), issues=(), questions=())
    with pytest.raises(AttributeError):
        result.status = "blocked"  # type: ignore[misc]
