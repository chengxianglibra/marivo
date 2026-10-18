"""MySQL bounded complete-key payloads retain native grouping and exact tuples."""

from dataclasses import replace
from datetime import date
from pathlib import Path

import ibis
import ibis.expr.operations as ops
import pandas as pd
import pytest

from marivo.analysis.core.data_types import DataColumn, ScalarDataType
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.mysql_key_parameters import membership
from marivo.analysis.materialization.query_parameters import instantiate
from marivo.datasource.adapters import PhysicalRequirement
from marivo.datasource.errors import DatasourceSourceCapabilityError
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.support.query_keys import integer_string_keys as _keys

_COLUMNS = (
    DataColumn("key.0", "id", ScalarDataType("int64"), False),
    DataColumn("key.1", "tenant", ScalarDataType("string"), True),
)


def test_mysql_complete_key_payload_has_bounded_expression_size() -> None:
    table = ibis.table({"id": "int64", "tenant": "string"}, name="facts")
    sizes = []
    for count in (10, 1_000, 10_000):
        predicate = membership(
            _COLUMNS,
            tuple((2**53 + index, "a" if index % 2 else None) for index in range(count)),
            {"key.0": table.id, "key.1": table.tenant},
        )
        assert predicate is not None
        result = table.filter(predicate)
        sql = ibis.to_sql(result, dialect="mysql")
        assert "JSON_EXTRACT" in sql
        assert "UNION" not in sql
        assert " OR " not in sql
        sizes.append(len(result.op().find(ops.Node)))
    assert sizes[0] == sizes[1] == sizes[2]
    with pytest.raises(ValueError, match="65535 encoded bytes"):
        membership(_COLUMNS, ((1, "a" * 32768),), {"key.0": table.id, "key.1": table.tenant})


def test_mysql_repeated_parameter_slot_keeps_all_join_constraints() -> None:
    table = ibis.table({"a": "int64", "b": "int64"}, name="facts")
    parameter = ibis.table({"id": "int64"}, name="selected")
    columns = (DataColumn("id", "id", ScalarDataType("int64"), False),)
    keys = StageOutput.from_frame(pd.DataFrame({"id": [2]}), columns, ("id",))
    expression = table.semi_join(
        parameter, [table.a.identical_to(parameter.id), table.b.identical_to(parameter.id)]
    )
    result = instantiate(expression, "selected", columns, keys, provider="mysql")
    assert "JSON_EXTRACT" not in ibis.to_sql(result, dialect="mysql")
    backend = ibis.duckdb.connect()
    try:
        backend.create_table("facts", pd.DataFrame({"a": [1, 2, 2], "b": [2, 1, 2]}))
        assert backend.execute(result).to_dict("records") == [{"a": 2, "b": 2}]
    finally:
        backend.disconnect()


