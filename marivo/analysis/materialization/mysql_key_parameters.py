"""Invocation-local MySQL complete-key membership through a JSON object."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING

import ibis
import ibis.expr.types as ir
import pandas as pd

from marivo.analysis.compiler.source_predicates import string_bytes
from marivo.analysis.core.data_types import DataColumn, ScalarDataType

if TYPE_CHECKING:
    from marivo.datasource.adapters import SourceSession


def qualifies_strings(values: dict[str, ir.Value], source: SourceSession | None) -> bool:
    """Keep native collated group membership on its established comparison route."""
    from marivo.analysis.materialization.query_preparation import physical_fields
    from marivo.datasource.engines.mysql import original_string_column
    from marivo.datasource.ir import TableSourceIR

    for value in values.values():
        if not value.type().is_string():
            continue
        if source is None:
            return False
        fields = physical_fields(value)
        if not fields:
            return False
        for field in fields:
            for identity, bound in source._bound_sources.items():
                if field.rel != bound.relation.op():
                    continue
                fact = (identity, field.name)
                if fact not in source._query_string_facts:
                    source._query_string_facts[fact] = isinstance(
                        bound.source, TableSourceIR
                    ) and original_string_column(
                        source._backend, source.datasource, bound.source, field.name
                    )
                if not source._query_string_facts[fact]:
                    return False
                break
            else:
                return False
    return True


def _hex(value: bytes) -> str:
    raise NotImplementedError("native MySQL HEX")


_HEX: Callable[[ir.Value], ir.StringValue] = ibis.udf.scalar.builtin(
    name="HEX", signature=(("binary",), "string")
)(_hex)


def membership(
    columns: tuple[DataColumn, ...],
    records: tuple[tuple[object, ...], ...],
    values: dict[str, ir.Value],
    source: SourceSession | None = None,
) -> ir.BooleanValue | None:
    """Use one JSON member lookup, with no per-key SQL expression expansion."""
    if not columns or any(
        not isinstance(column.dtype, ScalarDataType)
        or column.dtype.name not in ("int64", "string", "boolean", "date")
        for column in columns
    ):
        return None
    if not records:
        return ibis.literal(False)

    def encode(value: object, column: DataColumn) -> str:
        if value is None or value is pd.NA or value is pd.NaT:
            return "n"
        assert isinstance(column.dtype, ScalarDataType)
        if column.dtype.name == "string":
            assert isinstance(value, str)
            return "s" + value.encode("utf-8").hex().upper()
        if column.dtype.name == "boolean":
            return "b1" if value else "b0"
        return ("i" if column.dtype.name == "int64" else "d") + str(value)

    # Only digits, hex, minus and the component separators enter the path. A
    # distinct prefix and NULL token preserve empty strings and complete tuples.
    members = {
        "_".join(encode(value, column) for column, value in zip(columns, row, strict=True)): 1
        for row in records
    }
    longest = max(map(len, members))
    if longest > 65535:
        raise ValueError(
            f"MySQL JSON complete keys require at most 65535 encoded bytes; received {longest}. "
            "Use shorter string keys."
        )
    payload = json.dumps(members, separators=(",", ":"))
    # All member names are ASCII. A large binary JSON object has an 8-byte
    # header, 6-byte key entries and 5-byte value entries; small integer 1 is
    # inline. Include the outer type byte when bounding its native storage.
    binary_bytes = 9 + sum(11 + len(key) for key in members)
    if source is not None:
        limit = source._mysql_key_capacity()
        if max(len(payload), binary_bytes) > limit:
            raise ValueError(
                f"MySQL JSON keys require at most max_allowed_packet={limit} bytes; "
                f"received {len(payload)} text and at most {binary_bytes} binary bytes. "
                "Reduce the selected key domain or increase the datasource packet limit."
            )
    parts: list[ir.StringValue] = []
    for column in columns:
        value = values[column.slot_id]
        assert isinstance(column.dtype, ScalarDataType)
        if column.dtype.name == "string":
            part = ibis.literal("s") + _HEX(string_bytes(value, "mysql"))
        elif column.dtype.name == "boolean":
            part = value.ifelse("b1", "b0")
        else:
            part = ibis.literal("i" if column.dtype.name == "int64" else "d") + value.cast("string")
        assert isinstance(part, ir.StringValue)
        parts.append(value.isnull().ifelse("n", part))
    key = ibis.literal("_").join(parts)
    document = ibis.literal(payload).cast("json")
    assert isinstance(document, ir.JSONValue)
    # Ibis MySQL JSONGetItem prepends '$.' but does not quote its string index.
    # The closed alphabet above needs quoting, and cannot contain quotes/escapes.
    return document[ibis.literal('"') + key + '"'].notnull()
