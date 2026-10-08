"""Arrow operations on compact Cells without intermediate string columns."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from decimal import Decimal
from typing import TypeVar

import pyarrow as pa
import pyarrow.compute as pc
from pydantic import TypeAdapter, ValidationError

from marivo.analysis.core.cell_encoding import (
    CellBook,
    CellEncoding,
    CellFields,
    CellTable,
    CellTag,
    EncodedCell,
    KnownCell,
    ValidityCell,
    invalid,
)

_KEY = b"marivo.analysis.cell_table"
_TABLE = TypeAdapter(CellTable)
T = TypeVar("T")
_BINDINGS: ContextVar[dict[bytes, CellTable] | None] = ContextVar("cell_bindings", default=None)


@contextmanager
def binding_scope() -> Iterator[None]:
    """Reuse immutable binding decoding only within one execution operation."""
    if _BINDINGS.get() is not None:
        yield
        return
    token = _BINDINGS.set({})
    try:
        yield
    finally:
        _BINDINGS.reset(token)


def required(value: object, expected: type[T]) -> T:
    """Narrow an Arrow scalar at a typed algorithm boundary without coercion."""
    if not isinstance(value, expected):
        invalid(f"expected {expected.__name__} scalar, received {type(value).__name__}")
    return value


def number(value: object) -> int | float | Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        invalid("expected a numeric Arrow scalar")
    return value


def binding(schema: pa.Schema, *, exact: bool = False) -> CellTable:
    if not len(schema):
        return CellTable((), ())
    payload = (schema.metadata or {}).get(_KEY)
    if payload is None:
        return CellTable((), tuple(schema.names))
    cache = _BINDINGS.get()
    value = cache.get(payload) if cache is not None else None
    if value is None:
        try:
            value = _TABLE.validate_json(payload, strict=True)
        except ValidationError:
            invalid("invalid closed Cell table binding")
        if _TABLE.dump_json(value) != payload:
            invalid("noncanonical Cell table binding")
        if cache is not None:
            cache[payload] = value
    cells = value.cells
    if exact and any(
        cell.fields.value not in schema.names
        or (isinstance(cell, EncodedCell) and cell.state not in schema.names)
        for cell in cells
    ):
        invalid("Cell binding refers to absent physical fields")
    cells = tuple(cell for cell in cells if cell.fields.value in schema.names)
    virtual = {name for cell in cells for name in (cell.fields.tag, cell.fields.reason)}
    states = {cell.state for cell in cells if isinstance(cell, EncodedCell)}
    columns = tuple(
        name for name in value.logical_columns if name in schema.names or name in virtual
    )
    columns += tuple(name for name in schema.names if name not in columns and name not in states)
    return CellTable(cells, columns)


def logical_schema(schema: pa.Schema) -> pa.Schema:
    declared = binding(schema)
    fields = {field.name: field for field in schema}
    for cell in declared.cells:
        fields[cell.fields.tag] = pa.field(cell.fields.tag, pa.string())
        fields[cell.fields.reason] = pa.field(cell.fields.reason, pa.string())
    return pa.schema([fields[name] for name in declared.logical_columns], metadata=schema.metadata)


def rows(table: pa.Table) -> list[dict[str, object]]:
    """Decode only scalar algorithm inputs, without allocating Arrow string vectors."""
    declared = binding(table.schema)
    result: list[dict[str, object]] = table.to_pylist()
    for cell in declared.cells:
        if isinstance(cell, KnownCell):
            tag, reason = cell.book.decode(cell.code)
            for row in result:
                row[cell.fields.tag], row[cell.fields.reason] = tag, reason
        elif isinstance(cell, ValidityCell):
            missing = cell.book.decode(cell.missing_code)
            for row in result:
                row[cell.fields.tag], row[cell.fields.reason] = (
                    ("defined", None) if row[cell.fields.value] is not None else missing
                )
        else:
            codes(table, cell)
            lookup: dict[int, tuple[str | None, str | None]] = {
                0: ("defined", None),
                **{
                    code: (pair.tag, pair.reason)
                    for pair, code in zip(cell.book.entries, cell.book.codes(), strict=True)
                },
            }
            if cell.optional:
                lookup[-1] = (None, None)
            for row in result:
                pair = lookup.get(required(row.pop(cell.state), int))
                if pair is None:
                    invalid("undeclared physical Cell code at scalar read")
                row[cell.fields.tag], row[cell.fields.reason] = pair
    return result


def column(table: pa.Table, name: str) -> pa.ChunkedArray:
    """Read a logical status vector at a scalar/status disclosure boundary."""
    if name in table.column_names:
        return table[name]
    for cell in binding(table.schema).cells:
        if name in (cell.fields.tag, cell.fields.reason):
            component = 0 if name == cell.fields.tag else 1
            values = codes(table, cell)
            optional = isinstance(cell, EncodedCell) and cell.optional
            admitted = (*((-1,) if optional else ()), 0, *cell.book.codes())
            if pc.any(
                pc.invert(pc.is_in(values, value_set=pa.array(admitted, pa.int16())))
            ).as_py():
                invalid("undeclared Cell code at logical field read")
            lookup: list[str | None] = [None] * (len(cell.book.entries) * 4 + 5)
            lookup[1] = "defined" if component == 0 else None
            for pair, code in zip(cell.book.entries, cell.book.codes(), strict=True):
                lookup[code + 1] = pair.tag if component == 0 else pair.reason
            return pc.take(pa.array(lookup, pa.string()), pc.add(values.cast(pa.int32()), 1))
    invalid(f"missing logical field {name!r}")


def from_rows(values: Iterable[dict[str, object]], schema: pa.Schema) -> pa.Table:
    """Build physical arrays directly from scalar algorithm outputs."""
    declared = binding(schema)
    materialized = [dict(row) for row in values]
    for cell in declared.cells:
        for row in materialized:
            reason = row[cell.fields.reason]
            if reason is not None and not isinstance(reason, str):
                invalid("producer reason must be a string or None")
            code = (
                -1
                if isinstance(cell, EncodedCell)
                and cell.optional
                and row[cell.fields.tag] is None
                and reason is None
                else cell.book.code(str(row[cell.fields.tag]), reason)
            )
            if (row[cell.fields.value] is not None) != (code == 0):
                invalid("producer Cell state disagrees with value validity")
            if isinstance(cell, KnownCell) and code != cell.code:
                invalid("producer changed a statically known Cell")
            if isinstance(cell, ValidityCell) and code not in (0, cell.missing_code):
                invalid("producer changed a validity-bound Cell")
            if isinstance(cell, EncodedCell):
                row[cell.state] = code
            row.pop(cell.fields.tag)
            row.pop(cell.fields.reason)
    return pa.Table.from_pylist(materialized, schema=schema)


def rename(table: pa.Table, names: Iterable[str]) -> pa.Table:
    names = tuple(names)
    original = tuple(table.column_names)
    declared = binding(table.schema)
    logical_names = len(names) == len(declared.logical_columns)
    mapping = dict(zip(declared.logical_columns if logical_names else original, names, strict=True))
    cells: list[CellEncoding] = []
    for cell in declared.cells:
        old = cell.fields
        # Generated triples share a value prefix; callers rename physical values.
        value = mapping[old.value]
        prefix = value.removesuffix("value")
        fields = CellFields(
            value,
            mapping.get(old.tag, prefix + "cell_tag"),
            mapping.get(old.reason, prefix + "cell_reason"),
        )
        state = prefix + "cell_state"
        cells.append(
            replace(
                cell,
                fields=fields,
                **(
                    {"state": mapping.get(cell.state, state)}
                    if isinstance(cell, EncodedCell)
                    else {}
                ),
            )
        )
        if isinstance(cell, EncodedCell):
            mapping[cell.state] = mapping.get(cell.state, state)
        mapping[old.tag], mapping[old.reason] = fields.tag, fields.reason
    return annotate(
        table.rename_columns([mapping[name] for name in original]),
        tuple(cells),
        tuple(mapping.get(name, name) for name in declared.logical_columns),
    )


def compact_schema(
    schema: pa.Schema,
    reasons: tuple[tuple[str, tuple[str, ...]], ...],
    column_reasons: tuple[tuple[tuple[str, tuple[str, ...]], ...], ...] = (),
    *,
    optional: bool = False,
) -> pa.Schema:
    """Choose a carrier from the declared policy, independently of actual rows."""
    logical = logical_schema(schema)
    previous = binding(schema)
    cells: list[CellEncoding] = []
    for tag in logical.names:
        if tag.endswith("cell_tag"):
            prefix = tag.removesuffix("cell_tag")
            value, reason = prefix + "value", prefix + "cell_reason"
        elif tag.endswith("_tag"):
            prefix = tag.removesuffix("tag")
            value, reason = prefix + "value", prefix + "reason"
        else:
            continue
        if value not in logical.names:
            value = prefix.removesuffix("__")
        if value not in logical.names or reason not in logical.names:
            continue
        owned = reasons
        if prefix.startswith("column_") and column_reasons:
            owned = column_reasons[int(prefix.removeprefix("column_").removesuffix("__"))]
        inherited = next(
            (cell.book for cell in previous.cells if cell.fields.value == value), CellBook(())
        )
        entries = set(inherited.entries) | set(CellBook.from_reasons(owned).entries)
        book = CellBook.from_reasons(tuple((p.tag, (p.reason,)) for p in entries))
        fields = CellFields(value, tag, reason)
        cell: CellEncoding = (
            EncodedCell(fields, book, prefix + "cell_state", True)
            if optional
            else KnownCell(fields, book, 0)
            if not entries
            else (
                ValidityCell(fields, book, book.code(book.entries[0].tag, book.entries[0].reason))
                if len(entries) == 1
                else EncodedCell(fields, book, prefix + "cell_state")
            )
        )
        cells.append(cell)
    removed = {name for cell in cells for name in (cell.fields.tag, cell.fields.reason)}
    physical = [field for field in logical if field.name not in removed]
    physical.extend(
        pa.field(cell.state, pa.int16(), nullable=False)
        for cell in cells
        if isinstance(cell, EncodedCell)
    )
    return schema_binding(
        pa.schema(physical, metadata=schema.metadata), tuple(cells), tuple(logical.names)
    )


def policies(table: pa.Table) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Carry frozen input reasons into a consumer's output binding."""
    entries = {pair for cell in binding(table.schema).cells for pair in cell.book.entries}
    return tuple(
        (tag, tuple(sorted(p.reason for p in entries if p.tag == tag)))
        for tag in ("null", "undefined", "unknown")
        if any(p.tag == tag for p in entries)
    )


