"""Value-object constructors for semantic authoring.

Internal module: public symbols are re-exported from
``marivo.semantic.authoring``.
"""

from __future__ import annotations

from collections.abc import Sequence as _Sequence
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from marivo.refs import SemanticRef, SymbolKind
from marivo.semantic._authoring_context import (
    _register_authoring_file,
    _require_ref_id,
    _user_caller_location,
)
from marivo.semantic._authoring_validation import (
    _normalize_sample_interval_value,
    _normalize_time_fold,
    _validate_timezone,
)
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import ErrorKind, SemanticDecoratorError, _raise
from marivo.semantic.ir import (
    AiContextIR,
    DatetimeParse,
    HourPrefixParse,
    JoinKey,
    SemiAdditive,
    SnapshotVersioningIR,
    SqlProvenance,
    StrptimeParse,
    TimestampParse,
    ValidityVersioningIR,
    is_time_bearing_format,
)
from marivo.semantic.refs import DimensionRef, TimeDimensionRef, make_ref
from marivo.semantic.time_format import normalize_strptime
from marivo.semantic.typing import AiContextValue


def ai_context(
    *,
    business_definition: str | None = None,
    guardrails: _Sequence[str] | None = None,
) -> AiContextValue:
    """Construct a validated AiContext for semantic objects.

    Provides typed, IDE-friendly construction of AI context with eager
    validation.  Invalid key names are caught at call time by Python's
    keyword argument checking; value-type mismatches raise
    ``SemanticDecoratorError`` with ``[invalid_ai_context]`` including
    the caller's file and line.

    Args:
        business_definition: Plain-language description of what the object represents.
        guardrails: Constraints on how the object should be used.

    Returns:
        A validated ``AiContextValue`` for use with ``ai_context=`` parameters.

    Example:
        >>> ctx = ms.ai_context(
        ...     business_definition="Total revenue from all orders",
        ...     guardrails=["Do not use for margin calculations"],
        ... )
        >>> revenue = ms.aggregate(name="revenue", measure=amount, agg="sum", ai_context=ctx)

    Raises:
        SemanticDecoratorError: If any value has the wrong type.
    """
    location = _user_caller_location()

    if guardrails is not None and (
        not isinstance(guardrails, list | tuple)
        or not all(isinstance(item, str) for item in guardrails)
    ):
        _raise(
            ErrorKind.INVALID_AI_CONTEXT,
            "ms.ai_context(guardrails=...) requires list[str] or tuple[str, ...], "
            f"got {type(guardrails).__name__}.",
            cls=SemanticDecoratorError,
            location=location,
        )

    if business_definition is not None and not isinstance(business_definition, str):
        _raise(
            ErrorKind.INVALID_AI_CONTEXT,
            "ms.ai_context(business_definition=...) requires str, "
            f"got {type(business_definition).__name__}.",
            cls=SemanticDecoratorError,
            location=location,
        )

    return AiContextValue(
        business_definition=business_definition,
        guardrails=tuple(guardrails) if guardrails is not None else (),
    )


def _build_ai_context(ai_context: AiContextValue | None) -> AiContextIR:
    """Convert a validated AiContextValue into an AiContextIR.

    Rejects raw dicts with a teachable error directing the user to
    ``ms.ai_context(...)``.  Since ``AiContextValue`` is validated at
    construction time by ``ms.ai_context()`` or ``__post_init__``, no
    further validation is needed for genuine ``AiContextValue`` instances.
    """
    if ai_context is None:
        return AiContextIR()
    if not isinstance(ai_context, AiContextValue):
        _raise(
            ErrorKind.INVALID_AI_CONTEXT,
            "ai_context= expects an AiContextValue from ms.ai_context(...), "
            "not a raw dict. Construct it explicitly with "
            "ms.ai_context(business_definition=..., guardrails=[...]). "
            "summary= and other unsupported metadata keys are not accepted.",
            cls=SemanticDecoratorError,
        )
    return AiContextIR(
        business_definition=ai_context.business_definition,
        guardrails=ai_context.guardrails,
    )


