from __future__ import annotations

import pytest

from marivo.semantic.evidence import (
    AssessmentIssue,
    AssessmentResult,
    AuthoringQuestion,
    ColumnProfile,
    DatasetSource,
    SamplePolicy,
    derive_status,
)
from marivo.semantic.ir import FileSourceIR, TableSourceIR


def test_dataset_source_round_trips_table_through_ir():
    src = DatasetSource(kind="table", table="orders", database="sales_mart")
    ir = src.to_ir()
    assert isinstance(ir, TableSourceIR)
    assert ir.table == "orders"
    assert ir.database == "sales_mart"
    assert DatasetSource.from_ir(ir) == src


def test_dataset_source_round_trips_file_through_ir():
    src = DatasetSource(kind="file", path="/data/orders.parquet", format="parquet")
    ir = src.to_ir()
    assert isinstance(ir, FileSourceIR)
    assert ir.path == "/data/orders.parquet"
    assert DatasetSource.from_ir(ir) == src


def test_dataset_source_rejects_unknown_kind_at_construction():
    with pytest.raises(ValueError, match="unsupported dataset source kind"):
        DatasetSource(  # type: ignore[arg-type]
            kind="iceberg", path="/data/orders.parquet", format="parquet"
        )
    with pytest.raises(ValueError, match="unsupported dataset source kind"):
        DatasetSource(kind="iceberg")  # type: ignore[arg-type]


def test_dataset_source_rejects_mixed_table_and_file_fields():
    with pytest.raises(ValueError, match="table source does not accept file fields"):
        DatasetSource(kind="table", table="orders", path="/data/orders.parquet")
    with pytest.raises(ValueError, match="table source does not accept file fields"):
        DatasetSource(kind="table", table="orders", format="parquet")
    with pytest.raises(ValueError, match="file source does not accept table fields"):
        DatasetSource(kind="file", path="/data/orders.parquet", format="parquet", table="orders")
    with pytest.raises(ValueError, match="file source does not accept table fields"):
        DatasetSource(
            kind="file",
            path="/data/orders.parquet",
            format="parquet",
            database="sales_mart",
        )


def test_dataset_source_to_dict_is_json_safe():
    src = DatasetSource(kind="table", table="orders", database=("a", "b"))
    assert src.to_dict() == {
        "kind": "table",
        "table": "orders",
        "database": ["a", "b"],
        "path": None,
        "format": None,
    }


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


def test_sample_policy_validates_columns_for_selected_mode():
    with pytest.raises(ValueError):
        SamplePolicy(mode="selected_columns_profile", limit=10).validate()
    with pytest.raises(ValueError):
        SamplePolicy(mode="bounded_profile", limit=10, columns=("a",)).validate()
    with pytest.raises(ValueError):
        SamplePolicy(mode="bounded_profile").validate()  # limit required for row modes
    SamplePolicy(mode="metadata_only").validate()  # no raise


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
