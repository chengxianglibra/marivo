"""Typed datasource source serialization and validation."""

from __future__ import annotations

import pytest

import marivo.datasource as md
from marivo.datasource.ir import CsvSourceIR, ParquetSourceIR, TableSourceIR
from marivo.semantic.ir import source_from_dict


def test_typed_source_builders_have_no_options_bag() -> None:
    table = md.table("orders", database=("warehouse", "sales"))
    parquet = md.parquet("/tmp/orders/*.parquet", hive_partitioning=True, columns=("id", "amount"))
    csv = md.csv(
        "/tmp/orders.csv",
        columns={"id": "id", "amount": "amount"},
        header=False,
        delimiter="|",
    )

    assert isinstance(table, TableSourceIR)
    assert table.to_dict() == {
        "kind": "table",
        "table": "orders",
        "database": ["warehouse", "sales"],
    }
    assert isinstance(parquet, ParquetSourceIR)
    assert parquet.to_dict() == {
        "kind": "parquet",
        "path": "/tmp/orders/*.parquet",
        "hive_partitioning": True,
        "columns": ["id", "amount"],
    }
    assert isinstance(csv, CsvSourceIR)
    assert csv.to_dict() == {
        "kind": "csv",
        "path": "/tmp/orders.csv",
        "columns": {"amount": "amount", "id": "id"},
        "header": False,
        "delimiter": "|",
    }


def test_json_source_ir_has_minimal_json_shape() -> None:
    from marivo.datasource.ir import JsonSourceIR

    source = JsonSourceIR(
        path="data/events/*.json",
        columns=(("event_id", "event_id"),),
        format="newline_delimited",
    )

    assert source.kind == "json"
    assert source.path == "data/events/*.json"
    assert source.format == "newline_delimited"
    assert source.to_ir() is source
    assert source.to_dict() == {
        "kind": "json",
        "path": "data/events/*.json",
        "columns": {"event_id": "event_id"},
        "format": "newline_delimited",
        "records_path": None,
        "query_params": {},
        "method": "GET",
        "body": None,
        "body_params": [],
    }


def test_json_source_ir_rejects_empty_path_bad_format_and_bad_kind() -> None:
    from marivo.datasource.ir import JsonSourceIR

    with pytest.raises(ValueError, match=r"JsonSourceIR\.path"):
        JsonSourceIR(path="", columns=(("event_id", "event_id"),))
    with pytest.raises(TypeError, match=r"JsonSourceIR\.format"):
        JsonSourceIR(path="events.json", columns=(("event_id", "event_id"),), format="ndjson")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"JsonSourceIR\.records_path"):
        JsonSourceIR(path="events.json", columns=(("event_id", "event_id"),), records_path=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=r"JsonSourceIR\.records_path"):
        JsonSourceIR(path="events.json", columns=(("event_id", "event_id"),), records_path="data")
    with pytest.raises(ValueError, match=r"JsonSourceIR\.kind"):
        JsonSourceIR(path="events.json", columns=(("event_id", "event_id"),), kind="csv")  # type: ignore[arg-type]


def test_source_value_objects_reject_invalid_payloads() -> None:
    with pytest.raises(TypeError, match=r"TableSourceIR\.table"):
        TableSourceIR(table=42)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"TableSourceIR\.database"):
        TableSourceIR(table="orders", database=("warehouse", 1))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"ParquetSourceIR\.columns"):
        ParquetSourceIR(path="/tmp/orders.parquet", columns=("id", 1))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"CsvSourceIR\.header"):
        CsvSourceIR(path="/tmp/orders.csv", columns=(("order_id", "order_id"),), header="yes")  # type: ignore[arg-type]


def test_source_builders_reject_invalid_payloads() -> None:
    with pytest.raises(TypeError, match=r"TableSourceIR\.table"):
        md.table(42)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"ParquetSourceIR\.hive_partitioning"):
        md.parquet("/tmp/orders.parquet", hive_partitioning="yes")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"ParquetSourceIR\.columns"):
        md.parquet("/tmp/orders.parquet", columns=("id", 1))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"CsvSourceIR\.delimiter"):
        md.csv("/tmp/orders.csv", columns={"order_id": "order_id"}, delimiter=123)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"JsonSourceIR\.format"):
        md.json("/tmp/events.json", columns={"event_id": "event_id"}, format="ndjson")  # type: ignore[arg-type]


def test_source_from_dict_reads_projected_file_variants() -> None:
    assert source_from_dict({"kind": "parquet", "path": "/tmp/orders.parquet"}).to_dict() == {
        "kind": "parquet",
        "path": "/tmp/orders.parquet",
        "hive_partitioning": False,
        "columns": None,
    }
    assert source_from_dict(
        {
            "kind": "csv",
            "path": "/tmp/orders.csv",
            "columns": {"order_id": "order_id"},
            "delimiter": "\t",
        }
    ).to_dict() == {
        "kind": "csv",
        "path": "/tmp/orders.csv",
        "columns": {"order_id": "order_id"},
        "header": True,
        "delimiter": "\t",
    }


def test_source_from_dict_reads_json_variant() -> None:
    from marivo.datasource.ir import JsonSourceIR

    restored = source_from_dict(
        {
            "kind": "json",
            "path": "data/events/*.json",
            "columns": {"event_id": "event_id"},
            "format": "array",
            "records_path": "$.result.items",
        }
    )

    assert restored == JsonSourceIR(
        path="data/events/*.json",
        columns=(("event_id", "event_id"),),
        format="array",
        records_path="$.result.items",
    )
    assert restored.to_dict() == {
        "kind": "json",
        "path": "data/events/*.json",
        "columns": {"event_id": "event_id"},
        "format": "array",
        "records_path": "$.result.items",
        "query_params": {},
        "method": "GET",
        "body": None,
        "body_params": [],
    }
