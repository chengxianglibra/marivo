"""Lower logical Cell expressions to compact, typed relational carriers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo.analysis.core.cell_encoding import (
    CellBook,
    CellEncoding,
    CellFields,
    EncodedCell,
    KnownCell,
    ValidityCell,
    invalid,
)

_TAGS = ("defined", "null", "undefined", "unknown")


def state_value(table: ir.Table, cell: CellEncoding) -> ir.Value:
    if isinstance(cell, KnownCell):
        return table[cell.fields.value].isnull().ifelse(cell.code, cell.code).cast("int16")
    if isinstance(cell, ValidityCell):
        return table[cell.fields.value].notnull().ifelse(0, cell.missing_code).cast("int16")
    return table[cell.state]


@dataclass(frozen=True, slots=True)
class _Value:
    expression: ir.Value
    book: CellBook
    possible: tuple[int, ...]
    anchor: ir.Value | None = None


@dataclass(frozen=True, slots=True)
class _Virtual:
    value: _Value
    component: str


@dataclass(frozen=True, slots=True)
class CellRead:
    expression: ir.Table
    cells: tuple[CellEncoding, ...]
    logical_columns: tuple[str, ...]


def _merge(first: CellBook, second: CellBook) -> CellBook:
    entries = set(first.entries) | set(second.entries)
    return CellBook.from_reasons(tuple((p.tag, (p.reason,)) for p in entries))


def _remap(value: _Value, book: CellBook) -> ir.Value:
    if value.book == book or value.possible == (0,):
        return value.expression
    return ibis.cases(
        (value.expression == 0, 0),
        *(
            (value.expression == code, code if code < 0 else value.book.remap(code, book))
            for code in value.possible
            if code != 0
        ),
        else_=ibis.null().cast("int16"),
    ).cast("int16")


def _isin(value: _Value, codes: tuple[int, ...]) -> ir.BooleanValue:
    if not codes and value.anchor is not None:
        return value.anchor.isnull() == value.anchor.notnull()
    node = value.expression.op()
    while isinstance(node, ops.Cast):
        node = node.arg
    if (
        isinstance(node, ops.IfElse)
        and isinstance(node.true_expr, ops.Literal)
        and isinstance(node.false_null_expr, ops.Literal)
    ):
        first, last = node.true_expr.value in codes, node.false_null_expr.value in codes
        condition = node.bool_expr.to_expr()
        if first != last:
            return condition if first else ~condition
        return condition.ifelse(first, last)
    return value.expression.isin(codes)


def lower_cells(
    table: ir.Table,
    future_reasons: tuple[tuple[str, tuple[str, ...]], ...] = (),
    *,
    raw_cells: Mapping[ops.Relation, tuple[CellEncoding, ...]] | None = None,
    stage_policies: Mapping[ops.Relation, tuple[tuple[str, tuple[str, ...]], ...]] | None = None,
) -> CellRead:
    """Rewrite generated relational Cells before issuing a source read."""
    raw_names = {
        name
        for cells in (raw_cells or {}).values()
        for cell in cells
        for name in (cell.fields.tag, cell.fields.reason)
    }

    def cell_name(name: str) -> bool:
        return (
            name in raw_names
            or name in ("taga", "tagb", "reasona", "reasonb")
            or name.endswith(("cell_tag", "cell_reason", "_tag", "_reason"))
        )

    if (
        not future_reasons
        and not any(cell_name(name) for name in table.columns)
        and not any(cell_name(field.name) for field in table.op().find(ops.Field))
    ):
        return CellRead(table, (), tuple(table.columns))

    outer_references = {
        reference
        for join in table.op().find(ops.JoinChain)
        if any(link.how in ("left", "right", "outer") for link in join.rest)
        for reference in (join.first, *(link.table for link in join.rest))
    }
    bindings: dict[ops.Relation, tuple[CellEncoding, ...]] = {}
    virtual: dict[ops.Value, _Virtual] = {}

    def future(
        expression: ir.Table,
        cells: tuple[CellEncoding, ...],
        reasons: tuple[tuple[str, tuple[str, ...]], ...],
    ) -> tuple[ir.Table, tuple[CellEncoding, ...]]:
        updated = list(cells)
        for index, cell in enumerate(cells):
            if cell.fields.value != "value":
                continue
            book = _merge(cell.book, CellBook.from_reasons(reasons))
            if len(book.entries) == 1 and not isinstance(cell, EncodedCell):
                pair = book.entries[0]
                updated[index] = ValidityCell(cell.fields, book, book.code(pair.tag, pair.reason))
            elif book.entries:
                state = cell.fields.tag.removesuffix("tag") + "state"
                old = _Value(
                    state_value(expression, cell),
                    cell.book,
                    (
                        *((-1,) if isinstance(cell, EncodedCell) and cell.optional else ()),
                        0,
                        *cell.book.codes(),
                    ),
                )
                expression = expression.mutate(**{state: _remap(old, book).cast("!int16")})
                updated[index] = EncodedCell(
                    cell.fields, book, state, isinstance(cell, EncodedCell) and cell.optional
                )
        return expression, tuple(updated)

    def logical(value: _Value, component: str) -> ops.Value:
        state = value.expression
        expression = (
            ibis.cases(
                (state == 0, "defined"),
                *((state % 4 == i, tag) for i, tag in enumerate(_TAGS[1:], 1)),
                else_=ibis.null().cast("string"),
            )
            if component == "tag"
            else ibis.cases(
                *(
                    (state == value.book.code(p.tag, p.reason), p.reason)
                    for p in value.book.entries
                ),
                else_=ibis.null().cast("string"),
            )
            if value.book.entries
            else state.isnull().ifelse(ibis.null().cast("string"), ibis.null().cast("string"))
        )
        node = expression.op()
        virtual[node] = _Virtual(value, component)
        return node

    def encode_pair(tag: ops.Value, reason: ops.Value) -> _Value:
        a, b = virtual.get(tag), virtual.get(reason)
        if (
            a is not None
            and b is not None
            and (
                a.value.expression.op() == b.value.expression.op() and a.value.book == b.value.book
            )
        ):
            return a.value
        if isinstance(tag, ops.Literal) and isinstance(reason, ops.Literal):
            tag_value, reason_value = tag.value, reason.value
            if tag_value is None and reason_value is None:
                return _Value(ibis.literal(-1, type="int16"), CellBook(()), (-1,))
            if tag_value == "defined" and reason_value is None:
                return _Value(ibis.literal(0, type="int16"), CellBook(()), (0,))
            if not isinstance(tag_value, str) or not isinstance(reason_value, str):
                invalid(
                    f"generated Cell literal has invalid tag/reason {tag_value!r}/{reason_value!r}"
                )
                raise AssertionError("unreachable")
            book = CellBook.from_reasons(((tag_value, (reason_value,)),))
            code = book.code(tag_value, reason_value)
            return _Value(ibis.literal(code, type="int16"), book, (code,))
        if isinstance(tag, ops.IfElse) or isinstance(reason, ops.IfElse):
            branch = tag if isinstance(tag, ops.IfElse) else reason
            assert isinstance(branch, ops.IfElse)
            condition = branch.bool_expr
            first_tag = (
                tag.true_expr if isinstance(tag, ops.IfElse) and tag.bool_expr == condition else tag
            )
            last_tag = (
                tag.false_null_expr
                if isinstance(tag, ops.IfElse) and tag.bool_expr == condition
                else tag
            )
            first_reason = (
                reason.true_expr
                if isinstance(reason, ops.IfElse) and reason.bool_expr == condition
                else reason
            )
            last_reason = (
                reason.false_null_expr
                if isinstance(reason, ops.IfElse) and reason.bool_expr == condition
                else reason
            )
            if isinstance(condition, ops.Literal) and isinstance(condition.value, bool):
                return (
                    encode_pair(first_tag, first_reason)
                    if condition.value
                    else encode_pair(last_tag, last_reason)
                )
            first, last = encode_pair(first_tag, first_reason), encode_pair(last_tag, last_reason)
            book = _merge(first.book, last.book)
            possible = tuple(
                sorted(
                    {c if c < 0 else first.book.remap(c, book) for c in first.possible}
                    | {c if c < 0 else last.book.remap(c, book) for c in last.possible}
                )
            )
            return _Value(
                condition.to_expr().ifelse(_remap(first, book), _remap(last, book)).cast("int16"),
                book,
                possible,
            )
        if isinstance(tag, ops.Cast):
            return encode_pair(tag.arg, reason)
        if isinstance(reason, ops.Cast):
            return encode_pair(tag, reason.arg)
        if isinstance(tag, ops.SearchedCase):
            result: ops.Value = tag.default
            for condition, value in reversed(tuple(zip(tag.cases, tag.results, strict=True))):
                result = ops.IfElse(condition, value, result)
            return encode_pair(result, reason)
        if isinstance(reason, ops.SearchedCase):
            result = reason.default
            for condition, value in reversed(tuple(zip(reason.cases, reason.results, strict=True))):
                result = ops.IfElse(condition, value, result)
            return encode_pair(tag, result)
        invalid(f"unbound generated Cell expressions {type(tag).__name__}/{type(reason).__name__}")
        raise AssertionError("unreachable")

    def rewrite(node: ops.Node, changed: Mapping[str, object] | None) -> ops.Node:
        kwargs = {} if changed is None else dict(changed)
        if isinstance(node, ops.Relation) and raw_cells is not None and node in raw_cells:
            source_cells = raw_cells[node]
            original = node.to_expr()
            removed = {
                name for cell in source_cells for name in (cell.fields.tag, cell.fields.reason)
            }
            source_values = {
                name: original[name] for name in original.columns if name not in removed
            }
            for cell in source_cells:
                if isinstance(cell, EncodedCell):
                    source_values[cell.state] = ibis.cases(
                        (
                            (original[cell.fields.tag] == "defined")
                            & original[cell.fields.reason].isnull(),
                            0,
                        ),
                        *(
                            (
                                (
                                    (original[cell.fields.tag] == p.tag)
                                    & (original[cell.fields.reason] == p.reason)
                                ),
                                cell.book.code(p.tag, p.reason),
                            )
                            for p in cell.book.entries
                        ),
                        else_=-32768,
                    ).cast("!int16")
            bindings[node] = source_cells
            return original.select(**source_values).op()
        if isinstance(node, ops.JoinReference) and node in outer_references:
            parent = kwargs.get("parent", node.parent)
            assert isinstance(parent, ops.Relation)
            reference_cells = bindings.get(node.parent, ())
            if reference_cells:
                parent = (
                    parent.to_expr().mutate(__cell_present=ibis.literal(True, type="!boolean")).op()
                )
            bindings[node] = reference_cells
            return node.copy(parent=parent)
        if isinstance(node, ops.Field):
            parent = kwargs.get("rel", node.rel)
            assert isinstance(parent, ops.Relation)
            for cell in bindings.get(node.rel, ()):
                if node.name in (cell.fields.tag, cell.fields.reason):
                    state = state_value(parent.to_expr(), cell)
                    possible = (
                        (cell.code,)
                        if isinstance(cell, KnownCell)
                        else (
                            (0, cell.missing_code)
                            if isinstance(cell, ValidityCell)
                            else (
                                *((-1,) if cell.optional else ()),
                                -32768,
                                0,
                                *(cell.book.code(p.tag, p.reason) for p in cell.book.entries),
                            )
                        )
                    )
                    if node.rel in outer_references:
                        present = parent.to_expr()["__cell_present"].fill_null(False)
                        state = present.ifelse(state, -1).cast("int16")
                        possible = (-1, *possible)
                    return logical(
                        _Value(state, cell.book, possible, parent.to_expr()[cell.fields.value]),
                        "tag" if node.name == cell.fields.tag else "reason",
                    )
            return ops.Field(parent, node.name)
        if isinstance(node, (ops.Equals, ops.NotEquals)):
            left = kwargs.get("left", node.left)
            right = kwargs.get("right", node.right)
            assert isinstance(left, ops.Value) and isinstance(right, ops.Value)
            item = virtual.get(left)
            if item is None:
                left, right = right, left
                item = virtual.get(left)
            if item is not None and isinstance(right, ops.Literal) and isinstance(right.value, str):
                code = item.value.expression
                if item.component == "tag" and right.value in _TAGS:
                    result = (
                        _isin(item.value, (0,))
                        if right.value == "defined"
                        else _isin(
                            item.value,
                            tuple(
                                c
                                for c in item.value.possible
                                if c > 0 and c % 4 == _TAGS.index(right.value)
                            ),
                        )
                    )
                elif item.component == "reason":
                    codes = tuple(
                        item.value.book.code(p.tag, p.reason)
                        for p in item.value.book.entries
                        if p.reason == right.value
                    )
                    result = _isin(item.value, codes)
                else:
                    result = code.isnull() & False
                result = ~result if isinstance(node, ops.NotEquals) else result
                missing = (
                    _isin(item.value, (-1,))
                    if item.component == "tag"
                    else _isin(item.value, (-1, 0))
                )
                if -1 in item.value.possible or item.component == "reason":
                    result = missing.ifelse(ibis.null().cast("boolean"), result)
                return result.op()
        if isinstance(node, (ops.IsNull, ops.NotNull)):
            argument = kwargs.get("arg", node.arg)
            assert isinstance(argument, ops.Value)
            item = virtual.get(argument)
            if item is not None:
                result = (
                    _isin(item.value, (-1, 0))
                    if item.component == "reason"
                    else _isin(item.value, (-1,))
                )
                return (~result if isinstance(node, ops.NotNull) else result).op()
        if isinstance(node, ops.InValues):
            argument = kwargs.get("value", node.value)
            options = kwargs.get("options", node.options)
            assert isinstance(argument, ops.Value) and isinstance(options, tuple)
            item = virtual.get(argument)
            if (
                item is not None
                and item.component == "tag"
                and all(
                    isinstance(option, ops.Literal) and option.value in _TAGS for option in options
                )
            ):
                tags = tuple(
                    _TAGS.index(option.value)
                    for option in options
                    if isinstance(option, ops.Literal)
                )
                code = item.value.expression
                result = _isin(
                    item.value, tuple(c for c in item.value.possible if c >= 0 and c % 4 in tags)
                )
                return (
                    (code == -1).ifelse(ibis.null().cast("boolean"), result)
                    if -1 in item.value.possible
                    else result
                ).op()
        if isinstance(node, ops.Set):
            left, right = kwargs.get("left", node.left), kwargs.get("right", node.right)
            assert isinstance(left, ops.Relation) and isinstance(right, ops.Relation)
            first, last = bindings.get(node.left, ()), bindings.get(node.right, ())
            if len(first) != len(last):
                invalid("set operands have different Cell slots")
            set_cells: list[CellEncoding] = []
            operands = [left.to_expr(), right.to_expr()]
            for a, b in zip(first, last, strict=True):
                if a.fields != b.fields:
                    invalid("set operands have different logical Cell fields")
                if a == b:
                    set_cells.append(a)
                    continue
                book = _merge(a.book, b.book)
                state = a.fields.tag.removesuffix("tag") + "state"
                optional = any(isinstance(c, EncodedCell) and c.optional for c in (a, b))
                set_cells.append(EncodedCell(a.fields, book, state, optional))
                for i, cell in enumerate((a, b)):
                    source = operands[i]
                    possible = (
                        (cell.code,)
                        if isinstance(cell, KnownCell)
                        else (
                            (0, cell.missing_code)
                            if isinstance(cell, ValidityCell)
                            else (
                                *((-1,) if cell.optional else ()),
                                -32768,
                                0,
                                *(cell.book.code(p.tag, p.reason) for p in cell.book.entries),
                            )
                        )
                    )
                    value = _Value(state_value(source, cell), cell.book, possible)
                    operands[i] = source.mutate(**{state: _remap(value, book).cast("!int16")})
            physical = {c.fields.tag for c in set_cells} | {c.fields.reason for c in set_cells}
            columns = [name for name in node.schema.names if name not in physical]
            columns.extend(c.state for c in set_cells if isinstance(c, EncodedCell))
            kwargs.update(
                left=operands[0].select(*columns).op(), right=operands[1].select(*columns).op()
            )
            bindings[node] = tuple(set_cells)
            return node.copy(**kwargs)
        if isinstance(node, (ops.Project, ops.JoinChain, ops.Aggregate)):
            field = "metrics" if isinstance(node, ops.Aggregate) else "values"
            original = node.metrics if isinstance(node, ops.Aggregate) else node.values
            rewritten = kwargs.get(field, original)
            assert isinstance(rewritten, Mapping)
            values: dict[str, ops.Value] = {}
            for name, value in rewritten.items():
                assert isinstance(name, str) and isinstance(value, ops.Value)
                values[name] = value
            cells: list[CellEncoding] = []
            for tag_name in tuple(values):
                if tag_name not in values:
                    continue
                marker = virtual.get(values[tag_name])
                if tag_name.endswith("cell_tag"):
                    prefix = tag_name.removesuffix("cell_tag")
                    value_name, reason_name = prefix + "value", prefix + "cell_reason"
                    state_name = prefix + "cell_state"
                elif tag_name.endswith("_tag"):
                    prefix = tag_name.removesuffix("tag")
                    value_name, reason_name = prefix + "value", prefix + "reason"
                    state_name = prefix + "state"
                elif tag_name in ("taga", "tagb") and marker is not None:
                    suffix = tag_name[-1]
                    value_name, reason_name, state_name = (
                        "v" + suffix,
                        "reason" + suffix,
                        "state" + suffix,
                    )
                else:
                    continue
                if value_name not in values or reason_name not in values:
                    continue
                encoded = encode_pair(values[tag_name], values[reason_name])
                fields = CellFields(value_name, tag_name, reason_name)
                del values[tag_name], values[reason_name]
                if len(encoded.possible) == 1 and encoded.possible[0] >= 0:
                    encoding: CellEncoding = KnownCell(fields, encoded.book, encoded.possible[0])
                elif (
                    len(encoded.possible) == 2
                    and 0 in encoded.possible
                    and all(code >= 0 for code in encoded.possible)
                ):
                    encoding = ValidityCell(
                        fields, encoded.book, next(c for c in encoded.possible if c)
                    )
                else:
                    encoding = EncodedCell(fields, encoded.book, state_name, -1 in encoded.possible)
                    values[state_name] = encoded.expression.cast("!int16").op()
                cells.append(encoding)
            kwargs[field] = values
            result = node.copy(**kwargs)
            declared = tuple(cells)
            if stage_policies is not None and node in stage_policies:
                result_expression, declared = future(
                    result.to_expr(), declared, stage_policies[node]
                )
                result = result_expression.op()
            bindings[node] = declared
            return result
        result = node.copy(**kwargs) if changed is not None else node
        if isinstance(node, ops.Relation):
            direct = next((v for v in node.__args__ if isinstance(v, ops.Relation)), None)
            if direct is not None:
                bindings[node] = bindings.get(direct, ())
        return result

    rewritten = table.op().replace(rewrite)
    assert isinstance(rewritten, ops.Relation)
    expression = rewritten.to_expr()
    cells = bindings.get(table.op(), ())
    if future_reasons:
        expression, cells = future(expression, cells, future_reasons)
    return CellRead(expression, cells, tuple(table.columns))