def storage_schema(schema: pa.Schema) -> pa.Schema:
    """Keep the one frozen descriptor/receipt as the durable Cell binding owner."""
    metadata = dict(schema.metadata or {})
    metadata.pop(_KEY, None)
    return schema.with_metadata(metadata)


def annotate(
    table: pa.Table, cells: tuple[CellEncoding, ...], columns: tuple[str, ...]
) -> pa.Table:
    if not table.num_columns:
        return table
    if not cells:
        metadata = dict(table.schema.metadata or {})
        metadata.pop(_KEY, None)
        return (
            table.replace_schema_metadata(metadata)
            if _KEY in (table.schema.metadata or {})
            else table
        )
    value = CellTable(cells, columns)
    metadata = dict(table.schema.metadata or {})
    metadata[_KEY] = _TABLE.dump_json(value)
    return table.replace_schema_metadata(metadata)


def schema_binding(
    schema: pa.Schema, cells: tuple[CellEncoding, ...], columns: tuple[str, ...]
) -> pa.Schema:
    for cell in cells:
        if isinstance(cell, EncodedCell):
            index = schema.get_field_index(cell.state)
            if index < 0 or schema.field(index).type != pa.int16():
                invalid("encoded Cell requires its exact int16 state field")
            schema = schema.set(index, pa.field(cell.state, pa.int16(), nullable=False))
    metadata = dict(schema.metadata or {})
    if cells:
        metadata[_KEY] = _TABLE.dump_json(CellTable(cells, columns))
    else:
        metadata.pop(_KEY, None)
    return schema.with_metadata(metadata)


