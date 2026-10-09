"""Independent examples for the physical Cell carrier and public decoding."""

import ibis
import ibis.expr.operations as ops
import pyarrow as pa
import pytest

from marivo.analysis.compiler.cell_lowering import lower_cells, state_value
from marivo.analysis.core.cell_encoding import (
    CellBook,
    CellFields,
    EncodedCell,
    KnownCell,
    ValidityCell,
)
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.cell_arrow import defined_mask, expand, read_cell, remap


def test_four_states_and_defined_zero_have_distinct_codes() -> None:
    book = CellBook.from_reasons(
        (
            ("unknown", ("incomplete",)),
            ("undefined", ("zero_denominator",)),
            ("null", ("source_null",)),
        )
    )
    cell = EncodedCell(CellFields("value", "cell_tag", "cell_reason"), book, "cell_state")
    table = pa.table(
        {
            "value": pa.array([0, None, None, None], type=pa.int64()),
            "cell_state": pa.array([0, 5, 10, 15], type=pa.int16()),
        }
    )
    assert defined_mask(table, cell).to_pylist() == [True, False, False, False]
    assert expand(table, (cell,)).to_pylist() == [
        {"value": 0, "cell_tag": "defined", "cell_reason": None},
        {"value": None, "cell_tag": "null", "cell_reason": "source_null"},
        {"value": None, "cell_tag": "undefined", "cell_reason": "zero_denominator"},
        {"value": None, "cell_tag": "unknown", "cell_reason": "incomplete"},
    ]
    assert read_cell(table, cell, 1) == (None, "null", "source_null")


def test_validity_and_known_encodings_need_no_state_column() -> None:
    fields = CellFields("value", "cell_tag", "cell_reason")
    book = CellBook.from_reasons((("undefined", ("empty_mean",)),))
    nullable = ValidityCell(fields, book, 6)
    assert expand(pa.table({"value": [1, None]}), (nullable,))["cell_tag"].to_pylist() == [
        "defined",
        "undefined",
    ]
    known = KnownCell(fields, CellBook(()), 0)
    empty = pa.table({"value": pa.array([], type=pa.int64())})
    assert expand(empty, (known,)).schema.names == ["value", "cell_tag", "cell_reason"]


@pytest.mark.parametrize("code", [0, 6])
def test_known_state_preserves_rows_and_nulls_without_boolean_cast(code: int) -> None:
    fields = CellFields("value", "cell_tag", "cell_reason")
    book = CellBook.from_reasons((("undefined", ("empty_mean",)),))
    cell = KnownCell(fields, book, code)
    backend = ibis.duckdb.connect()
    try:
        source = backend.create_table("known_cells", pa.table({"value": [0, None, 2]}))
        expression = source.select(state=state_value(source, cell))
        assert backend.to_pyarrow(expression)["state"].to_pylist() == [code, code, code]
        assert backend.to_pyarrow(expression.limit(0)).num_rows == 0
        assert not any(cast.arg.dtype.is_boolean() for cast in expression.op().find(ops.Cast))
    finally:
        backend.disconnect()


def test_reason_dictionaries_are_rebound_without_changing_meaning() -> None:
    source = CellBook.from_reasons((("undefined", ("zero_denominator",)),))
    target = CellBook.from_reasons((("undefined", ("missing_side", "zero_denominator")),))
    values = pa.chunked_array([pa.array([0, 6], type=pa.int16())])
    assert remap(values, source, target).to_pylist() == [0, 10]
    with pytest.raises(AnalysisError):
        remap(pa.chunked_array([pa.array([7], type=pa.int16())]), source, target)


@pytest.mark.parametrize("code", [-1, 1, 4, 7, 32768])
def test_invalid_codes_do_not_become_unknown(code: int) -> None:
    book = CellBook.from_reasons((("null", ("source_null",)),))
    with pytest.raises(AnalysisError):
        book.decode(code)


def test_source_validity_encoding_omits_string_cases_and_preserves_filter() -> None:
    backend = ibis.duckdb.connect()
    try:
        source = backend.create_table("cell_values", pa.table({"x": [0, None, 2]}))
        logical = source.select(
            value=source.x,
            cell_tag=source.x.isnull().ifelse("null", "defined"),
            cell_reason=source.x.isnull().ifelse("source_null", ibis.null().cast("string")),
        )
        compact = lower_cells(logical)
        assert isinstance(compact.cells[0], ValidityCell)
        assert "CASE" not in str(ibis.to_sql(compact.expression, dialect="duckdb"))
        physical = backend.to_pyarrow(compact.expression)
        assert expand(physical, compact.cells).to_pylist() == [
            {"value": 0, "cell_tag": "defined", "cell_reason": None},
            {"value": None, "cell_tag": "null", "cell_reason": "source_null"},
            {"value": 2, "cell_tag": "defined", "cell_reason": None},
        ]
        selected = lower_cells(logical.filter(logical.cell_tag == "defined"))
        assert backend.to_pyarrow(selected.expression)["value"].to_pylist() == [0, 2]
    finally:
        backend.disconnect()


