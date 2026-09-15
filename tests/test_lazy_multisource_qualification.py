"""Offline compiler/decoder evidence, explicitly not remote backend acceptance."""

import duckdb
import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pyarrow as pa
import pytest
import sqlglot as sg
from ibis.backends.trino import Backend

from tests.lazy_multisource_qualification import (
    SnapshotCompiler,
    assertion_envelope,
    clickhouse_envelope_sql,
    decode_envelope,
    qualification_recipe,
)


def test_clickhouse_envelope_pins_wire_types_before_union() -> None:
    _, recipe = qualification_recipe()
    ast = sg.parse_one(clickhouse_envelope_sql(recipe), read="clickhouse")
    names = assertion_envelope(recipe).schema().names
    branches = [
        select for select in ast.find_all(sg.exp.Select) if tuple(select.named_selects) == names
    ]
    assert len(branches) == 3
    for branch in branches:
        fields = {expression.alias: expression.unalias() for expression in branch.expressions}
        assert all(isinstance(expression, sg.exp.Cast) for expression in fields.values())
        assert fields["__q_violations"].args["to"].sql(dialect="clickhouse") == "Nullable(Int64)"
        assert fields["order_count"].args["to"].sql(dialect="clickhouse") == "Nullable(Int64)"
    assert clickhouse_envelope_sql(recipe) == ast.sql(dialect="clickhouse")


def _evaluate(
    source: ir.Table, expression: ir.Table, values: tuple[tuple[int | None, float | None], ...]
) -> pa.Table:
    rows = [{"id": identity, "amount": value} for identity, value in values]
    arrow = pa.Table.from_pylist(rows, schema=source.schema().to_pyarrow())
    with duckdb.connect(config={"threads": 1}) as connection:
        connection.register("seed", arrow)
        connection.execute("CREATE SCHEMA qualification")
        connection.execute("CREATE TABLE qualification.orders AS SELECT * FROM seed")
        return connection.execute(str(ibis.to_sql(expression, dialect="duckdb"))).to_arrow_table()


