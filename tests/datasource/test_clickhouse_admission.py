"""Metadata-only topology and decoded-carrier qualification."""

from unittest.mock import Mock

import pytest
from ibis.backends import BaseBackend

from marivo.datasource.engines import clickhouse as ch
from marivo.datasource.ir import TableSourceIR
from tests.datasource.source_cases import datasource


@pytest.mark.parametrize(
    "engine,expected",
    [
        ("MergeTree", None),
        ("ReplacingMergeTree", None),
        ("Distributed", None),
        ("View", "engine=View"),
        ("", "metadata is unavailable"),
    ],
)
def test_clickhouse_join_topology(
    monkeypatch: pytest.MonkeyPatch, engine: str, expected: str | None
) -> None:
    rows = Mock(return_value=({"engine": engine},))
    monkeypatch.setattr(ch, "_clickhouse_rows", rows)
    issue = ch.table_join_issue(
        Mock(spec=BaseBackend), datasource("clickhouse", {}), TableSourceIR("events", "db")
    )
    if expected is None:
        assert issue is None
    else:
        assert issue is not None and expected in issue
    assert rows.call_count == 1


def test_enum_equality_admission_does_not_qualify_native_string_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ch,
        "_clickhouse_rows",
        Mock(
            return_value=tuple(
                {"name": name, "type": dtype}
                for name, dtype in (
                    ("plain", "String"),
                    ("low", "LowCardinality(String)"),
                    ("enum8", "Enum8('z' = 1, 'a' = 2)"),
                    ("enum16", "Nullable(Enum16('z' = 1, 'a' = 2))"),
                    ("fixed", "FixedString(4)"),
                )
            )
        ),
    )
    backend, ds, source = (
        Mock(spec=BaseBackend),
        datasource("clickhouse", {}),
        TableSourceIR("events", "db"),
    )
    assert ch.decoded_string_columns(backend, ds, source) == {"plain", "low"}
    assert ch.decoded_string_columns(backend, ds, source, enums=True) == {
        "plain",
        "low",
        "enum8",
        "enum16",
    }


def test_invisible_table_metadata_does_not_qualify(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ch, "_clickhouse_rows", Mock(side_effect=PermissionError("system.tables denied"))
    )
    with pytest.raises(PermissionError, match=r"system\.tables"):
        ch.table_join_issue(
            Mock(spec=BaseBackend), datasource("clickhouse", {}), TableSourceIR("events", "db")
        )


def test_distributed_compiler_broadcasts_subqueries_without_mutating_local_compiler() -> None:
    import ibis
    from ibis.backends.clickhouse import Backend

    from marivo.datasource.engines._aggregate_compilers import (
        compile_distributed_query,
        configure_aggregate_compiler,
    )

    backend = Backend()
    configure_aggregate_compiler(backend)
    left = ibis.table({"id": "int64"}, name="left")
    right = ibis.table({"id": "int64"}, name="right")
    query = left.join(right, left.id == right.id).filter(left.id.isin(right.id))
    sql = compile_distributed_query(query, {})
    assert "GLOBAL INNER JOIN" in sql and "GLOBAL IN" in sql
    assert "GLOBAL" not in backend.compile(query, limit=None)