def snapshot(
    *,
    partition_field: DimensionRef | TimeDimensionRef,
    grain: Literal["day"],
    timezone: str | None = None,
    format: str | None = None,
) -> SnapshotVersioningIR:
    """Declare daily snapshot partition versioning for an entity."""
    partition_ref = _require_ref_id(
        partition_field,
        parameter="partition_field",
        expected=(DimensionRef, TimeDimensionRef),
    )
    if grain != "day":
        _raise(
            ErrorKind.INVALID_REF,
            "snapshot versioning currently supports only grain='day'.",
            cls=SemanticDecoratorError,
        )
    if timezone is not None:
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            _raise(
                ErrorKind.INVALID_REF,
                f"timezone {timezone!r} is not a valid IANA timezone name.",
                cls=SemanticDecoratorError,
            )
    return SnapshotVersioningIR(
        kind="snapshot",
        partition_field=partition_ref,
        grain="day",
        timezone=timezone,
        format=format,
    )


def validity(
    *,
    valid_from: DimensionRef | TimeDimensionRef,
    valid_to: DimensionRef | TimeDimensionRef,
    interval: Literal["closed_open", "closed_closed"],
    open_end: tuple[str | None, ...],
    timezone: str | None = None,
) -> ValidityVersioningIR:
    """Declare SCD2 validity interval versioning for an entity.

    Args:
        valid_from: Dimension or time-dimension ref for the interval start column.
        valid_to: Dimension or time-dimension ref for the interval end column.
        interval: ``"closed_open"`` (``[valid_from, valid_to)``) or
            ``"closed_closed"`` (``[valid_from, valid_to]``).
        open_end: Non-empty tuple of sentinel values that mean "still current"
            in the ``valid_to`` column. Use ``None`` for SQL NULL, or a string
            sentinel such as ``"9999-12-31"``.
        timezone: Optional IANA timezone name for anchor date casting.

    Returns:
        A ``ValidityVersioningIR`` for use in ``ms.entity(versioning=...)``.

    Raises:
        SemanticDecoratorError: ``interval`` is not one of the two allowed values,
            ``open_end`` is empty, or ``timezone`` is not a valid IANA name.
    """
    if interval not in ("closed_open", "closed_closed"):
        _raise(
            ErrorKind.INVALID_ENTITY_VERSIONING,
            f"validity versioning interval must be 'closed_open' or 'closed_closed', "
            f"got {interval!r}.",
            cls=SemanticDecoratorError,
            details={"field": "interval", "reason": f"unsupported interval value {interval!r}"},
        )
    if not open_end:
        _raise(
            ErrorKind.INVALID_ENTITY_VERSIONING,
            "validity versioning open_end must be a non-empty tuple.",
            cls=SemanticDecoratorError,
            details={"field": "open_end", "reason": "empty tuple is not allowed"},
        )
    if timezone is not None:
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            _raise(
                ErrorKind.INVALID_ENTITY_VERSIONING,
                f"timezone {timezone!r} is not a valid IANA timezone name.",
                cls=SemanticDecoratorError,
                details={"field": "timezone", "reason": f"unknown IANA timezone {timezone!r}"},
            )
    valid_from_ref = _require_ref_id(
        valid_from,
        parameter="valid_from",
        expected=(DimensionRef, TimeDimensionRef),
    )
    valid_to_ref = _require_ref_id(
        valid_to,
        parameter="valid_to",
        expected=(DimensionRef, TimeDimensionRef),
    )
    return ValidityVersioningIR(
        kind="validity",
        valid_from=valid_from_ref,
        valid_to=valid_to_ref,
        interval=interval,
        open_end=open_end,
        timezone=timezone,
    )


