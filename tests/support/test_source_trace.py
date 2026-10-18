"""Transfer traces count submitted key payloads, excluding schema-only templates."""

from pathlib import Path

import ibis
import pytest

from marivo.analysis.core.data_types import DataColumn, ScalarDataType
from marivo.analysis.materialization import query_parameters
from marivo.datasource.adapters import PhysicalRequirement
from tests.datasource.source_cases import SourceData, source_case
from tests.support.query_keys import integer_string_keys as _keys
from tests.support.source_trace import capture_source


def test_inline_trace_counts_actual_payload_and_ignores_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = SourceData(
        "id BIGINT, tenant VARCHAR(10)",
        "(1,'a'),(2,'b')",
        "",
        [{"id": 1, "tenant": "a"}, {"id": 2, "tenant": "b"}],
    )
    with source_case("duckdb", "ordinary-table", tmp_path, monkeypatch, data) as case:
        trace = capture_source(monkeypatch, measure_cost=True)
        bound = case.session.bind(case.source, source_identity="traced_keys")
        qualified = case.session.qualify(
            bound, PhysicalRequirement("query.source_read", 1, frozenset({"scan", "join"}))
        )
        parameter = ibis.table({"id": "int64", "tenant": "string"}, name="selected")
        expression = bound.relation.semi_join(
            parameter,
            [
                bound.relation.id.identical_to(parameter.id),
                bound.relation.tenant.identical_to(parameter.tenant),
            ],
        )
        columns = (
            DataColumn("id", "id", ScalarDataType("int64"), False),
            DataColumn("tenant", "tenant", ScalarDataType("string"), True),
        )
        for purpose, rows in (("query.prepare", ()), ("query.source_read", ((1, "a"),))):
            actual = query_parameters.instantiate(
                expression, "selected", columns, _keys(rows), provider="mysql"
            )
            compiled = case.session.compile(
                qualified, actual, purpose=purpose, expected_schema=actual.schema().to_pyarrow()
            )
            if purpose != "query.prepare":
                assert len(case.session.execute(compiled)) == 1
        assert trace.uploaded_key_rows == [1]
        assert len(trace.uploaded_key_bytes) == 1
        assert trace.uploaded_key_bytes[0] > 0
        assert trace.returned_rows == [1]
        assert len(trace.returned_pandas_bytes) == len(trace.returned_arrow_bytes) == 1
        assert len(trace.source_seconds) == 1