@pytest.mark.parametrize("backend,profile", source_params([("mysql", "innodb-table")]))
@pytest.mark.parametrize("string_type", ("VARCHAR(100)", "CHAR(100)"))
def test_mysql_json_keys_preserve_native_complete_tuples(
    backend: str,
    profile: str,
    string_type: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    large = 2**53 + 1
    special = '雪_"\\.$'
    # Hex literals avoid fixture SQL escaping obscuring the transport assertion.
    special_sql = f"CONVERT(0x{special.encode().hex()} USING utf8mb4)"
    data = SourceData(
        f"id BIGINT, amount BIGINT, tenant {string_type} COLLATE utf8mb4_0900_bin, revision BIGINT",
        f"({large},2,'a',1),({large + 1},4,'b',2),({large + 2},8,NULL,3),"
        f"(-1,16,{special_sql},4),(0,32,'',5),({2**63 - 1},64,'a ',6),"
        f"({-(2**63)},128,'A',7)",
        "",
        [],
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        bound = case.session.bind(case.source, source_identity="mysql-json-keys")
        qualified = case.session.qualify(
            bound, PhysicalRequirement("query.source_read", 1, frozenset({"scan", "join", "group"}))
        )
        table = bound.relation
        parameter = ibis.table({"key.0": "int64", "key.1": "string"}, name="selected")
        selected = table.semi_join(
            parameter,
            [
                table.id.identical_to(parameter["key.0"]),
                table.tenant.identical_to(parameter["key.1"]),
            ],
        )
        expression = selected.aggregate(total=selected.amount.sum().fill_null(0))
        trailing = "a" if string_type.startswith("CHAR") else "a "
        for rows, expected in (
            (((large, "a"), (large + 1, "a"), (large + 2, None)), 10),
            (((-1, special), (0, ""), (2**63 - 1, trailing), (-(2**63), "A")), 240),
            ((*((index, "x") for index in range(10_000)), (large, "a")), 2),
            ((), 0),
        ):
            result = instantiate(
                expression, "selected", _COLUMNS, _keys(rows), provider="mysql", source=case.session
            )
            issued = case.session.compile(
                qualified,
                result,
                purpose="query.json_keys",
                expected_schema=result.schema().to_pyarrow(),
            )
            assert case.session.execute(issued)["total"].tolist() == [expected]
            if rows:
                assert "JSON_EXTRACT" in issued.sql
                assert "UNION" not in issued.sql
        assert case.session._key_request_limit is not None
        count = len(case.session.submissions)
        real_limit, encoding = case.session._key_request_limit
        assert real_limit > 100_000
        # Exercise the connection-specific bound without changing server state.
        case.session._key_request_limit = (128, encoding)
        with pytest.raises(ValueError, match="max_allowed_packet=128"):
            instantiate(
                expression,
                "selected",
                _COLUMNS,
                _keys(tuple((index, "abc") for index in range(100))),
                provider="mysql",
                source=case.session,
            )
        small = instantiate(
            expression,
            "selected",
            _COLUMNS,
            _keys(((large, "a"),)),
            provider="mysql",
            source=case.session,
        )
        with pytest.raises(DatasourceSourceCapabilityError, match="request bytes"):
            case.session.compile(
                qualified,
                small,
                purpose="query.json_keys",
                expected_schema=small.schema().to_pyarrow(),
            )
        assert len(case.session.submissions) == count


@pytest.mark.parametrize("backend,profile", source_params([("mysql", "innodb-table")]))
def test_mysql_json_keys_keep_nullable_integer_boolean_and_date_components(
    backend: str, profile: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = SourceData(
        "id BIGINT, amount BIGINT, enabled BOOLEAN, day DATE",
        "(1,2,TRUE,'2026-10-18'),(1,4,FALSE,'2026-10-18'),"
        "(2,8,TRUE,NULL),(NULL,16,NULL,'2026-10-19')",
        "",
        [],
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        bound = case.session.bind(case.source, source_identity="mysql-json-typed-keys")
        qualified = case.session.qualify(
            bound, PhysicalRequirement("query.source_read", 1, frozenset({"scan", "join", "group"}))
        )
        table = bound.relation
        columns = (
            DataColumn("id", "id", ScalarDataType("int64"), True),
            DataColumn("enabled", "enabled", ScalarDataType("boolean"), True),
            DataColumn("day", "day", ScalarDataType("date"), True),
        )
        parameter = ibis.table(
            {"id": "int64", "enabled": "boolean", "day": "date"}, name="selected"
        )
        selected = table.semi_join(
            parameter,
            [
                table.id.identical_to(parameter.id),
                table.enabled.cast("boolean").identical_to(parameter.enabled),
                table.day.identical_to(parameter.day),
            ],
        )
        expression = selected.aggregate(total=selected.amount.sum())
        keys = StageOutput.from_frame(
            pd.DataFrame(
                {
                    "id": pd.array([1, 2, None], dtype="Int64"),
                    "enabled": [True, True, None],
                    "day": [date(2026, 10, 18), None, date(2026, 10, 19)],
                }
            ),
            columns,
            ("id", "enabled", "day"),
        )
        result = instantiate(
            expression, "selected", columns, keys, provider="mysql", source=case.session
        )
        issued = case.session.compile(
            qualified,
            result,
            purpose="query.json_keys",
            expected_schema=result.schema().to_pyarrow(),
        )
        assert case.session.execute(issued)["total"].tolist() == [26]
        assert "JSON_EXTRACT" in issued.sql
        native = keys.table.copy()
        native["id"] = native["id"].astype("float64")
        floating_keys = StageOutput.from_frame(
            native,
            (replace(columns[0], dtype=ScalarDataType("float64")), *columns[1:]),
            keys.keys,
        )
        result = instantiate(
            expression, "selected", columns, floating_keys, provider="mysql", source=case.session
        )
        issued = case.session.compile(
            qualified,
            result,
            purpose="query.json_keys",
            expected_schema=result.schema().to_pyarrow(),
        )
        assert case.session.execute(issued)["total"].tolist() == [26]
