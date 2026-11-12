"""Trino partition metadata projection stays an Ibis expression."""

from __future__ import annotations

from itertools import islice
from pathlib import Path
from typing import Literal

import ibis
import pytest
from ibis.backends.trino import Backend

import marivo.datasource as md
from marivo.datasource.engines.trino import _partition_projection
from marivo.datasource.errors import DatasourceConnectionError
from marivo.datasource.metadata import ColumnMetadata, PartitionMetadata, TableMetadata


class _Cursor:
    _query = None

    def __init__(self, columns: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
        self.description = tuple((name,) for name in columns)
        self.rows = iter(rows)

    def fetchall(self) -> list[tuple[str, ...]]:
        return list(self.rows)

    def fetchmany(self, size: int) -> list[tuple[str, ...]]:
        return list(islice(self.rows, size))

    def close(self) -> None:
        pass


@pytest.mark.parametrize("failure", [None, "schema", "values", "connection"])
def test_trino_inspection_captures_partition_values_or_retains_base_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Literal["schema", "values", "connection"] | None,
) -> None:
    (tmp_path / "marivo.toml").write_text('[project]\nname = "trino-inspection-test"\n')
    spec = md.trino(
        name="warehouse", host="trino.invalid", catalog="hive", user=md.env("TRINO_USER")
    )
    md.register(spec, project_root=tmp_path)
    source = md.table("events", database=("hive", "iceberg"))
    columns = (
        ColumnMetadata("event_id", "bigint", False, None, 1),
        ColumnMetadata("dt", "varchar", False, None, 2),
    )
    fields = (PartitionMetadata(name="dt", type="varchar"),)
    metadata = TableMetadata(
        datasource="warehouse",
        table="events",
        database=("hive", "iceberg"),
        backend_type="trino",
        comment=None,
        columns=columns,
        partitions=fields,
        partition_state="known",
        warnings=(),
    )
    monkeypatch.setattr(
        "marivo.datasource.inspection._inspect_source_from_ir", lambda *args, **kwargs: metadata
    )
    secret = "test-credential-canary"

    class Resolver:
        def resolve(self, request: md.CredentialRequest) -> md.SecretValue:
            assert request.reference == "TRINO_USER"
            return md.SecretValue(secret)

    partition_ref = '"hive"."iceberg"."events$partitions"'

    def raw_sql(_backend: Backend, query: object) -> _Cursor:
        sql = str(query)
        if sql == f"SHOW COLUMNS FROM {partition_ref}":
            if failure == "schema":
                raise RuntimeError(f"Cannot describe partition metadata for {secret}")
            return _Cursor(("Column", "Type"), [("partition", "row(dt varchar)")])
        if f"FROM {partition_ref}" in sql:
            if failure == "values":
                raise RuntimeError(f"Cannot read partition metadata for {secret}")
            return _Cursor(("dt",), [("2026-09-27",), ("2026-09-26",)])
        pytest.fail(f"Unexpected SQL in partition inspection: {sql}")

    monkeypatch.setattr(Backend, "raw_sql", raw_sql)
    if failure == "connection":

        def fail_connection(_backend: Backend, **kwargs: object) -> None:
            raise OSError(f"Connection refused for {secret}")

        monkeypatch.setattr(Backend, "do_connect", fail_connection)
        with (
            md.credential_scope(resolver=Resolver()),
            pytest.raises(DatasourceConnectionError, match="Connection refused") as captured,
        ):
            md.inspect(spec.ref, source, workspace_dir=tmp_path)
        assert secret not in str(captured.value)
        return

    with md.credential_scope(resolver=Resolver()):
        inspection = md.inspect(spec.ref, source, workspace_dir=tmp_path)
        default = inspection.partitions()
        if failure is not None:
            ascending = inspection.partitions(order="asc")
            assert ascending.status == "incomplete"
            assert any("partition metadata" in issue for issue in ascending.issues)
            assert all(secret not in issue for issue in ascending.issues)

    assert inspection.schema == columns
    assert default.partitioning.fields == fields
    if failure is None:
        assert default.status == "complete"
        assert default.partitioning.values == ((("dt", "2026-09-27"),), (("dt", "2026-09-26"),))
    else:
        assert default.status == "incomplete"
        assert default.partitioning.value_source is None
        assert default.partitioning.values == ()
        assert not default.partitioning.values_complete
        assert not default.partitioning.truncated
        assert any("partition metadata" in warning for warning in inspection.warnings)
        assert all(secret not in warning for warning in inspection.warnings)


def test_iceberg_partition_fields_are_projected_from_nested_row() -> None:
    relation = ibis.table(
        {"partition": "struct<log_date: date, log_hour: string>", "record_count": "int64"},
        name="iceberg_partitions",
    )
    expression = _partition_projection(relation, ("log_date", "log_hour"), "desc", 101)

    assert expression.columns == ("log_date", "log_hour")
    assert str(expression.schema()["log_date"]) == "date"
    sql = ibis.duckdb.connect().compile(expression, limit=None)
    assert '"partition"."log_date"' in sql
    assert '"partition"."log_hour"' in sql
    assert "DESC" in sql
    assert "LIMIT 101" in sql


def test_non_iceberg_partition_columns_and_order_are_preserved() -> None:
    relation = ibis.table({"dt": "date", "hour": "string"}, name="hive_partitions")
    expression = _partition_projection(relation, ("dt", "hour"), "asc", 2)

    assert expression.columns == ("dt", "hour")
    sql = ibis.duckdb.connect().compile(expression, limit=None)
    assert "ASC" in sql
    assert "LIMIT 2" in sql
