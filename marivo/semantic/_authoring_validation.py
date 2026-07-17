"""Pure validation and normalization helpers for semantic authoring.

Internal module: all symbols are private.  The public authoring surface is
re-exported from ``marivo.semantic.authoring``.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import textwrap
from collections.abc import Callable
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from marivo.semantic._authoring_context import _register_authoring_file
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import ErrorKind, SemanticDecoratorError, _raise
from marivo.semantic.ir import (
    Additivity,
    DateParse,
    DatetimeParse,
    HourPrefixParse,
    JoinKey,
    SampleIntervalIR,
    SemanticParse,
    SemiAdditive,
    SqlProvenance,
    StrptimeParse,
    TimeFoldIR,
    TimestampParse,
)


def _validate_metric_provenance(provenance: SqlProvenance | None) -> None:
    if provenance is None:
        return
    if not isinstance(provenance, SqlProvenance):
        _raise(
            ErrorKind.INVALID_REF,
            "metric provenance must be constructed with ms.from_sql(sql=..., dialect=...).",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )


def _validate_time_parse(parse: SemanticParse | None) -> None:
    if parse is None:
        return
    if not isinstance(
        parse,
        (DateParse, DatetimeParse, TimestampParse, StrptimeParse, HourPrefixParse),
    ):
        _raise(
            ErrorKind.INVALID_REF,
            "time_dimension(parse=...) accepts ms.datetime(...), ms.timestamp(...), "
            "ms.strptime(...), ms.hour_prefix(...), or None.",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )


def _validate_relationship_keys(
    keys: tuple[object, ...], *, semantic_id: str
) -> tuple[JoinKey, ...]:
    resolved: list[JoinKey] = []
    for key in keys:
        if not isinstance(key, JoinKey):
            _raise(
                ErrorKind.INVALID_REF,
                "ms.relationship(keys=...) accepts only ms.join_on(from_key, to_key) values.",
                cls=SemanticDecoratorError,
                refs=(semantic_id,),
                constraint_id=ConstraintId.REF_SHAPE,
            )
        resolved.append(key)
    return tuple(resolved)


def _compute_body_ast_hash(fn: Callable[..., Any]) -> str:
    """Compute a SHA-256 hash of the function body AST."""
    try:
        source = inspect.getsource(fn)
        source = textwrap.dedent(source)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body_source = ast.get_source_segment(source, node)
                if body_source is not None:
                    return hashlib.sha256(body_source.encode()).hexdigest()[:16]
        return hashlib.sha256(source.encode()).hexdigest()[:16]
    except (OSError, TypeError, IndentationError):
        return hashlib.sha256(b"<unavailable>").hexdigest()[:16]


def _compute_agg_hash(
    measure_id: str,
    agg: Any,
    fold: TimeFoldIR | None,
    *,
    filter: tuple[tuple[str, Any], ...] | None = None,
) -> str:
    payload = repr(
        {
            "measure": measure_id,
            "agg": agg,
            "fold": (fold.kind, fold.q) if fold else None,
            "filter": filter,
        }
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _validate_unit(unit: str | None, semantic_id: str, object_kind: str = "metric") -> None:
    if unit is None:
        return
    if unit == "" or any(not (0x21 <= ord(ch) <= 0x7E) for ch in unit):
        _raise(
            ErrorKind.INVALID_REF,
            f"{object_kind} {semantic_id!r}: unit must be a non-empty token of printable "
            f"ASCII without whitespace (UCUM case-sensitive code such as 'CNY', "
            f"'%', '1', 'ms', '{{order}}'); got {unit!r}.",
            refs=(semantic_id,),
            cls=SemanticDecoratorError,
        )


def _normalize_sample_interval(
    sample_interval: tuple[int, str] | None,
    *,
    semantic_id: str,
    data_type: str,
    granularity: str,
) -> SampleIntervalIR | None:
    if sample_interval is None:
        return None
    count, unit = sample_interval
    if unit not in {"minute", "hour"} or count <= 0:
        _raise(
            ErrorKind.INVALID_SAMPLE_INTERVAL,
            "sample_interval must use a positive minute or hour interval.",
            refs=(semantic_id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.SAMPLE_INTERVAL_VALID,
        )
    seconds = count * (60 if unit == "minute" else 3600)
    if 86400 % seconds != 0:
        _raise(
            ErrorKind.INVALID_SAMPLE_INTERVAL,
            "sample_interval must divide one day evenly.",
            refs=(semantic_id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.SAMPLE_INTERVAL_VALID,
        )
    if data_type not in {"datetime", "timestamp", "string", "integer"}:
        _raise(
            ErrorKind.INVALID_SAMPLE_INTERVAL,
            "sample_interval is only supported on datetime, timestamp, string, or integer time dimensions.",
            refs=(semantic_id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.SAMPLE_INTERVAL_VALID,
        )
    rank = {
        "second": 0,
        "minute": 1,
        "hour": 2,
        "day": 3,
        "week": 4,
        "month": 5,
        "quarter": 6,
        "year": 7,
    }
    if rank[granularity] > rank[unit]:
        allowed = [g for g, r in sorted(rank.items(), key=lambda kv: kv[1]) if r <= rank[unit]]
        allowed_list = ", ".join(repr(g) for g in allowed)
        _raise(
            ErrorKind.INVALID_SAMPLE_INTERVAL,
            f"time dimension {semantic_id!r}: physical granularity {granularity!r} cannot "
            f"be coarser than sample_interval unit {unit!r}. Set granularity to {unit!r} or "
            f"finer (one of: {allowed_list}).",
            refs=(semantic_id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.SAMPLE_INTERVAL_VALID,
        )
    return SampleIntervalIR(count=count, unit=unit)  # type: ignore[arg-type]


def _validate_timezone(timezone: str) -> None:
    """Validate that timezone is a valid IANA timezone name."""
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        _raise(
            ErrorKind.INVALID_REF,
            f"timezone {timezone!r} is not a valid IANA timezone name.",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )


def _normalize_sample_interval_value(
    sample_interval: tuple[int, Literal["minute", "hour"]] | None,
) -> SampleIntervalIR | None:
    """Normalize a sample_interval tuple from a parse builder, using a synthetic semantic_id."""
    if sample_interval is None:
        return None
    _count, unit = sample_interval
    return _normalize_sample_interval(
        sample_interval,
        semantic_id="<parse>",
        data_type="datetime",
        granularity=unit,
    )


def _normalize_time_fold(
    time_fold: str | tuple[str, float] | None,
    *,
    semantic_id: str,
) -> TimeFoldIR | None:
    if time_fold is None:
        return None
    if isinstance(time_fold, str):
        if time_fold not in {"mean", "min", "max", "first", "last"}:
            _raise(
                ErrorKind.INVALID_TIME_FOLD,
                f"time_fold {time_fold!r} is not supported.",
                refs=(semantic_id,),
                cls=SemanticDecoratorError,
                constraint_id=ConstraintId.TIME_FOLD_VALID,
            )
        return TimeFoldIR(kind=time_fold)  # type: ignore[arg-type]
    kind, q = time_fold
    if kind != "percentile" or not isinstance(q, (float, int)) or not 0 < float(q) < 1:
        _raise(
            ErrorKind.INVALID_TIME_FOLD,
            "percentile time_fold must be ('percentile', q) with 0 < q < 1.",
            refs=(semantic_id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.TIME_FOLD_VALID,
        )
    return TimeFoldIR(kind="percentile", q=float(q))


def _normalize_additivity(additivity: Additivity, *, semantic_id: str) -> Additivity:
    """Validate an Additivity value (literal or SemiAdditive variant)."""
    if isinstance(additivity, SemiAdditive):
        return additivity
    if additivity in ("additive", "non_additive"):
        return additivity
    _raise(
        ErrorKind.INVALID_REF,
        f"Metric {semantic_id!r}: additivity must be 'additive', 'non_additive', "
        "or ms.semi_additive(over=..., fold=...). "
        "Note: use underscores, not hyphens (e.g. 'non_additive', not 'non-additive').",
        cls=SemanticDecoratorError,
        constraint_id=ConstraintId.REF_SHAPE,
    )


def _validate_time_parse_granularity(
    *,
    semantic_id: str,
    granularity: str,
    parse: SemanticParse | None,
) -> None:
    """Validate that the parse variant is compatible with the declared granularity."""
    if parse is None:
        return
    if isinstance(parse, HourPrefixParse) and granularity != "hour":
        _raise(
            ErrorKind.INVALID_REF,
            f"time dimension {semantic_id!r}: ms.hour_prefix(...) requires granularity='hour'.",
            refs=(semantic_id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.TIME_GRANULARITY_PARSE_COMPATIBLE,
        )
    if isinstance(parse, DateParse) and granularity in {"hour", "minute", "second"}:
        _raise(
            ErrorKind.INVALID_REF,
            f"time dimension {semantic_id!r}: granularity={granularity!r} requires ms.datetime(...) or ms.timestamp(...).",
            refs=(semantic_id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.TIME_GRANULARITY_PARSE_COMPATIBLE,
        )
    if isinstance(parse, HourPrefixParse) and granularity in {"minute", "second"}:
        _raise(
            ErrorKind.INVALID_REF,
            f"time dimension {semantic_id!r}: granularity={granularity!r} requires ms.datetime(...) or ms.timestamp(...).",
            refs=(semantic_id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.TIME_GRANULARITY_PARSE_COMPATIBLE,
        )
    if isinstance(parse, StrptimeParse) and granularity in {"hour", "minute", "second"}:
        import re as _re

        fmt = parse.format or ""
        date_directives = {"%Y", "%y", "%m", "%d", "%j", "%U", "%W"}
        time_directives = {"%H", "%I", "%k", "%l", "%M", "%S", "%T", "%p"}
        tokens = set(_re.findall(r"%[a-zA-Z]", fmt))
        if not tokens & time_directives:
            _raise(
                ErrorKind.INVALID_REF,
                f"time dimension {semantic_id!r}: granularity={granularity!r} requires a time-bearing "
                f"format, but strptime format {fmt!r} has no hour/minute/second directives.",
                refs=(semantic_id,),
                cls=SemanticDecoratorError,
                constraint_id=ConstraintId.TIME_GRANULARITY_PARSE_COMPATIBLE,
            )
        if parse.sample_interval is not None and not tokens & date_directives:
            _raise(
                ErrorKind.INVALID_SAMPLE_INTERVAL,
                f"time dimension {semantic_id!r}: sampled strptime format {fmt!r} must include date context.",
                refs=(semantic_id,),
                cls=SemanticDecoratorError,
                constraint_id=ConstraintId.SAMPLE_INTERVAL_VALID,
            )


def _validate_sample_interval_granularity(
    *,
    semantic_id: str,
    granularity: str,
    parse: SemanticParse | None,
) -> None:
    """Validate that sample_interval unit is not coarser than the declared granularity."""
    if parse is None:
        return
    sample_ir: SampleIntervalIR | None = None
    data_type_str: str | None = None
    if isinstance(parse, DatetimeParse):
        sample_ir = parse.sample_interval
        data_type_str = "datetime"
    elif isinstance(parse, TimestampParse):
        sample_ir = parse.sample_interval
        data_type_str = "timestamp"
    elif isinstance(parse, StrptimeParse):
        sample_ir = parse.sample_interval
        data_type_str = "strptime"
    elif isinstance(parse, HourPrefixParse):
        sample_ir = parse.sample_interval
        data_type_str = "hour_prefix"
    if sample_ir is None or data_type_str is None:
        return
    rank = {
        "second": 0,
        "minute": 1,
        "hour": 2,
        "day": 3,
        "week": 4,
        "month": 5,
        "quarter": 6,
        "year": 7,
    }
    if rank.get(granularity, 99) > rank.get(sample_ir.unit, 0):
        allowed = [
            g for g, r in sorted(rank.items(), key=lambda kv: kv[1]) if r <= rank[sample_ir.unit]
        ]
        allowed_list = ", ".join(repr(g) for g in allowed)
        _raise(
            ErrorKind.INVALID_SAMPLE_INTERVAL,
            f"time dimension {semantic_id!r}: physical granularity {granularity!r} cannot "
            f"be coarser than sample_interval unit {sample_ir.unit!r}. Set granularity to {sample_ir.unit!r} or "
            f"finer (one of: {allowed_list}).",
            refs=(semantic_id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.SAMPLE_INTERVAL_VALID,
        )


_register_authoring_file(__file__)