def pack_bound(table: pa.Table, declared: CellTable) -> pa.Table:
    for cell in declared.cells:
        if cell.fields.tag not in table.column_names:
            continue
        valid = table[cell.fields.value].is_valid()
        defined = pc.and_kleene(
            pc.equal(table[cell.fields.tag], "defined"), table[cell.fields.reason].is_null()
        )
        states = pc.if_else(
            pc.and_kleene(defined, valid).fill_null(False),
            pa.scalar(0, pa.int16()),
            pa.scalar(-32768, pa.int16()),
        )
        for pair in cell.book.entries:
            selected = pc.and_kleene(
                pc.and_kleene(
                    pc.equal(table[cell.fields.tag], pair.tag),
                    pc.equal(table[cell.fields.reason], pair.reason),
                ),
                pc.invert(valid),
            ).fill_null(False)
            states = pc.if_else(
                selected, pa.scalar(cell.book.code(pair.tag, pair.reason), pa.int16()), states
            )
        if isinstance(cell, EncodedCell) and cell.optional:
            absent = pc.and_(
                pc.and_(table[cell.fields.tag].is_null(), table[cell.fields.reason].is_null()),
                pc.invert(valid),
            )
            states = pc.if_else(absent, pa.scalar(-1, pa.int16()), states)
        if pc.any(pc.equal(states, -32768)).as_py():
            invalid("producer emitted an undeclared or invalid Cell state")
        if isinstance(cell, EncodedCell):
            table = table.append_column(pa.field(cell.state, pa.int16(), nullable=False), states)
        elif pc.any(pc.not_equal(states, codes(table, cell))).as_py():
            invalid("producer disagrees with its static Cell encoding")
        table = table.drop((cell.fields.tag, cell.fields.reason))
    table = annotate(table, declared.cells, declared.logical_columns)
    validate(table)
    return table


