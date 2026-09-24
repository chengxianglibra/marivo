"""Shared first-round J1 value and keyed-state validation."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

import pyarrow as pa

from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.materialization.errors import MaterializationError

_MIN_I64 = -(2**63)
_MAX_I64 = 2**63 - 1


def _fail(expected: str, received: str) -> MaterializationError:
    return MaterializationError(
        expected=expected,
        received=received,
        repair="Correct the selected J1 source or retained state before continuing.",
        stage="output_validation",
    )


def _number(value: object, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if type(value) is int:
        if not _MIN_I64 <= value <= _MAX_I64:
            raise _fail("int64 numeric value", "integer overflow")
        return
    if type(value) is float and math.isfinite(value):
        return
    raise _fail("finite int64 or float64 numeric value", type(value).__name__)


def merge_numbers(values: Iterable[object]) -> int | float:
    """Merge checked numeric state without NumPy integer wraparound."""
    collected: list[int | float] = []
    for value in values:
        if type(value) is not int and type(value) is not float:
            raise _fail("finite int64 or float64 numeric value", type(value).__name__)
        _number(value)
        collected.append(value)
    if any(type(value) is float for value in collected):
        result = math.fsum(float(value) for value in collected)
    else:
        result = sum(int(value) for value in collected)
    _number(result)
    return result


def merge_counts(values: Iterable[object]) -> int:
    """Add nonnegative int64 counts with an explicit overflow boundary."""
    total = 0
    for value in values:
        if type(value) is not int or value < 0 or value > _MAX_I64:
            raise _fail("nonnegative int64 state count", type(value).__name__)
        total += value
        if total > _MAX_I64:
            raise _fail("nonnegative int64 state count", "integer overflow")
    return total


def _validate_table(table: pa.Table, *, part: bool = False) -> None:
    names = set(table.column_names)
    if len(names) != len(table.column_names):
        raise _fail("unique J1 output columns", "duplicate column")
    keys: tuple[str, ...]
    if part:
        if not {"member", "group", "state_sum", "non_null_count", "row_count"} <= names:
            raise _fail("keyed coordinate state", "missing part columns")
        if tuple(table.column_names) != (
            "member",
            "group",
            "state_sum",
            "non_null_count",
            "row_count",
        ):
            raise _fail("exact keyed coordinate state fields", "part fields differ")
        keys = ("member", "group")
    elif "group" in names:
        keys = ("group",)
    elif "member" in names:
        keys = ("member",)
    else:
        keys = ()
    for key_name in keys:
        physical = table.schema.field(key_name).type
        admitted = (
            pa.types.is_string(physical)
            if key_name == "group"
            else pa.types.is_string(physical) or pa.types.is_int64(physical)
        )
        if not admitted:
            raise _fail("string or int64 explicit key", str(table.schema.field(key_name).type))
    for name in ("non_null_count", "row_count", "current_count"):
        if name in names and not pa.types.is_int64(table.schema.field(name).type):
            raise _fail("int64 state count", str(table.schema.field(name).type))
    for name in ("state_sum", "current_sum"):
        if name in names and not (
            pa.types.is_int64(table.schema.field(name).type)
            or pa.types.is_float64(table.schema.field(name).type)
        ):
            raise _fail("int64 or float64 state", str(table.schema.field(name).type))
    if "value" in names and "cell_tag" not in names:
        raise _fail("Cell tag for every value", "missing tag")
    if "cell_tag" in names and ("value" not in names or "cell_reason" not in names):
        raise _fail("complete value Cell fields", "missing field")
    for name in ("cell_tag", "cell_reason"):
        if name in names and not pa.types.is_string(table.schema.field(name).type):
            raise _fail("physical string Cell fields", str(table.schema.field(name).type))
    rows = table.to_pylist()
    seen: set[tuple[object, ...]] = set()
    for row in rows:
        identity = tuple(row[name] for name in keys)
        if any(value is None for value in identity) or identity in seen:
            raise _fail("complete unique explicit keys", "missing or duplicate key")
        seen.add(identity)
        for name in ("non_null_count", "row_count", "current_count"):
            if name in row:
                value = row[name]
                if type(value) is not int or not 0 <= value <= _MAX_I64:
                    raise _fail("nonnegative int64 state count", name)
        for name in ("state_sum", "current_sum"):
            if name in row:
                _number(row[name], nullable=True)
        if "value" in row and "cell_tag" in row:
            tag = row["cell_tag"]
            reason = row.get("cell_reason")
            if tag == "defined":
                if row["value"] is None or reason is not None:
                    raise _fail("Defined value without reason", "invalid Cell")
                if isinstance(row["value"], (int, float)):
                    _number(row["value"])
            elif tag == "null":
                if row["value"] is not None or reason not in (
                    "source_null",
                    "empty_contribution",
                ):
                    raise _fail("Null Cell with a registered reason", "invalid Cell")
            elif tag == "undefined":
                if row["value"] is not None or reason != "empty_mean":
                    raise _fail("Undefined(empty_mean)", "invalid Cell")
            else:
                raise _fail("registered J1 Cell tag", str(tag))
        if "non_null_count" in row and "row_count" in row:
            if row["non_null_count"] > row["row_count"]:
                raise _fail("non-null support within row support", "inconsistent counts")
            if "state_sum" in row:
                support = row["non_null_count"]
                state_sum = row["state_sum"]
                if (support == 0) != (state_sum is None):
                    raise _fail("sum state iff positive support", "inconsistent state")
                if "cell_tag" in row:
                    expected = "defined" if support else "null"
                    if row["cell_tag"] != expected or row["value"] != state_sum:
                        raise _fail("Cell matching original sum state", "inconsistent Cell")


@dataclass(frozen=True, slots=True)
class J1ExecutionResult:
    """One completed private stage; publication remains a separate Runtime action."""

    root: LogicalRootHandle
    primary: pa.Table
    parts: tuple[tuple[str, pa.Table], ...] = ()
    completed_checks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        operation = self.root.operator_id
        parameters = self.root.parameters if type(self.root.parameters) is tuple else ()
        cell = ("value", "cell_tag", "cell_reason")
        state = ("state_sum", "non_null_count", "row_count")
        expected: tuple[str, ...]
        if operation in ("dsl.j1.members", "dsl.j1.where"):
            expected = ("member",)
        elif operation == "dsl.j1.read":
            expected = ("member", *cell)
        elif operation == "dsl.j1.group":
            expected = (
                ("group", *cell, *state)
                if len(parameters) == 2 and parameters[1] == "contribution"
                else ("group",)
            )
        elif operation == "dsl.j1.observe":
            parent = self.root.inputs[0].root if self.root.inputs else None
            key = (
                "group"
                if isinstance(parent, LogicalRootHandle)
                and parent.shape_id.local_shape_id == "group"
                else "member"
            )
            expected = (key, *cell, *state)
        elif operation == "dsl.j1.rollup":
            expected = (*cell, *state)
        elif operation == "dsl.j1.summarize":
            method = parameters[0] if parameters else None
            expected = (
                (*cell, "current_count")
                if method == "count"
                else (*cell, "current_sum")
                if method == "sum"
                else (*cell, "current_sum", "current_count")
                if method == "mean"
                else ()
            )
        else:
            expected = ()
        if not expected or tuple(self.primary.column_names) != expected:
            raise _fail("exact admitted J1 result fields", operation)
        if "value" in expected:
            physical = self.primary.schema.field("value").type
            valid = (
                pa.types.is_string(physical)
                if operation == "dsl.j1.read"
                else pa.types.is_int64(physical)
                if operation == "dsl.j1.summarize" and parameters[0] == "count"
                else pa.types.is_float64(physical)
                if operation == "dsl.j1.summarize" and parameters[0] == "mean"
                else pa.types.is_int64(physical) or pa.types.is_float64(physical)
            )
            if not valid:
                raise _fail("admitted J1 value physical type", str(physical))
        _validate_table(self.primary)
        if len({role for role, _ in self.parts}) != len(self.parts):
            raise _fail("unique retained J1 parts", "duplicate role")
        for role, table in self.parts:
            if role != "coordinate":
                raise _fail("registered coordinate role", role)
            _validate_table(table, part=True)
            if "member" not in self.primary.column_names or (
                table.schema.field("member").type != self.primary.schema.field("member").type
            ):
                raise _fail("coordinate part on the exact member key type", "foreign key type")
            primary_rows = {row["member"]: row for row in self.primary.to_pylist()}
            counts: dict[object, list[tuple[int, int, object]]] = {}
            for row in table.to_pylist():
                member = row["member"]
                if member not in primary_rows:
                    raise _fail("coordinate part members within primary domain", "foreign member")
                counts.setdefault(member, []).append(
                    (row["non_null_count"], row["row_count"], row["state_sum"])
                )
            for member, primary in primary_rows.items():
                components = counts.get(member, [])
                if (
                    merge_counts(item[0] for item in components) != primary["non_null_count"]
                    or merge_counts(item[1] for item in components) != primary["row_count"]
                ):
                    raise _fail("complete coordinate contribution partition", "state counts differ")
                if pa.types.is_int64(table.schema.field("state_sum").type):
                    amount = merge_numbers(item[2] for item in components if item[2] is not None)
                    if primary["non_null_count"] and amount != primary["state_sum"]:
                        raise _fail("exact integer coordinate sum partition", "state sums differ")
