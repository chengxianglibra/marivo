"""Once-rounded numeric finishing expressed as source-native Ibis operations."""

from __future__ import annotations

from collections.abc import Callable

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir

_INTEGER = dt.Decimal(38, 0)


def _huge_definition(value: ir.Value, prototype: ir.Value) -> ir.Value:
    """DuckDB HUGEINT carrier selected by a native integer SUM prototype."""
    raise NotImplementedError("SQL builtin")


def _quotient_definition(numerator: ir.Value, denominator: ir.Value) -> ir.Value:
    """Native truncating integer division, avoiding Decimal-to-double division."""
    raise NotImplementedError("SQL builtin")


_huge: Callable[[ir.Value, ir.Value], ir.Value] = ibis.udf.scalar.builtin(
    name="cast_to_type", signature=((_INTEGER, dt.int64), _INTEGER)
)(_huge_definition)
_native_quotient: Callable[[ir.Value, ir.Value], ir.Value] = ibis.udf.scalar.builtin(
    name="divide", signature=((_INTEGER, _INTEGER), _INTEGER)
)(_quotient_definition)


def _quotient(numerator: ir.Value, denominator: ir.Value) -> ir.Value:
    prototype = ibis.literal(0, type="int64").name("zero").as_table()
    zero = prototype.aggregate(zero=prototype.zero.sum()).zero.as_scalar()
    return _native_quotient(_huge(numerator, zero), _huge(denominator, zero))


def decimal_divide(
    table: ir.Table, numerator: str, denominator: str, result: dt.Decimal
) -> ir.Table:
    """Add ``numeric_result`` with one HALF_EVEN finish, preserving all input digits.

    Only grouped states enter this expression. Long division runs entirely in SQL;
    no contribution vectors or intermediate states are collected in Python.
    """
    ntype, dtype = table[numerator].type(), table[denominator].type()
    nscale = ntype.scale if isinstance(ntype, dt.Decimal) else 0
    dscale = dtype.scale if isinstance(dtype, dt.Decimal) else 0
    assert nscale is not None and dscale is not None and result.scale is not None
    places = result.scale + dscale - nscale
    if places < 0:
        raise ValueError("Decimal output scale cannot discard numerator digits before division")
    zero_table = ibis.literal(0, type="int64").name("zero").as_table()
    huge_zero = zero_table.aggregate(zero=zero_table.zero.sum()).zero.as_scalar()

    def coefficient(value: ir.Value) -> ir.Value:
        return _huge(value.cast("string").replace(".", "").cast(_INTEGER), huge_zero)

    table = table.mutate(
        numeric_n=coefficient(table[numerator]),
        numeric_d=coefficient(table[denominator]),
    )
    table = table.mutate(
        numeric_sign=(table.numeric_n < 0) != (table.numeric_d < 0),
        numeric_n=table.numeric_n.abs(),
        numeric_d=(table.numeric_d == 0).ifelse(1, table.numeric_d.abs()),
    )
    table = table.mutate(
        numeric_q=_quotient(table.numeric_n, table.numeric_d),
        numeric_r=table.numeric_n % table.numeric_d,
        numeric_d10=_quotient(table.numeric_d, ibis.literal(10, type=_INTEGER)),
        numeric_dm=table.numeric_d % 10,
    )
    for _ in range(places):
        # Compare against ceil(d*k/10) without ever forming d*10. Intermediate
        # remainders fit HUGEINT even at Decimal(38,0)'s boundary.
        table = table.mutate(
            numeric_digit=ibis.cases(
                *(
                    (
                        table.numeric_r
                        >= table.numeric_d10 * digit
                        + _quotient(table.numeric_dm * digit + 9, ibis.literal(10, type=_INTEGER)),
                        digit,
                    )
                    for digit in range(9, 0, -1)
                ),
                else_=0,
            )
        )
        table = table.mutate(
            numeric_q=table.numeric_q * 10 + table.numeric_digit,
            numeric_r=(table.numeric_r - table.numeric_d10 * table.numeric_digit) * 10
            - table.numeric_dm * table.numeric_digit,
        )
    table = table.mutate(
        numeric_q=table.numeric_q
        + (
            (table.numeric_r > table.numeric_d - table.numeric_r)
            | ((table.numeric_r == table.numeric_d - table.numeric_r) & (table.numeric_q % 2 != 0))
        ).cast("int64")
    )
    digits = table.numeric_q.cast("string")
    padded = digits.lpad(ibis.greatest(digits.length(), result.scale + 1), "0")
    rendered = (
        padded.substr(0, padded.length() - result.scale)
        + "."
        + padded.substr(padded.length() - result.scale)
        if result.scale
        else padded
    )
    return table.mutate(numeric_result=(table.numeric_sign.ifelse("-", "") + rendered).cast(result))


