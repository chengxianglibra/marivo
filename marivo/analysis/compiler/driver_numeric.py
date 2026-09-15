"""Native exact binary64 sums for driver partitions and ordered prefixes."""

from __future__ import annotations

from collections.abc import Callable

import ibis
import ibis.expr.types as ir
from ibis.backends.duckdb import Backend

# A finite binary64 value has at most 1074 fractional decimal digits. Encoding
# this fixed scale as BIGNUM permits an exact sum followed by one binary64 round.
DRIVER_NUMERIC_SETUP_SQL = (
    "CREATE OR REPLACE TEMP MACRO __marivo_driver_float_units(x) AS "
    "CASE WHEN isfinite(x) THEN "
    "CAST(REPLACE(PRINTF('%.1074f', x), '.', '') AS BIGNUM) ELSE NULL END",
    "CREATE OR REPLACE TEMP MACRO __marivo_driver_float_from_units(x) AS "
    "CAST(CAST(x AS VARCHAR) || 'e-1074' AS DOUBLE)",
)


def install_driver_numeric_functions(backend: Backend) -> None:
    """Install fixed native macros inside the owning source connection."""
    for statement in DRIVER_NUMERIC_SETUP_SQL:
        backend.raw_sql(statement)


def _units_signature(value: float) -> str:
    raise NotImplementedError("native BIGNUM handoff signature only")


def _sum_signature(value: str) -> str:
    raise NotImplementedError("native BIGNUM aggregate signature only")


def _float_signature(value: str) -> float:
    raise NotImplementedError("native BIGNUM finalization signature only")


# Ibis has no BIGNUM datatype. The string annotations describe the opaque
# handoff only; these expressions never cast or transfer the engine BIGNUM.
_units: Callable[[ir.Value], ir.StringValue] = ibis.udf.scalar.builtin(
    _units_signature, name="__marivo_driver_float_units"
)
_units_sum: Callable[[ir.Value], ir.StringValue] = ibis.udf.agg.builtin(_sum_signature, name="sum")
_from_units: Callable[[ir.Value], ir.FloatingValue] = ibis.udf.scalar.builtin(
    _float_signature, name="__marivo_driver_float_from_units"
)


def exact_float_sum(value: ir.NumericValue) -> ir.FloatingValue:
    """Sum exact native binary64 expansions with one correctly rounded result."""
    return _from_units(_units_sum(_units(value)))