def project(table: pa.Table, columns: Iterable[str]) -> pa.Table:
    columns = tuple(columns)
    declared = binding(table.schema)
    cells = tuple(
        cell
        for cell in declared.cells
        if {cell.fields.value, cell.fields.tag, cell.fields.reason} <= set(columns)
    )
    removed = {name for cell in cells for name in (cell.fields.tag, cell.fields.reason)}
    physical = tuple(name for name in columns if name not in removed)
    physical += tuple(
        cell.state for cell in cells if isinstance(cell, EncodedCell) and cell.state not in physical
    )
    return annotate(table.select(physical), cells, columns)


def pack(
    table: pa.Table,
    reasons: tuple[tuple[str, tuple[str, ...]], ...],
    column_reasons: tuple[tuple[tuple[str, tuple[str, ...]], ...], ...] = (),
) -> pa.Table:
    """Bind producer-owned fields once at the compact exchange boundary."""
    if not any(name.endswith("cell_tag") or name.endswith("_tag") for name in table.column_names):
        declared = binding(table.schema)
        result = annotate(table, declared.cells, declared.logical_columns)
        validate(result)
        return result
    schema = compact_schema(table.schema, reasons, column_reasons)
    return pack_bound(table, binding(schema))


def logical_table(table: pa.Table) -> pa.Table:
    declared = binding(table.schema)
    result = expand(table, declared.cells)
    result = result.select(declared.logical_columns)
    metadata = dict(result.schema.metadata or {})
    metadata.pop(_KEY, None)
    return result.replace_schema_metadata(metadata) if result.num_columns else result


def codes(table: pa.Table, cell: CellEncoding) -> pa.ChunkedArray:
    if isinstance(cell, KnownCell):
        return pa.chunked_array([pa.repeat(pa.scalar(cell.code, pa.int16()), table.num_rows)])
    if isinstance(cell, ValidityCell):
        return pc.if_else(
            table[cell.fields.value].is_valid(),
            pa.scalar(0, pa.int16()),
            pa.scalar(cell.missing_code, pa.int16()),
        )
    result = table[cell.state]
    if result.type != pa.int16() or result.null_count:
        invalid("state field must be non-null int16")
    return result


def defined_mask(table: pa.Table, cell: CellEncoding) -> pa.ChunkedArray:
    if isinstance(cell, ValidityCell):
        return table[cell.fields.value].is_valid()
    return pc.equal(codes(table, cell), pa.scalar(0, pa.int16()))


