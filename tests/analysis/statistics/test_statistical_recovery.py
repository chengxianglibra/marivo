"""Native declaration boundaries; cold kernels are owned by test_composition."""

from marivo.analysis.methods.physical import ScalarType, SourceShape, TimeShape
from marivo.analysis.methods.runs_physical import implementations
from marivo.analysis.methods.semantics import MethodKey


def test_remote_group_runs_declarations_are_exact() -> None:
    grouped = [
        item
        for item in implementations(MethodKey("time.runs"))
        if isinstance(item.key.shape, SourceShape)
        and item.key.shape.backend != "duckdb"
        and item.key.input_domains == ("group",)
    ]
    assert len(grouped) == 5
    assert {
        item.key.shape.backend for item in grouped if isinstance(item.key.shape, SourceShape)
    } == {"sqlite", "postgres", "mysql", "trino", "clickhouse"}
    for item in grouped:
        assert isinstance(item.key.shape, SourceShape)
        assert item.key.shape.form == "table" and item.key.shape.table_kind == "native"
        assert item.key.shape.time == TimeShape("instant", "us", "UTC")
        assert item.key.input_types == (ScalarType("int64"),)
        assert item.key.route == "ibis_python"
