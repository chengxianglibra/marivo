"""Execute the analysis laws over finite bags and complete coordinates."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from decimal import Decimal, localcontext
from fractions import Fraction
from math import isfinite
from typing import Literal, TypeAlias

from tests.formal.reference.model import (
    Bag,
    Binary,
    Branch,
    Coalesce,
    Column,
    DemandMask,
    Expression,
    FailureKind,
    IfElse,
    Key,
    LiteralValue,
    Number,
    Obligation,
    Occurrence,
    Read,
    Reducer,
    Result,
    Scope,
    Unary,
    Value,
    key_rank,
    observation_key_rank,
    valid_nan,
)

Lookup: TypeAlias = Callable[[str, Key], Value]


def _number(value: Value) -> Number:
    assert isinstance(value, (int, float, Fraction, Decimal))
    return value


def _pair_carriers(left: Number, right: Number) -> tuple[float, float] | tuple[Fraction, Fraction]:
    if isinstance(left, float) or isinstance(right, float):
        return float(left), float(right)
    return Fraction(left), Fraction(right)


def _total(values: Iterable[Value]) -> Number:
    total: Number = 0
    for value in values:
        left, right = _pair_carriers(total, _number(value))
        total = left + right
    return total


def _quotient(numerator: Number, denominator: Number) -> Value:
    if denominator == 0:
        return None
    left, right = _pair_carriers(numerator, denominator)
    return left / right


def _ordered(reducer: Reducer, bag: Bag) -> tuple[Value, FailureKind | None]:
    times = tuple(row.time for row in bag)
    if None in times:
        return None, "missing_order_key"
    if len(set(times)) != len(times):
        return None, "duplicate_order_key"
    available = tuple(
        (time, row.value)
        for row in bag
        if (time := row.time) is not None and (reducer.include_null or row.value is not None)
    )
    extreme = min if reducer.kind == "first" else max
    return (extreme(available, key=lambda item: item[0])[1] if available else None), None


def _pair(reducer: Reducer, bag: Bag) -> Value:
    paired = tuple(
        (_number(row.value), _number(row.weight))
        for row in bag
        if row.value is not None and row.weight is not None
    )
    if reducer.kind == "weighted_mean":
        products = (a * b for x, y in paired for a, b in (_pair_carriers(x, y),))
        return _quotient(_total(products), _total(y for _, y in paired))
    if len(paired) < 2:
        return None
    first_x, first_y = paired[0]
    if all(x == first_x for x, _ in paired) or all(y == first_y for _, y in paired):
        return None
    # The coefficient's native convention drops NaN pairs after the input
    # pair count and exact-constant checks. Infinity remains a numeric value.
    candidates = tuple((x, y) for x, y in paired if not valid_nan(x) and not valid_nan(y))
    if len(candidates) < 2 or any(
        isinstance(value, float) and not isfinite(value) for item in candidates for value in item
    ):
        return float("nan")
    # Form exact centered moments before the one irrational operation.
    xs, ys = zip(*((Fraction(left), Fraction(right)) for left, right in candidates), strict=True)
    avg_x, avg_y = sum(xs) / len(xs), sum(ys) / len(ys)
    dx, dy = [item - avg_x for item in xs], [item - avg_y for item in ys]
    squared = sum(item * item for item in dx) * sum(item * item for item in dy)
    if not squared:
        return float("nan")
    cross = sum(x * y for x, y in zip(dx, dy, strict=True))
    ratio = cross * cross / squared
    with localcontext() as context:
        context.prec = 80
        magnitude = (Decimal(ratio.numerator) / Decimal(ratio.denominator)).sqrt()
    return float(magnitude.copy_negate() if cross < 0 else magnitude)


def _quantile(values: Sequence[Value], q: Fraction) -> Value:
    ordered = sorted(values, key=lambda value: key_rank((value,)))
    offset = (len(ordered) - 1) * q
    index = offset.numerator // offset.denominator
    weight = offset - index
    if not weight:
        return ordered[index]
    left, right = _pair_carriers(_number(ordered[index]), _number(ordered[index + 1]))
    return left + weight * (right - left)


def reduce_bag(reducer: Reducer, bag: Bag) -> tuple[Value, FailureKind | None]:
    """Complete one declared reducer on an ordered collection of bag occurrences."""
    value, failure = _finish_bag(reducer, bag)
    if failure is not None:
        return value, failure
    if reducer.strict and any(
        row.value is None or (reducer.kind == "weighted_mean" and row.weight is None) for row in bag
    ):
        return value, "strict_null"
    return value, None


def _finish_bag(reducer: Reducer, bag: Bag) -> tuple[Value, FailureKind | None]:
    kind = reducer.kind
    if kind in ("first", "last"):
        return _ordered(reducer, bag)
    if kind == "row_count":
        return len(bag), None
    values = tuple(row.value for row in bag if row.value is not None)
    if kind in ("weighted_mean", "corr"):
        return _pair(reducer, bag), None
    if kind == "count":
        return len(values), None
    if kind == "count_distinct":
        return len({key_rank((value,)) for value in values}), None
    if not values:
        return (0 if kind == "sum" and not bag else None), None
    if kind == "sum":
        return _total(values), None
    if kind == "mean":
        return _quotient(_total(values), len(values)), None
    if kind in ("median", "quantile"):
        return _quantile(values, reducer.quantile if kind == "quantile" else Fraction(1, 2)), None
    extreme = min if kind == "min" else max
    return extreme(values, key=lambda value: key_rank((value,))), None


def support(
    branches: Iterable[Branch], scope: Scope
) -> tuple[tuple[Key, ...], frozenset[Obligation]]:
    """Find the public key set before any value, branch demand or sample completion."""
    if scope.mode == "scalar":
        return ((),), frozenset()
    if scope.mode == "explicit":
        domain = set(scope.population)
        needed = {
            key
            for branch in branches
            for key in (
                branch.required_keys
                if branch.required_keys is not None
                else frozenset(key for key, bag in branch.contributions.items() if bag)
            )
        }
        missing = needed - domain
        return tuple(sorted(domain, key=observation_key_rank)), frozenset(
            Obligation("coverage", key) for key in missing
        )
    keys = {
        key
        for branch in branches
        if scope.mode != "calendar" or branch.timed
        for key, bag in branch.contributions.items()
        if bag
    }
    return tuple(sorted(keys, key=observation_key_rank)), frozenset()


def _branch_key(branch: Branch, key: Key, calendar_position: int | None) -> Key:
    if calendar_position is None or branch.timed:
        return key
    return (*key[:calendar_position], *key[calendar_position + 1 :])


def _binary(expression: Binary, left: Value, right: Value) -> Value:
    operation = expression.operation
    if operation == "and":
        return False if left is False or right is False else None if None in (left, right) else True
    if operation == "or":
        return True if left is True or right is True else None if None in (left, right) else False
    if left is None or right is None:
        return None
    if operation == "eq":
        return left == right
    if operation == "lt":
        if valid_nan(left) or valid_nan(right):
            return False
        return key_rank((left,)) < key_rank((right,))
    if operation == "divide":
        return _quotient(_number(left), _number(right))
    a, b = _pair_carriers(_number(left), _number(right))
    if operation == "add":
        return a + b
    if operation == "subtract":
        return a - b
    return a * b


def evaluate(expression: Expression, key: Key, lookup: Lookup) -> Value:
    """Evaluate only the branches whose current parent demands their value."""
    if isinstance(expression, LiteralValue):
        return expression.value
    if isinstance(expression, Read):
        return lookup(expression.name, key)
    if isinstance(expression, IfElse):
        condition = evaluate(expression.condition, key, lookup)
        branch = expression.yes if condition is True else expression.otherwise
        return evaluate(branch, key, lookup)
    if isinstance(expression, Coalesce):
        value = evaluate(expression.left, key, lookup)
        return evaluate(expression.right, key, lookup) if value is None else value
    if isinstance(expression, Unary):
        value = evaluate(expression.operand, key, lookup)
        return (
            value is None
            if expression.operation == "is_null"
            else (None if value is None else not value)
        )
    left = evaluate(expression.left, key, lookup)
    right = evaluate(expression.right, key, lookup)
    return _binary(expression, left, right)


def observe(
    branches: Mapping[str, Branch], expressions: Mapping[str, Expression], scope: Scope
) -> Result:
    """Compute all four objects for the same finite observation."""
    keys, coverage = support(branches.values(), scope)
    obligations = set(coverage)
    contributions = {
        name: {
            key: branch.contributions.get(_branch_key(branch, key, scope.calendar_position), ())
            for key in keys
        }
        for name, branch in branches.items()
    }

    def read(name: str, key: Key) -> Value:
        value, failure = reduce_bag(branches[name].reducer, contributions[name][key])
        if failure is not None:
            obligations.add(Obligation(failure, key))
        return value

    values = {
        name: Column.from_values({key: evaluate(expression, key, read) for key in keys})
        for name, expression in expressions.items()
    }
    return Result(keys, contributions, values, frozenset(obligations))


def _pull_column(column: Column, mapping: Mapping[Key, Key]) -> Column:
    return Column({new: column.x[old] for new, old in mapping.items() if old in column.x})


def pull_back(result: Result, mapping: Mapping[Key, Key]) -> Result:
    """Reindex existing values, preserving obligations from the completed input."""
    return Result(
        tuple(mapping),
        {
            name: {new: bags.get(old, ()) for new, old in mapping.items()}
            for name, bags in result.contributions.items()
        },
        {name: _pull_column(column, mapping) for name, column in result.columns.items()},
        result.obligations,
        {
            name: frozenset(new for new, old in mapping.items() if old in present)
            for name, present in result.presence.items()
        },
    )


def where(result: Result, expression: Expression) -> Result:
    def value(name: str, key: Key) -> Value:
        return result.columns[name].at(key)

    selected: DemandMask = frozenset(
        key for key in result.keys if evaluate(expression, key, value) is True
    )
    return pull_back(result, {key: key for key in result.keys if key in selected})


def select(result: Result, expressions: Mapping[str, Expression]) -> Result:
    def read(name: str, key: Key) -> Value:
        return result.columns[name].at(key)

    return replace(
        result,
        columns={
            name: Column.from_values({key: evaluate(expr, key, read) for key in result.keys})
            for name, expr in expressions.items()
        },
    )


def fix(result: Result, axis: int, value: Value) -> Result:
    return pull_back(
        result,
        {
            (*key[:axis], *key[axis + 1 :]): key
            for key in result.keys
            if key_rank((key[axis],)) == key_rank((value,))
        },
    )


def top(result: Result, size: int, score: str, *, descending: bool = True) -> Result:
    def score_order(key: Key) -> tuple[int, Number]:
        value = result.columns[score].at(key)
        if value is None:
            return 2, 0
        if valid_nan(value):
            return 1, 0
        number = _number(value)
        return 0, -number if descending else number

    ordered = sorted(sorted(result.keys, key=key_rank), key=score_order)
    return pull_back(result, {key: key for key in ordered[:size]})


def push_forward(
    result: Result, axes: tuple[int, ...], reductions: Mapping[str, tuple[Reducer, str]]
) -> Result:
    """Reduce current rows along the projected axes and erase row presence."""
    grouped: dict[Key, list[Key]] = {}
    for key in result.keys:
        grouped.setdefault(tuple(key[index] for index in axes), []).append(key)
    if not axes:
        grouped.setdefault((), [])
    bags = {
        name: {
            key: tuple(Occurrence(old, result.columns[column].at(old)) for old in rows)
            for key, rows in grouped.items()
        }
        for name, (_, column) in reductions.items()
    }
    keys = tuple(sorted(grouped, key=key_rank))
    reduced = observe(
        {name: Branch(reducer, bags[name]) for name, (reducer, _) in reductions.items()},
        {name: Read(name) for name in reductions},
        Scope("explicit", keys),
    )
    ordered = pull_back(reduced, {key: key for key in keys})
    return replace(ordered, obligations=ordered.obligations | result.obligations)


def over(result: Result, axes: tuple[int, ...], reducer: Reducer, column: str) -> Result:
    reduced = push_forward(result, axes, {column: (reducer, column)})
    return replace(
        result,
        columns={
            **result.columns,
            column: _pull_column(
                reduced.columns[column],
                {key: tuple(key[index] for index in axes) for key in result.keys},
            ),
        },
        obligations=reduced.obligations,
    )


def align(
    left: Result,
    right: Result,
    left_mapping: Mapping[Key, Key] | None = None,
    right_mapping: Mapping[Key, Key] | None = None,
) -> Result:
    lhs = pull_back(left, {key: key for key in left.keys} if left_mapping is None else left_mapping)
    rhs = pull_back(
        right, {key: key for key in right.keys} if right_mapping is None else right_mapping
    )
    keys = tuple(dict.fromkeys((*lhs.keys, *rhs.keys)))
    values: dict[str, Column] = {}
    presence: dict[str, frozenset[Key]] = {}
    contributions: dict[str, Mapping[Key, Bag]] = {}
    for prefix, result in (("left", lhs), ("right", rhs)):
        presence[f"{prefix}_present"] = frozenset(result.keys)
        presence.update({f"{prefix}.{name}": keys for name, keys in result.presence.items()})
        values.update({f"{prefix}.{name}": column for name, column in result.columns.items()})
        contributions.update(
            {f"{prefix}.{name}": value for name, value in result.contributions.items()}
        )
    return Result(keys, contributions, values, left.obligations | right.obligations, presence)


def compare(
    left: Result,
    baseline: Result,
    correspondence: Mapping[Value, Value],
    time_position: int,
    *,
    current_times: frozenset[Value],
    mode: Literal["absolute", "relative"],
) -> Result:
    uses = Counter(correspondence.values())
    invalid_times = {
        source
        for source, target in correspondence.items()
        if target not in current_times or uses[target] != 1
    }
    mapping: dict[Key, Key] = {}
    errors = {Obligation("invalid_correspondence", (source,)) for source in invalid_times}
    for key in baseline.keys:
        time = key[time_position]
        if time not in correspondence or time in invalid_times:
            errors.add(Obligation("invalid_correspondence", (time,)))
            continue
        target = (*key[:time_position], correspondence[time], *key[time_position + 1 :])
        if target in mapping:
            errors.add(Obligation("invalid_correspondence", (time,)))
            continue
        mapping[target] = key
    aligned = align(left, baseline, right_mapping=mapping)
    units: dict[str, Column] = {}
    for name in left.columns:
        if name not in baseline.columns:
            continue
        current, prior = aligned.columns[f"left.{name}"], aligned.columns[f"right.{name}"]
        values = {}
        for key in aligned.keys:
            now, before = current.at(key), prior.at(key)
            if now is None or before is None:
                continue
            a, b = _pair_carriers(_number(now), _number(before))
            values[key] = a - b if mode == "absolute" else _quotient(a - b, -b if b < 0 else b)
        units[f"{mode}.{name}"] = Column.from_values(values)
    return replace(
        aligned, columns={**aligned.columns, **units}, obligations=aligned.obligations | errors
    )
