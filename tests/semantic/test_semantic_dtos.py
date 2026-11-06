from __future__ import annotations

from typing import get_args, get_type_hints

import pytest

import marivo.datasource as md
from marivo.datasource.ir import ParquetSourceIR
from marivo.semantic.dtos import (
    AssessmentIssue,
    AuthoringAssessment,
    AuthoringSourceInput,
    TableSource,
    derive_status,
)


def test_file_source_parquet_dict_round_trips_through_semantic_ir_parser() -> None:
    from marivo.semantic.ir import source_from_dict

    src = ParquetSourceIR(path="/data/orders.parquet", hive_partitioning=True)

    restored = source_from_dict(src.to_dict())

    assert isinstance(restored, ParquetSourceIR)
    assert restored.path == "/data/orders.parquet"
    assert restored.hive_partitioning is True


def test_parameterized_json_source_round_trips_through_semantic_ir_parser() -> None:
    from marivo.semantic.ir import source_from_dict

    src = md.json(
        "https://api.example/query",
        columns={"value": "value"},
        query_params={"start": md.source_param("start"), "step": "60s"},
    )

    restored = source_from_dict(src.to_dict())

    assert restored == src


def test_post_json_source_round_trips_through_semantic_ir_parser() -> None:
    from marivo.semantic.ir import source_from_dict

    src = md.json(
        "https://api.example/graphql",
        columns={"name": "name"},
        method="POST",
        body={
            "query": "{ items { name } }",
            "variables": {"page": md.source_param("page_num")},
        },
        records_path="$.data.items",
    )

    assert source_from_dict(src.to_dict()) == src


def test_authoring_source_input_to_dict_is_json_safe() -> None:
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


def test_authoring_source_role_is_finite_public_vocabulary() -> None:
    from marivo.semantic.dtos import AuthoringSourceRole

    assert get_args(AuthoringSourceRole) == ("primary", "from", "to", "component")
    assert get_type_hints(AuthoringSourceInput)["role"] == AuthoringSourceRole


def test_derive_status_blocked_on_blocker_issue() -> None:
    issue = AssessmentIssue(
        kind="missing_column",
        severity="blocker",
        refs=("sales.revenue",),
        message="x",
        rule_id="r1",
    )
    assert derive_status((issue,)) == "blocked"


def test_derive_status_needs_input_then_supported() -> None:
    needs = AssessmentIssue(
        kind="missing_evidence",
        severity="warning",
        refs=("sales.revenue",),
        message="x",
        rule_id="r1",
    )
    assert derive_status((needs,)) == "needs_input"
    assert derive_status(()) == "supported"


def test_authoring_assessment_is_frozen() -> None:
    assessment = AuthoringAssessment(status="supported", issues=())
    with pytest.raises(AttributeError):
        assessment.status = "blocked"  # type: ignore[misc]
