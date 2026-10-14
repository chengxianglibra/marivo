"""Shared codec for retained fit-state payload parts.

Fit methods (deviation, runs, association, forecast) retain their captured
inputs and fitted state as single-row JSON string parts. This module owns the
one canonical encoding: strict decode, canonical round-trip, embedded Arrow
tables and complete-key indexing.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

import pandas as pd
import pyarrow as pa
from pydantic import TypeAdapter, ValidationError

from marivo.analysis.core.model import PartRole
from marivo.analysis.materialization.graph_exchange import (
    ArrowPart,
    ExchangeContract,
    ExchangePart,
    to_arrow,
)
from marivo.analysis.materialization.graph_protocol import invalid

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class SavedTable:
    data: str
    rows: int


@dataclass(frozen=True, slots=True)
class SavedPart:
    role: PartRole
    keys: tuple[str, ...]
    table: SavedTable


def save(table: pa.Table) -> SavedTable:
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    raw = sink.getvalue().to_pybytes()
    return SavedTable(base64.b64encode(raw).decode(), table.num_rows)


def load(value: SavedTable) -> pa.Table:
    raw = base64.b64decode(value.data, validate=True)
    with pa.ipc.open_stream(raw) as reader:
        table = reader.read_all()
    if table.num_rows != value.rows:
        raise invalid("retained fit-state table row count differs")
    return table


def payload(role: str, text: str) -> ArrowPart:
    """Encode one canonical JSON payload as its single-row retained part."""
    return ArrowPart(role, pa.table({role + "__retained": [text]}))


def retained_text(parts: tuple[ExchangePart | ArrowPart, ...], role: str) -> str:
    """Return the one JSON string retained under ``role``."""
    table = next((part.table for part in parts if part.role == role), None)
    if table is None:
        raise invalid(f"retained fit-state part {role} is missing")
    column = role + "__retained"
    try:
        if isinstance(table, pd.DataFrame):
            value = table[column].iloc[0]
        elif isinstance(table, pa.Table):
            value = table[column][0].as_py()
        else:
            raise invalid(f"retained fit-state part {role} is not a table")
    except (KeyError, IndexError) as error:
        raise invalid(f"retained fit-state part {role} lacks its payload column") from error
    if not isinstance(value, str):
        raise invalid(f"retained fit-state part {role} is not a JSON string")
    return value


def decode(adapter: TypeAdapter[T], text: str, what: str) -> T:
    """Strictly decode one payload and require its canonical encoding."""
    try:
        value = adapter.validate_json(text, strict=True)
    except ValidationError as error:
        raise invalid(f"invalid retained {what} payload") from error
    if adapter.dump_json(value).decode() != text:
        raise invalid(f"retained {what} payload is not canonically encoded")
    return value


def decode_part(
    parts: tuple[ExchangePart | ArrowPart, ...], role: str, adapter: TypeAdapter[T]
) -> T:
    return decode(adapter, retained_text(parts, role), role)


def index(
    table: pa.Table | pd.DataFrame, keys: tuple[str, ...], what: str
) -> dict[tuple[object, ...], int]:
    """Map each complete key tuple to its row; reject null or duplicate keys."""
    frame = table.to_pandas() if isinstance(table, pa.Table) else table
    result: dict[tuple[object, ...], int] = {}
    for i, row in enumerate(frame.to_dict(orient="records")):
        key = tuple(row[k] for k in keys)
        if key in result or any(value is None or pd.isna(value) for value in key):
            raise invalid(f"{what} input contains null or duplicate complete keys")
        result[key] = i
    return result


def typed_parts(
    contract: ExchangeContract, parts: tuple[ExchangePart, ...]
) -> tuple[ArrowPart, ...]:
    """Re-type exchanged parts against their declared contract schemas, in order."""
    return tuple(
        ArrowPart(part.role, to_arrow(part.table, declared.schema))
        for part, declared in zip(parts, contract.parts, strict=True)
    )


def validate_with(
    verify: Callable[[ExchangeContract, pa.Table, tuple[ArrowPart, ...]], None],
    contract: ExchangeContract,
    primary: pd.DataFrame,
    parts: tuple[ExchangePart, ...],
) -> None:
    """Run a fit kernel's verifier over exchanged values re-typed to the contract."""
    verify(contract, to_arrow(primary, contract.schema), typed_parts(contract, parts))
