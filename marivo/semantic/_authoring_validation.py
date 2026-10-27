"""Pure validation and normalization helpers for semantic authoring.

Internal module: all symbols are private.  The public authoring surface is
re-exported from ``marivo.semantic.authoring``.
"""

from __future__ import annotations

import hashlib
import re
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from marivo.semantic._authoring_context import _register_authoring_file
from marivo.semantic._dsl_authoring import (
    AdditiveAllV1,
    AdditiveOverV1,
    AdditivityPolicy,
    NonAdditiveV1,
)
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import ErrorKind, SemanticDecoratorError, _raise
from marivo.semantic.ir import (
    Additivity,
    DateParse,
    HourPrefixParse,
    JoinKey,
    SampleIntervalIR,
    SemanticParse,
    StrptimeParse,
    TimestampParse,
    is_time_bearing_format,
)


def _validate_time_parse(parse: SemanticParse | None) -> None:
    if parse is None:
        return
    if not isinstance(
        parse,
        (DateParse, TimestampParse, StrptimeParse, HourPrefixParse),
    ):
        _raise(
            ErrorKind.INVALID_REF,
            "time_dimension(parse=...) accepts ms.timestamp(...), "
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


def _compute_column_hash(column: str) -> str:
    """Return a stable definition digest for a direct-column declaration."""
    return hashlib.sha256(f"column:{column}".encode()).hexdigest()[:16]


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


def _normalize_sample_interval_value(
    sample_interval: tuple[int, Literal["minute", "hour"]] | None,
    *,
    semantic_id: str,
    parse: SemanticParse | None,
) -> SampleIntervalIR | None:
    """Validate field-owned cadence and known encoding precision."""
    if sample_interval is None:
        return None
    try:
        if not isinstance(sample_interval, tuple) or len(sample_interval) != 2:
            raise ValueError("sample_interval must be a (count, unit) tuple")
        count, unit = sample_interval
        sample = SampleIntervalIR(count=count, unit=unit)
        if isinstance(parse, StrptimeParse):
            tokens = set(re.findall(r"%[a-zA-Z]", parse.format))
            if not tokens & {"%Y", "%y", "%m", "%d", "%j", "%U", "%W"}:
                raise ValueError("sampled strptime encodings require date context")
            if not is_time_bearing_format(parse.format):
                raise ValueError("DATE encodings cannot represent subday samples")
            if not tokens & {"%H", "%I", "%k", "%l", "%T"}:
                raise ValueError("sampling requires hour-bearing source encoding")
            if unit == "minute" and not tokens & {"%M", "%T"}:
                raise ValueError("minute sampling requires minute-bearing source encoding")
        if isinstance(parse, DateParse):
            raise ValueError("DATE encodings cannot represent subday samples")
        if isinstance(parse, HourPrefixParse) and unit != "hour":
            raise ValueError("hour_prefix encodings can represent only whole-hour samples")
        return sample
    except (TypeError, ValueError) as cause:
        _raise(
            ErrorKind.INVALID_SAMPLE_INTERVAL,
            f"time dimension {semantic_id!r}: {cause}. Declare a representable canonical minute/hour sample_interval on the time field.",
            refs=(semantic_id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.SAMPLE_INTERVAL_VALID,
        )


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


def _normalize_additivity(
    additivity: AdditivityPolicy,
    *,
    semantic_id: str,
    status_time_dimension: str | None = None,
) -> Additivity:
    """Project a closed declaration to the existing execution summary.

    The projection is not authority for a new DSL coordinate fold.
    """
    if type(additivity) not in (AdditiveOverV1, AdditiveAllV1, NonAdditiveV1):
        _raise(
            ErrorKind.INVALID_REF,
            f"{semantic_id!r}: additivity requires ms.additive_over(...), ms.additive_except(...), "
            "ms.additive_all(), or ms.non_additive().",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    if status_time_dimension is not None:
        if type(additivity) is not AdditiveAllV1 or (
            status_time_dimension not in additivity.exceptions
        ):
            _raise(
                ErrorKind.INVALID_REF,
                f"{semantic_id!r}: status time must be fixed by ms.additive_except(...).",
                cls=SemanticDecoratorError,
                constraint_id=ConstraintId.REF_SHAPE,
            )
        return "non_additive"
    if type(additivity) is AdditiveAllV1:
        return "additive" if not additivity.exceptions else "non_additive"
    if type(additivity) is AdditiveOverV1:
        return "non_additive"
    return "non_additive"


_register_authoring_file(__file__)