@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("snapshot", [7, 1234567890123456789])
def test_snapshot_visitor_pins_every_physical_scan_without_connecting(
    bound: bool, snapshot: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Offline qualification opened a connection")

    monkeypatch.setattr(Backend, "do_connect", forbidden)
    source, recipe = qualification_recipe(catalog="iceberg")
    if bound:
        source = ops.DatabaseTable(
            name="orders",
            schema=source.schema(),
            source=Backend(),
            namespace=ops.Namespace(catalog="iceberg", database="qualification"),
        ).to_expr()
        # A bound physical leaf exercises the same visitor seam used after schema lookup.
        expression = source.join(source.view(), "id").select(source.id).limit(3)
    else:
        expression = recipe.expression
    compiler = SnapshotCompiler(source, snapshot)
    sql = compiler.statement(expression)
    ast = sg.parse_one(sql, read="trino")
    scans = [table for table in ast.find_all(sg.exp.Table) if table.name == "orders"]
    assert scans
    for scan in scans:
        assert (scan.catalog, scan.db) == ("iceberg", "qualification")
        version = scan.args["version"]
        assert isinstance(version, sg.exp.Version)
        assert version.args["kind"] == "AS OF"
        assert version.expression == sg.exp.Cast(
            this=sg.exp.Literal.number(snapshot), to=sg.exp.DataType.build("BIGINT")
        )
    assert compiler.statement(expression) == sql
    assert "FOR VERSION AS OF" not in str(ibis.to_sql(expression, dialect="trino"))
    if not bound:
        for check in recipe.validations:
            check_sql = compiler.statement(check.expression)
            assert f"FOR VERSION AS OF CAST({snapshot} AS BIGINT)" in check_sql


@pytest.mark.parametrize("snapshot", [0, -1, 2**63, True])
def test_snapshot_probe_rejects_invalid_ids(snapshot: int) -> None:
    source, _ = qualification_recipe()
    with pytest.raises(ValueError, match="BIGINT"):
        SnapshotCompiler(source, snapshot)


def test_snapshot_probe_rejects_a_second_physical_source() -> None:
    source, _ = qualification_recipe(catalog="iceberg")
    other = ibis.table(source.schema(), name="other", catalog="iceberg", database="qualification")
    with pytest.raises(ValueError, match="second physical source"):
        SnapshotCompiler(source, 123).statement(source.union(other))


def test_envelope_uses_real_sum_count_checks_and_retained_parts() -> None:
    source, recipe = qualification_recipe()
    assert tuple(check.name for check in recipe.validations) == (
        "sales.orders.identity_non_null",
        "sales.orders.source_row_unique",
    )
    envelope = assertion_envelope(recipe)
    ast = sg.parse_one(str(ibis.to_sql(envelope, dialect="clickhouse")), read="clickhouse")
    assert len(tuple(ast.find_all(sg.exp.Union))) == 2
    table = _evaluate(source, envelope, ((1, 10.0), (2, 20.0), (3, -5.0)))
    decoded = decode_envelope(table, recipe)
    assert decoded.primary.to_pylist() == [{"revenue": 25.0, "order_count": 3}]
    assert len(decoded.parts) == 2
    assert [part.num_rows for _, part in decoded.parts] == [1, 1]
    assert all(name.startswith("__mv_") for _, part in decoded.parts for name in part.column_names)
    assert not any(name.startswith("__q_") for name in decoded.primary.column_names)


def test_envelope_keeps_non_null_count_separate_from_population_count() -> None:
    source, recipe = qualification_recipe()
    table = _evaluate(source, assertion_envelope(recipe), ((1, 10.0), (2, None), (3, 20.0)))
    decoded = decode_envelope(table, recipe)
    assert decoded.primary.to_pylist() == [{"revenue": 30.0, "order_count": 2}]
    assert [list(part.to_pylist()[0].values()) for _, part in decoded.parts] == [
        [30.0, 2, 3],
        [2, 3],
    ]


@pytest.mark.parametrize("empty_source", [False, True])
def test_empty_primary_still_contains_all_assertions(empty_source: bool) -> None:
    source, recipe = qualification_recipe()
    envelope = assertion_envelope(recipe, empty_primary=True)
    table = _evaluate(source, envelope, () if empty_source else ((1, 10.0),))
    assert table.num_rows == 2
    assert decode_envelope(table, recipe).primary.num_rows == 0


@pytest.mark.parametrize("empty_primary", [False, True])
@pytest.mark.parametrize("values", [((1, -10.0), (1, -20.0)), ((None, -10.0),)])
def test_filtered_or_empty_output_cannot_hide_invalid_source(
    empty_primary: bool, values: tuple[tuple[int | None, float], ...]
) -> None:
    source, recipe = qualification_recipe(positive_only=True)
    table = _evaluate(source, assertion_envelope(recipe, empty_primary=empty_primary), values)
    with pytest.raises(ValueError, match="failing source assertion"):
        decode_envelope(table, recipe)


@pytest.mark.parametrize(
    "damage", ["missing", "duplicate", "kind", "ordinal", "payload", "primary"]
)
def test_decoder_fails_closed_on_malformed_envelopes(damage: str) -> None:
    source, recipe = qualification_recipe()
    table = _evaluate(source, assertion_envelope(recipe), ((1, 10.0),))
    rows = table.to_pylist()
    primary = next(row for row in rows if row["__q_kind"] == "primary")
    assertion = next(row for row in rows if row["__q_kind"] == "assertion")
    if damage == "missing":
        rows.remove(assertion)
    elif damage == "duplicate":
        rows.append(assertion.copy())
    elif damage == "kind":
        assertion["__q_kind"] = "unknown"
    elif damage == "ordinal":
        assertion["__q_check"] = 99
    elif damage == "payload":
        assertion["revenue"] = 10.0
    else:
        primary["__q_violations"] = 0
    with pytest.raises(ValueError):
        decode_envelope(pa.Table.from_pylist(rows, schema=table.schema), recipe)
