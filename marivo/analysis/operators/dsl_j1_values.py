"""Shared first-round J1 value and keyed-state validation."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

import pyarrow as pa

from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.observation.dsl_j1 import J3_RATIO_COLUMNS, numeric_threshold_is_lossless
from marivo.analysis.operators.dsl_j1_contracts import J1_COMPARE_DIFFERENCE

_MIN_I64 = -(2**63)
_MAX_I64 = 2**63 - 1


def admit_numeric_threshold(value_type: str, threshold: object, stage: str) -> int | float:
    """Keep numeric predicates exact at the selected physical comparison type."""
    if not numeric_threshold_is_lossless(value_type, threshold):
        raise MaterializationError(
            expected="finite lossless threshold for the numeric Cell type",
            received=f"{type(threshold).__name__} threshold for {value_type}",
            repair="Use an admitted finite threshold without numeric type conversion loss.",
            stage=stage,
        )
    assert type(threshold) is int or type(threshold) is float
    return threshold


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


def _validate_table(
    table: pa.Table, *, part: bool = False, count_observation: bool = False
) -> None:
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
            expected = (
                "physical string coordinate key"
                if key_name == "group"
                else "physical string or int64 member key"
            )
            raise _fail(expected, str(physical))
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
                if count_observation:
                    if (
                        state_sum != support
                        or support != row["row_count"]
                        or row.get("cell_tag") != "defined"
                        or row.get("cell_reason") is not None
                        or row.get("value") != support
                    ):
                        raise _fail(
                            "count Cell and support including valid empty zero",
                            "inconsistent count",
                        )
                    continue
                if (support == 0) != (state_sum is None):
                    raise _fail("sum state iff positive support", "inconsistent state")
                if "cell_tag" in row:
                    expected = "defined" if support else "null"
                    if row["cell_tag"] != expected or row["value"] != state_sum:
                        raise _fail("Cell matching original sum state", "inconsistent Cell")


def _validate_ratio(root: LogicalRootHandle, table: pa.Table) -> None:
    params = root.parameters if type(root.parameters) is tuple else ()
    if root.operator_id == "dsl.j1.ratio_observe":
        if len(params) != 5 or type(params[4]) is not tuple:
            raise _fail("canonical ratio parameters", "invalid observation")
        keys = ("member", *(f"coord_{index}" for index in range(len(params[4]))))
    elif len(params) == 3 and params[1] in ("group", "singleton"):
        keys = ("group",) if params[1] == "group" else ()
    else:
        raise _fail("canonical ratio parameters", "invalid rollup")
    expected = (*keys, "value", "cell_tag", "cell_reason", *J3_RATIO_COLUMNS)
    if tuple(table.column_names) != expected:
        raise _fail("exact ratio value and component fields", str(table.column_names))
    if not pa.types.is_float64(table.schema.field("value").type):
        raise _fail("float64 ratio value", str(table.schema.field("value").type))
    for name in J3_RATIO_COLUMNS[1:]:
        if not pa.types.is_int64(table.schema.field(name).type):
            raise _fail("int64 component count", name)
    sum_type = table.schema.field("numerator_sum").type
    if not (pa.types.is_int64(sum_type) or pa.types.is_float64(sum_type)):
        raise _fail("numeric numerator state", str(sum_type))
    seen: set[tuple[object, ...]] = set()
    for row in table.to_pylist():
        key = tuple(row[name] for name in keys)
        if any(value is None for value in key) or key in seen:
            raise _fail("complete unique ratio coordinate tuple", "missing or duplicate key")
        seen.add(key)
        numerator = row["numerator_sum"]
        _number(numerator)
        for name in J3_RATIO_COLUMNS[1:]:
            value = row[name]
            if type(value) is not int or value < 0 or value > _MAX_I64:
                raise _fail("nonnegative component count", name)
        if row["numerator_non_null_count"] > row["numerator_row_count"]:
            raise _fail("numerator support within rows", "inconsistent state")
        if row["denominator_count"] != row["denominator_row_count"]:
            raise _fail("count component row support", "inconsistent state")
        denominator = row["denominator_count"]
        if denominator == 0:
            if (
                row["value"] is not None
                or row["cell_tag"] != "undefined"
                or row["cell_reason"] != "zero_denominator"
            ):
                raise _fail("Undefined(zero_denominator)", "invalid ratio Cell")
        else:
            value = row["value"]
            _number(value)
            if (
                row["cell_tag"] != "defined"
                or row["cell_reason"] is not None
                or not math.isclose(
                    float(value), float(numerator) / denominator, rel_tol=1e-12, abs_tol=1e-12
                )
            ):
                raise _fail("ratio finished from retained components", "inconsistent ratio Cell")


@dataclass(frozen=True, slots=True)
class J1ExecutionResult:
    """One completed private stage; publication remains a separate Runtime action."""

    root: LogicalRootHandle
    primary: pa.Table
    parts: tuple[tuple[str, pa.Table], ...] = ()
    completed_checks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        operation = self.root.operator_id
        if operation in ("dsl.j1.correlate", "dsl.j1.correlate_where"):
            names = (
                "metric_key_a",
                "metric_key_b",
                "status",
                "coefficient",
                "input_observation_count",
                "matched_observation_count",
                "null_pair_count",
                "complete_pair_count",
            )
            if tuple(self.primary.column_names) != names or self.parts or self.primary.num_rows > 1:
                raise _fail("one exact Association result", "invalid Association rows or parts")
            for name in names[:3]:
                if self.primary.schema.field(name).type != pa.string():
                    raise _fail("string Association identity and status", name)
            if self.primary.schema.field("coefficient").type != pa.float64() or any(
                self.primary.schema.field(name).type != pa.int64() for name in names[4:]
            ):
                raise _fail("float64 coefficient and int64 counts", "invalid Association type")
            binding_root = (
                self.root.inputs[0].root if operation == "dsl.j1.correlate_where" else self.root
            )
            binding = binding_root.parameters if isinstance(binding_root, LogicalRootHandle) else ()
            if type(binding) is not tuple or len(binding) != 4 or binding[2] != "spearman":
                raise _fail("bound Spearman Metric pair", "invalid Association binding")
            for row in self.primary.to_pylist():
                coefficient = row["coefficient"]
                pair_counts = tuple(row[name] for name in names[4:])
                if (
                    not all(isinstance(row[name], str) and row[name] for name in names[:3])
                    or row["metric_key_a"] == row["metric_key_b"]
                    or row["metric_key_a"] != "metric:" + str(binding[0])
                    or row["metric_key_b"] != "metric:" + str(binding[1])
                    or row["status"] != "valid"
                    or type(coefficient) is not float
                    or not math.isfinite(coefficient)
                    or abs(coefficient) > 1
                    or any(type(value) is not int or value < 0 for value in pair_counts)
                    or pair_counts[0] != pair_counts[1]
                    or pair_counts[1] != pair_counts[2] + pair_counts[3]
                ):
                    raise _fail(
                        "valid complete Spearman result and counts", "inconsistent Association row"
                    )
            return
        if operation in ("dsl.j1.ratio_observe", "dsl.j1.ratio_rollup"):
            if self.parts:
                raise _fail("ratio state in keyed retained columns", "unexpected independent parts")
            _validate_ratio(self.root, self.primary)
            return
        parameters = self.root.parameters if type(self.root.parameters) is tuple else ()
        cell = ("value", "cell_tag", "cell_reason")
        state = ("state_sum", "non_null_count", "row_count")
        expected: tuple[str, ...]
        numeric_where = (
            operation == "dsl.j1.where" and len(parameters) == 3 and parameters[0] == "numeric"
        )
        if operation == "dsl.j1.members" or (operation == "dsl.j1.where" and not numeric_where):
            expected = ("member",)
        elif operation in ("dsl.j1.read", "dsl.j1.compare") or numeric_where:
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
        elif operation in ("dsl.j1.summarize", "dsl.j1.correlate_summarize"):
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
                if operation in ("dsl.j1.summarize", "dsl.j1.correlate_summarize")
                and parameters[0] == "count"
                else pa.types.is_float64(physical)
                if operation in ("dsl.j1.summarize", "dsl.j1.correlate_summarize")
                and parameters[0] == "mean"
                else pa.types.is_int64(physical) or pa.types.is_float64(physical)
            )
            if not valid:
                raise _fail("admitted J1 value physical type", str(physical))
        _validate_table(
            self.primary,
            count_observation=(
                operation == "dsl.j1.observe" and "count_observation@v1" in self.root.requirements
            ),
        )
        if operation == "dsl.j1.correlate_summarize":
            method = parameters[0]
            if self.primary.num_rows != 1:
                raise _fail("one current coefficient statistic", "wrong row count")
            row = self.primary.to_pylist()[0]
            value = row["value"]
            amount = row.get("current_sum")
            count = row.get("current_count")
            if method == "count":
                valid = (
                    row["cell_tag"] == "defined" and row["cell_reason"] is None and value == count
                )
            elif method == "sum":
                valid = (
                    row["cell_tag"] == "defined" and row["cell_reason"] is None and value == amount
                )
            else:
                valid = (
                    count == 0
                    and value is None
                    and row["cell_tag"] == "undefined"
                    and row["cell_reason"] == "empty_mean"
                    and amount == 0.0
                ) or (
                    type(count) is int
                    and count > 0
                    and row["cell_tag"] == "defined"
                    and row["cell_reason"] is None
                    and type(amount) is float
                    and type(value) is float
                    and math.isclose(value, amount / count, rel_tol=1e-12, abs_tol=1e-12)
                )
            if not valid:
                raise _fail(
                    "coefficient statistic finished from current support",
                    "inconsistent statistic Cell",
                )
        if len({role for role, _ in self.parts}) != len(self.parts):
            raise _fail("unique retained J1 parts", "duplicate role")
        if operation == "dsl.j1.compare" or numeric_where:
            if (
                tuple(role for role, _ in self.parts)
                != J1_COMPARE_DIFFERENCE.contract.required_parts
            ):
                raise _fail("both exact comparison endpoint parts", "missing endpoint")
            primary = {row["member"]: row for row in self.primary.to_pylist()}
            sides: list[dict[object, dict[str, object]]] = []
            for _role, table in self.parts:
                if (
                    tuple(table.column_names) != ("member", "value", "cell_tag", "cell_reason")
                    or table.schema.field("member").type != self.primary.schema.field("member").type
                    or table.schema.field("value").type != self.primary.schema.field("value").type
                ):
                    raise _fail("matching keyed comparison endpoint schema", "part schema differs")
                _validate_table(table)
                side = {row["member"]: row for row in table.to_pylist()}
                if set(side) != set(primary) or any(
                    row["cell_tag"] != "defined" for row in side.values()
                ):
                    raise _fail("complete Defined endpoint rows", "part domain or Cell differs")
                sides.append(side)
            for member, row in primary.items():
                current = sides[0][member]["value"]
                baseline = sides[1][member]["value"]
                if not isinstance(current, (int, float)) or not isinstance(baseline, (int, float)):
                    raise _fail("finite numeric endpoint values", "non-numeric endpoint")
                _number(current)
                _number(baseline)
                expected_difference = current - baseline
                _number(expected_difference)
                if row["cell_tag"] != "defined" or (
                    row["value"] != expected_difference
                    if type(expected_difference) is int
                    else not math.isclose(
                        row["value"], expected_difference, rel_tol=1e-12, abs_tol=1e-12
                    )
                ):
                    raise _fail("difference from retained exact endpoints", "result differs")
            return
        for role, table in self.parts:
            if role != "coordinate":
                raise _fail("registered coordinate role", role)
            _validate_table(table, part=True)
            if "member" not in self.primary.column_names or (
                table.schema.field("member").type != self.primary.schema.field("member").type
            ):
                raise _fail("coordinate part on the exact member key type", "foreign key type")
            if table.schema.field("state_sum").type != self.primary.schema.field("state_sum").type:
                raise _fail("coordinate part on the exact sum state type", "foreign sum type")
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
                amount = merge_numbers(item[2] for item in components if item[2] is not None)
                if primary["non_null_count"]:
                    original = primary["state_sum"]
                    if pa.types.is_int64(table.schema.field("state_sum").type):
                        if amount != original:
                            raise _fail(
                                "exact integer coordinate sum partition", "state sums differ"
                            )
                    elif not math.isclose(
                        float(amount), float(original), rel_tol=1e-12, abs_tol=1e-12
                    ):
                        raise _fail("bounded float64 coordinate sum partition", "state sums differ")