def _epoch_definition(value: ir.Value) -> ir.Value:
    raise NotImplementedError("SQL builtin")


def _part_definition(part: ir.Value, value: ir.Value) -> ir.Value:
    raise NotImplementedError("SQL builtin")


duration_ticks: Callable[[ir.Value], ir.Value] = ibis.udf.scalar.builtin(
    name="epoch_us", signature=((dt.Interval("us"),), dt.int64)
)(_epoch_definition)
interval_part: Callable[[ir.Value, ir.Value], ir.Value] = ibis.udf.scalar.builtin(
    name="date_part", signature=((dt.string, dt.Interval("us")), dt.int64)
)(_part_definition)


def integer_divide(table: ir.Table, numerator: str, denominator: str) -> ir.Table:
    """Round an int64 ratio to binary64 once using native HUGEINT arithmetic."""
    prototype = ibis.literal(0, type="int64").name("zero").as_table()
    zero = prototype.aggregate(zero=prototype.zero.sum()).zero.as_scalar()
    table = table.mutate(
        binary_n=_huge(table[numerator], zero),
        binary_d=_huge(table[denominator], zero),
    )
    table = table.mutate(
        binary_negative=(table.binary_n < 0) != (table.binary_d < 0),
        binary_n=table.binary_n.abs(),
        binary_d=(table.binary_d == 0).ifelse(1, table.binary_d.abs()),
    )
    # The estimate only selects a binade; exact integer comparisons correct it.
    table = table.mutate(
        binary_e=(ibis.greatest(table.binary_n, 1).cast("float64") / table.binary_d.cast("float64"))
        .log2()
        .floor()
        .cast("int64")
    )

    def power(exponent: ir.Value) -> ir.Value:
        return _huge((ibis.literal(2.0) ** exponent).cast(_INTEGER), zero)

    table = table.mutate(
        binary_left=table.binary_n * power(ibis.greatest(-table.binary_e, 0)),
        binary_right=table.binary_d * power(ibis.greatest(table.binary_e, 0)),
    )
    table = table.mutate(
        binary_e=table.binary_e
        + ibis.cases(
            (table.binary_left < table.binary_right, -1),
            (table.binary_left >= table.binary_right * 2, 1),
            else_=0,
        )
    )
    table = table.mutate(binary_shift=52 - table.binary_e)
    table = table.mutate(
        binary_n=table.binary_n * power(ibis.greatest(table.binary_shift, 0)),
        binary_d=table.binary_d * power(ibis.greatest(-table.binary_shift, 0)),
    )
    table = table.mutate(
        binary_q=_quotient(table.binary_n, table.binary_d),
        binary_r=table.binary_n % table.binary_d,
    )
    table = table.mutate(
        binary_q=table.binary_q
        + (
            (table.binary_r > table.binary_d - table.binary_r)
            | ((table.binary_r == table.binary_d - table.binary_r) & (table.binary_q % 2 != 0))
        ).cast("int64")
    )
    return table.mutate(
        numeric_result=table.binary_q.cast("float64")
        * (ibis.literal(2.0) ** -table.binary_shift)
        * table.binary_negative.ifelse(-1.0, 1.0)
    )