def validate(table: pa.Table) -> None:
    """Reject invalid codes, null carriers and value/state disagreements in every batch."""
    declared = binding(table.schema, exact=True)
    for cell in declared.cells:
        if cell.fields.value not in table.column_names:
            invalid("Cell binding refers to an absent value field")
        value = table[cell.fields.value]
        if isinstance(cell, KnownCell):
            if (cell.code == 0 and value.null_count) or (
                cell.code != 0 and value.null_count != len(value)
            ):
                invalid("Cell state disagrees with value validity")
            continue
        if isinstance(cell, ValidityCell):
            continue
        state = codes(table, cell)
        if not len(state):
            continue
        extrema = pc.min_max(state)
        if extrema["min"].as_py() == 0 and extrema["max"].as_py() == 0:
            if value.null_count:
                invalid("Cell state disagrees with value validity")
            continue
        admitted = (
            *((-1,) if isinstance(cell, EncodedCell) and cell.optional else ()),
            0,
            *cell.book.codes(),
        )
        if pc.any(pc.invert(pc.is_in(state, value_set=pa.array(admitted, pa.int16())))).as_py():
            invalid("undeclared physical Cell code")
        if pc.any(pc.not_equal(pc.equal(state, 0), table[cell.fields.value].is_valid())).as_py():
            invalid("Cell state disagrees with value validity")


def remap(values: pa.ChunkedArray, source: CellBook, target: CellBook) -> pa.ChunkedArray:
    size = len(source.entries) * 4 + 4
    translation = [0] * size
    admitted = [False] * size
    admitted[0] = True
    for pair, old in zip(source.entries, source.codes(), strict=True):
        translation[old] = target.code(pair.tag, pair.reason)
        admitted[old] = True
    if (
        values.null_count
        or pc.any(pc.or_(pc.less(values, 0), pc.greater_equal(values, size))).as_py()
    ):
        invalid("invalid source state codes")
    if not pc.all(pc.take(pa.array(admitted), values)).as_py() and len(values):
        invalid("undeclared source state codes")
    return values if source == target else pc.take(pa.array(translation, type=pa.int16()), values)


def read_cell(
    table: pa.Table, cell: CellEncoding, index: int
) -> tuple[object, CellTag, str | None]:
    code = (
        cell.code
        if isinstance(cell, KnownCell)
        else 0
        if isinstance(cell, ValidityCell) and table[cell.fields.value][index].is_valid
        else cell.missing_code
        if isinstance(cell, ValidityCell)
        else table[cell.state][index].as_py()
    )
    tag, reason = cell.book.decode(code)
    value = table[cell.fields.value][index].as_py()
    if (value is not None) != (tag == "defined"):
        invalid("Cell state disagrees with value validity")
    return value, tag, reason


def expand(table: pa.Table, cells: tuple[CellEncoding, ...]) -> pa.Table:
    """Restore the logical fields at the public presentation boundary."""
    for cell in cells:
        values = codes(table, cell)
        optional = isinstance(cell, EncodedCell) and cell.optional
        admitted = (*((-1,) if optional else ()), 0, *cell.book.codes())
        if pc.any(pc.invert(pc.is_in(values, value_set=pa.array(admitted, pa.int16())))).as_py():
            invalid("undeclared Cell code at public expansion")
        if pc.any(pc.not_equal(pc.equal(values, 0), table[cell.fields.value].is_valid())).as_py():
            invalid("Cell state disagrees with value validity")
        size = len(cell.book.entries) * 4 + 5
        tags: list[str | None] = [None] * size
        reasons: list[str | None] = [None] * size
        tags[1] = "defined"
        for pair, code in zip(cell.book.entries, cell.book.codes(), strict=True):
            tags[code + 1], reasons[code + 1] = pair.tag, pair.reason
        indices = pc.add(values.cast(pa.int32()), 1)
        decoded_tags = pc.take(pa.array(tags, type=pa.string()), indices)
        if decoded_tags.null_count and not optional:
            invalid("undeclared Cell code at public expansion")
        if isinstance(cell, EncodedCell):
            table = table.drop((cell.state,))
        position = table.column_names.index(cell.fields.value) + 1
        table = table.add_column(position, cell.fields.tag, decoded_tags)
        table = table.add_column(
            position + 1,
            cell.fields.reason,
            pc.take(pa.array(reasons, type=pa.string()), indices),
        )
    return table
