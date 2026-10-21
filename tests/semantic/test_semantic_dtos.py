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


def test_table_source_is_shared_datasource_ir_type():
    from marivo.datasource.ir import TableSourceIR

    source = TableSource(table="orders", database="warehouse")

    assert isinstance(source, TableSourceIR)
    assert source.to_dict() == {
        "kind": "table",
        "table": "orders",
        "database": "warehouse",
    }


def test_parquet_source_is_shared_datasource_ir_type():
    from marivo.datasource.ir import ParquetSourceIR

    source = ParquetSourceIR(path="orders.parquet")

    assert isinstance(source, ParquetSourceIR)
    assert source.to_dict() == {
        "kind": "parquet",
        "path": "orders.parquet",
        "hive_partitioning": False,
        "columns": None,
    }


def test_csv_source_is_shared_datasource_ir_type():
    from marivo.datasource.ir import CsvSourceIR

    source = CsvSourceIR(
        path="orders.csv",
        columns=(("order_id", "order_id"), ("amount", "amount")),
        delimiter="|",
    )

    assert isinstance(source, CsvSourceIR)
    assert source.to_dict() == {
        "kind": "csv",
        "path": "orders.csv",
        "columns": {"amount": "amount", "order_id": "order_id"},
        "header": True,
        "delimiter": "|",
    }


def test_file_source_parquet_dict_round_trips_through_semantic_ir_parser():
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


def test_dataset_source_cannot_mix_table_and_file_fields():
    with pytest.raises(TypeError):
        TableSource(table="orders", path="/data/orders.parquet")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        ParquetSourceIR(path="/data/orders.parquet", table="orders")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        ParquetSourceIR(path="/data/orders.parquet", database="sales_mart")  # type: ignore[call-arg]


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
    assert derive_status((issue,)) == "blocked"


def test_derive_status_needs_input_then_supported():
    needs = AssessmentIssue(
        kind="missing_evidence",
        severity="warning",
        refs=("sales.revenue",),
        message="x",
        rule_id="r1",
    )
    assert derive_status((needs,)) == "needs_input"
    assert derive_status(()) == "supported"


def test_authoring_assessment_is_frozen():
    assessment = AuthoringAssessment(status="supported", issues=())
    with pytest.raises(AttributeError):
        assessment.status = "blocked"  # type: ignore[misc]