def semi_additive(
    *,
    over: TimeDimensionRef,
    fold: str | tuple[Literal["percentile"], float],
) -> SemiAdditive:
    """Declare a semi-additive nature: additive off the ``over`` time axis, folded by ``fold``.

    ``over`` must be a ``TimeDimensionRef`` returned by ``@ms.time_dimension``.
    Use as the ``additivity=`` value on a measure or a metric::

        @ms.measure(entity=inventory,
                    additivity=ms.semi_additive(over=snapshot_date, fold="last"))
        def quantity(inventory):
            return inventory.qty
    """
    if not isinstance(over, TimeDimensionRef):
        received = getattr(over, "id", over)
        _raise(
            ErrorKind.INVALID_REF,
            "ms.semi_additive(...) over must be a TimeDimensionRef returned by "
            f"@ms.time_dimension(...); got {type(over).__name__}: {received!r}.",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    over_id = over.id
    fold_ir = _normalize_time_fold(fold, semantic_id=over_id)
    if fold_ir is None:
        _raise(
            ErrorKind.INVALID_REF,
            "ms.semi_additive(...) requires a fold (e.g. 'last', 'max', ('percentile', 0.9)).",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    return SemiAdditive(over=over_id, fold=fold_ir)


def ref(id: str) -> SemanticRef:
    """Return a typed semantic ref for forward / cross-domain references.

    Prefer importing refs returned by authoring calls. Use this explicit
    fallback only for generated definitions, forward references, import cycles,
    or protected model boundaries.
    """
    if not isinstance(id, str):
        _raise(
            ErrorKind.INVALID_REF,
            "ms.ref(...) requires a string in '<kind>.<semantic_id>' format.",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    kind_raw, separator, semantic_id = id.partition(".")
    if not separator or not kind_raw or not semantic_id or ".." in semantic_id:
        _raise(
            ErrorKind.INVALID_REF,
            "ms.ref(...) requires '<kind>.<semantic_id>', for example "
            "'metric.sales.revenue' or 'dimension.sales.orders.region'.",
            refs=(id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    try:
        kind = SymbolKind(kind_raw)
    except ValueError:
        allowed = ", ".join(sorted(k.value for k in SymbolKind))
        _raise(
            ErrorKind.INVALID_REF,
            f"ms.ref(...) kind {kind_raw!r} is not supported; expected one of: {allowed}.",
            refs=(id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    try:
        return make_ref(semantic_id, kind)
    except ValueError as exc:
        _raise(
            ErrorKind.INVALID_REF,
            f"ms.ref(...) could not build {kind.value} ref from {semantic_id!r}: {exc}",
            refs=(id,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )


def from_sql(*, sql: str, dialect: str) -> SqlProvenance:
    """Declare SQL parity provenance for a Python metric body.

    Use as the ``provenance=`` value on ``@ms.metric(...)``::

        @ms.metric(entities=[orders], additivity="additive",
                   provenance=ms.from_sql(sql="select sum(amount) from orders", dialect="duckdb"))
        def revenue(orders_table):
            return orders_table.amount.sum()
    """
    return SqlProvenance(sql=sql, dialect=dialect)


def join_on(
    from_key: DimensionRef | TimeDimensionRef,
    to_key: DimensionRef | TimeDimensionRef,
    /,
) -> JoinKey:
    """Build one relationship key pair for ``ms.relationship(keys=[...])``.

    Each call creates one (from_key, to_key) pairing. Pass a list of
    ``ms.join_on(...)`` calls to ``keys=``.

    Example::

        ms.relationship(
            name="orders_to_customers",
            from_entity=orders, to_entity=customers,
            keys=[ms.join_on(customer_id, id)],
        )
    """
    return JoinKey(
        from_key=_require_ref_id(
            from_key,
            parameter="from_key",
            expected=(DimensionRef, TimeDimensionRef),
        ),
        to_key=_require_ref_id(
            to_key,
            parameter="to_key",
            expected=(DimensionRef, TimeDimensionRef),
        ),
    )


def datetime(
    *,
    timezone: str | None = None,
    sample_interval: tuple[int, Literal["minute", "hour"]] | None = None,
) -> DatetimeParse:
    """Declare an already-temporal datetime column parse.

    Use as the ``parse=`` value on ``@ms.time_dimension(...)`` when the
    source column is a native datetime type.

    Args:
        timezone: Optional IANA timezone name. Declare it for naive source
            columns; otherwise readiness blocks analysis because runtime would
            interpret values in the datasource read timezone.
        sample_interval: Optional periodic sampling interval for sampled time
            dimensions, e.g. ``(5, "minute")`` or ``(1, "hour")``.

    Returns:
        A ``DatetimeParse`` value object.

    Raises:
        SemanticDecoratorError: ``timezone`` is not a valid IANA name.

    Example:
        >>> @ms.time_dimension(entity=events, granularity="minute",
        ...                    parse=ms.datetime(timezone="UTC"))
        ... def ts(events):
        ...     return events.ts
    """
    if timezone is not None:
        _validate_timezone(timezone)
    return DatetimeParse(
        timezone=timezone,
        sample_interval=_normalize_sample_interval_value(sample_interval),
    )


def timestamp(
    *,
    timezone: str | None = None,
    sample_interval: tuple[int, Literal["minute", "hour"]] | None = None,
) -> TimestampParse:
    """Declare an already-temporal timestamp column parse.

    Use as the ``parse=`` value on ``@ms.time_dimension(...)`` when the
    source column is a native timestamp type.

    Args:
        timezone: Optional IANA timezone name. Declare it for naive source
            columns; otherwise readiness blocks analysis because runtime would
            interpret values in the datasource read timezone.
        sample_interval: Optional periodic sampling interval for sampled time
            dimensions, e.g. ``(5, "minute")`` or ``(1, "hour")``.

    Returns:
        A ``TimestampParse`` value object.

    Raises:
        SemanticDecoratorError: ``timezone`` is not a valid IANA name.

    Example:
        >>> @ms.time_dimension(entity=events, granularity="second",
        ...                    parse=ms.timestamp(timezone="UTC"))
        ... def ts(events):
        ...     return events.ts
    """
    if timezone is not None:
        _validate_timezone(timezone)
    return TimestampParse(
        timezone=timezone,
        sample_interval=_normalize_sample_interval_value(sample_interval),
    )


def strptime(
    format: str,
    /,
    *,
    timezone: str | None = None,
    sample_interval: tuple[int, Literal["minute", "hour"]] | None = None,
) -> StrptimeParse:
    """Declare a string/integer strptime parse.

    Use as the ``parse=`` value on ``@ms.time_dimension(...)`` when the
    source column is a string or integer that must be parsed with a Python
    strptime format. The physical column type (string or integer) is inferred
    from the ibis expression at analysis time.

    Args:
        format: Canonical Python strptime format string (e.g. ``"%Y%m%d"``,
            ``"%Y-%m-%d %H:%M:%S"``). Must be ``%``-prefixed.
        timezone: Optional IANA timezone for time-bearing formats.
        sample_interval: Optional periodic sampling interval for sampled time
            dimensions, e.g. ``(5, "minute")`` or ``(1, "hour")``.

    Returns:
        A ``StrptimeParse`` value object.

    Raises:
        SemanticDecoratorError: ``format`` is not a valid strptime format, or
            ``timezone`` is not a valid IANA name.

    Example:
        >>> @ms.time_dimension(entity=orders, granularity="day",
        ...                    parse=ms.strptime("%Y%m%d"))
        ... def dt(orders):
        ...     return orders.dt
    """
    normalized = normalize_strptime(format)
    if timezone is not None:
        _validate_timezone(timezone)
        if not is_time_bearing_format(normalized):
            _raise(
                ErrorKind.INVALID_REF,
                "timezone is only supported for time-bearing strptime formats, not date-only formats.",
                cls=SemanticDecoratorError,
                details={"field": "timezone", "format": normalized},
            )
    return StrptimeParse(
        format=normalized,
        timezone=timezone,
        sample_interval=_normalize_sample_interval_value(
            sample_interval,
        ),
    )


def hour_prefix(
    prefix: TimeDimensionRef,
    /,
    *,
    sample_interval: tuple[int, Literal["minute", "hour"]] | None = None,
) -> HourPrefixParse:
    """Declare an hour-only partition parse using a day prefix column.

    Use as the ``parse=`` value on ``@ms.time_dimension(...)`` when the
    source column encodes only the hour component (e.g. ``"01"``, ``"23"``)
    and must be combined with a day-level time dimension prefix. The physical
    column type (string or integer) is inferred from the ibis expression at
    analysis time.

    Args:
        prefix: The ``TimeDimensionRef`` of a day-level time dimension that
            supplies the date context for this hour column.
        sample_interval: Optional ``(count, unit)`` declaring the periodic
            sampling cadence (e.g. ``(1, "hour")`` for hourly samples).
            When set, the time dimension can serve as a sampled-fold axis.

    Returns:
        An ``HourPrefixParse`` value object.

    Example:
        >>> @ms.time_dimension(entity=logs, granularity="day")
        ... def dt(logs):
        ...     return logs.dt
        >>> @ms.time_dimension(entity=logs, granularity="hour",
        ...                    parse=ms.hour_prefix(dt))
        ... def hh(logs):
        ...     return logs.hh
        >>> @ms.time_dimension(entity=logs, granularity="hour",
        ...                    parse=ms.hour_prefix(dt,
        ...                                        sample_interval=(1, "hour")))
        ... def hh(logs):
        ...     return logs.hh
    """
    if not isinstance(prefix, TimeDimensionRef):
        received = getattr(prefix, "id", prefix)
        _raise(
            ErrorKind.INVALID_REF,
            "ms.hour_prefix(...) prefix must be a TimeDimensionRef returned by "
            f"@ms.time_dimension(...); got {type(prefix).__name__}: {received!r}.",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    return HourPrefixParse(
        prefix=prefix.id,
        sample_interval=_normalize_sample_interval_value(
            sample_interval,
        ),
    )


_register_authoring_file(__file__)