@pytest.mark.parametrize(
    "dialect", ["duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse"]
)
def test_static_native_sql_contains_only_the_required_carrier(dialect: str) -> None:
    source = ibis.table({"x": "int64"}, name="cells")
    logical = source.select(
        value=source.x,
        cell_tag=source.x.isnull().ifelse("null", "defined"),
        cell_reason=source.x.isnull().ifelse("source_null", ibis.null().cast("string")),
    )
    compact = lower_cells(logical.filter(logical.cell_tag == "defined"))
    sql = str(ibis.to_sql(compact.expression, dialect=dialect))
    assert "CASE" not in sql
    assert "cell_tag" not in sql and "cell_reason" not in sql
    assert compact.expression.columns == ("value",)


def test_union_rebinds_different_books_and_preserves_reason_null_predicates() -> None:
    backend = ibis.duckdb.connect()
    try:
        source = backend.create_table("union_cells", pa.table({"x": [0, None]}))

        def branch(reason: str) -> ibis.expr.types.Table:
            return source.select(
                value=source.x,
                cell_tag=source.x.isnull().ifelse("undefined", "defined"),
                cell_reason=source.x.isnull().ifelse(reason, ibis.null().cast("string")),
            )

        logical = branch("a").union(branch("b"), distinct=False)
        compact = lower_cells(logical)
        assert isinstance(compact.cells[0], EncodedCell)
        assert expand(backend.to_pyarrow(compact.expression), compact.cells)[
            "cell_reason"
        ].to_pylist() == [None, "a", None, "b"]
        selected = lower_cells(logical.filter(logical.cell_reason != "a"))
        assert expand(backend.to_pyarrow(selected.expression), selected.cells)[
            "cell_reason"
        ].to_pylist() == ["b"]
        defined = lower_cells(logical.filter(logical.cell_reason.isnull()))
        assert backend.to_pyarrow(defined.expression)["value"].to_pylist() == [0, 0]
    finally:
        backend.disconnect()


def test_optional_endpoint_absence_stays_outside_the_four_cell_states() -> None:
    from marivo.analysis.materialization.cell_arrow import (
        annotate,
        column,
        from_rows,
        rows,
        validate,
    )

    book = CellBook.from_reasons((("null", ("source_null",)),))
    cell = EncodedCell(CellFields("value", "cell_tag", "cell_reason"), book, "cell_state", True)
    table = annotate(
        pa.table(
            {
                "value": pa.array([None, None, 0], pa.int64()),
                "cell_state": pa.array([-1, 5, 0], pa.int16()),
            }
        ),
        (cell,),
        ("value", "cell_tag", "cell_reason"),
    )
    validate(table)
    assert column(table, "cell_tag").to_pylist() == [None, "null", "defined"]
    assert from_rows(rows(table), table.schema).equals(table)
    assert expand(table, (cell,))["cell_reason"].to_pylist() == [None, "source_null", None]


@pytest.mark.parametrize("value,code", [(1, 5), (None, 0), (None, 1)])
def test_state_validity_and_dictionary_corruption_are_rejected(
    value: int | None, code: int
) -> None:
    from marivo.analysis.materialization.cell_arrow import annotate, validate

    cell = EncodedCell(
        CellFields("value", "cell_tag", "cell_reason"),
        CellBook.from_reasons((("null", ("source_null",)),)),
        "cell_state",
    )
    table = annotate(
        pa.table(
            {"value": pa.array([value], pa.int64()), "cell_state": pa.array([code], pa.int16())}
        ),
        (cell,),
        ("value", "cell_tag", "cell_reason"),
    )
    with pytest.raises(AnalysisError):
        validate(table)
    with pytest.raises(AnalysisError):
        expand(table, (cell,))


def test_maximum_code_and_chunked_empty_buffers_round_trip() -> None:
    book = CellBook.from_reasons((("unknown", tuple(f"r{i:04}" for i in range(8191))),))
    cell = EncodedCell(CellFields("value", "cell_tag", "cell_reason"), book, "cell_state")
    assert book.code("unknown", "r8190") == 32767
    table = pa.table(
        {
            "value": pa.chunked_array([pa.array([], pa.int64()), pa.array([None], pa.int64())]),
            "cell_state": pa.chunked_array(
                [pa.array([], pa.int16()), pa.array([32767], pa.int16())]
            ),
        }
    )
    assert expand(table, (cell,))["cell_reason"].to_pylist() == ["r8190"]
    with pytest.raises(AnalysisError):
        CellBook.from_reasons((("unknown", tuple(f"r{i:04}" for i in range(8192))),))
    with pytest.raises(AnalysisError):
        remap(pa.chunked_array([pa.array([5], pa.int16())]), book, book)


def test_outer_join_known_null_and_validity_distinguish_absent_endpoints() -> None:
    backend = ibis.duckdb.connect()
    try:
        left = backend.create_table("outer_keys", pa.table({"key": [1, 2]}))
        source = backend.create_table(
            "outer_cells", pa.table({"key": [1], "x": pa.array([None], pa.int64())})
        )
        for known in (True, False):
            right = source.select(
                key=source.key,
                value=source.x,
                cell_tag=ibis.literal("null")
                if known
                else source.x.isnull().ifelse("null", "defined"),
                cell_reason=ibis.literal("source_null")
                if known
                else source.x.isnull().ifelse("source_null", ibis.null().cast("string")),
            )
            logical = left.left_join(right, "key").select(
                key=left.key,
                value=right.value,
                cell_tag=right.cell_tag,
                cell_reason=right.cell_reason,
            )
            compact = lower_cells(logical)
            assert isinstance(compact.cells[0], EncodedCell) and compact.cells[0].optional
            decoded = expand(backend.to_pyarrow(compact.expression), compact.cells)
            assert decoded["cell_tag"].to_pylist() == ["null", None]
            assert decoded["cell_reason"].to_pylist() == ["source_null", None]
    finally:
        backend.disconnect()


def test_compact_pandas_producer_still_rejects_lossy_numeric_casts() -> None:
    from dataclasses import replace

    import pandas as pd

    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.analysis.materialization.graph_exchange import from_pandas
    from tests.analysis.graph.selection_fixtures import selection_input

    _, verified = selection_input(1, parts=False)
    contract = replace(verified.result.contract, parts=())
    frame = pd.DataFrame(
        {
            "key_0": [0],
            "key_1": ["0"],
            "value": [1.5],
            "cell_tag": ["defined"],
            "cell_reason": [None],
        }
    )
    with pytest.raises(MaterializationError, match="lossy pandas-to-Arrow"):
        from_pandas(frame, contract, validate=False)


def test_prepare_policy_cannot_hide_an_invalid_source_code() -> None:
    from marivo.analysis.materialization.cell_arrow import schema_binding, validate

    backend = ibis.duckdb.connect()
    try:
        source = backend.create_table(
            "undeclared_source_cell",
            pa.table(
                {
                    "value": pa.array([None], pa.int64()),
                    "cell_tag": ["unknown"],
                    "cell_reason": ["unavailable"],
                }
            ),
        )
        reasons = (("null", ("source_null",)),)
        cell = EncodedCell(
            CellFields("value", "cell_tag", "cell_reason"),
            CellBook.from_reasons(reasons),
            "cell_state",
        )
        compact = lower_cells(source, reasons, raw_cells={source.op(): (cell,)})
        assert isinstance(compact.cells[0], EncodedCell)
        table = backend.to_pyarrow(compact.expression)
        table = table.cast(schema_binding(table.schema, compact.cells, compact.logical_columns))
        with pytest.raises(AnalysisError, match="undeclared physical Cell code"):
            validate(table)
    finally:
        backend.disconnect()


def test_binding_reuse_is_operation_scoped_and_never_certifies_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.analysis.core.cell_encoding import CellTable
    from marivo.analysis.materialization import cell_arrow

    cell = EncodedCell(CellFields("value", "cell_tag", "cell_reason"), CellBook(()), "cell_state")
    schema = cell_arrow.schema_binding(
        pa.schema([("value", pa.int64()), ("cell_state", pa.int16())]),
        (cell,),
        ("value", "cell_tag", "cell_reason"),
    )
    table = pa.Table.from_pylist([{"value": 1, "cell_state": 0}], schema=schema)
    original = cell_arrow._TABLE.validate_json
    calls = []

    def decode(payload: bytes, *, strict: bool) -> CellTable:
        calls.append(payload)
        return original(payload, strict=strict)

    monkeypatch.setattr(cell_arrow._TABLE, "validate_json", decode)
    with cell_arrow.binding_scope():
        cell_arrow.binding(schema)
        cell_arrow.validate(table)
        corrupted = table.set_column(1, schema.field(1), pa.array([-1], pa.int16()))
        with pytest.raises(AnalysisError, match="undeclared physical Cell code"):
            cell_arrow.validate(corrupted)
        assert len(calls) == 1
    with cell_arrow.binding_scope():
        cell_arrow.binding(schema)
    cell_arrow.binding(schema)
    cell_arrow.binding(schema)
    assert len(calls) == 4
