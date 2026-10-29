"""Value-object constructors for semantic authoring.

Internal module: public symbols are re-exported from
``marivo.semantic.authoring``.
"""

from __future__ import annotations

from collections.abc import Sequence as _Sequence
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import ibis.expr.types as ibis_ir
from ibis.common.deferred import Deferred

from marivo.refs import SemanticKind
from marivo.semantic._authoring_context import (
    _caller_location,
    _register_authoring_file,
    _require_ref_id,
)
from marivo.semantic._authoring_validation import (
    _validate_timezone,
)
from marivo.semantic._dsl_authoring import AdditiveAllV1, AdditiveOverV1, CoordinateInput
from marivo.semantic.errors import ErrorKind, SemanticDecoratorError, _raise
from marivo.semantic.ir import (
    AiContextIR,
    HourPrefixParse,
    JoinKey,
    SnapshotVersioningIR,
    StrptimeParse,
    TimestampParse,
    ValidityVersioningIR,
    is_time_bearing_format,
)
from marivo.semantic.time_format import normalize_strptime
from marivo.semantic.typing import AiContextValue


def additive_over(*axes: CoordinateInput) -> AdditiveOverV1:
    """Declare the existing field coordinates on which a Measure may be summed.

    Args:
        axes: One or more constructed Dimension or TimeDimension fields or lookup refs,
            passed as positional arguments. Unpack an existing collection with ``*axes``.

    Returns:
        A closed additive-over policy.

    Example:
        ``ms.additive_over(region, product)``.

    Constraints: Every axis must exist on the declared contribution root. Axes resolve
    by kind/path in the complete physical closure or frozen row scope; original
    definition versions are not required.
    """
    from marivo.semantic._dsl_authoring import additive_over as build

    return build(*axes)


def additive_all() -> AdditiveAllV1:
    """Grant summing across all native coordinates.

    Returns:
        A closed additive-all policy.

    Example:
        ``ms.additive_all()``.

    Constraints: Accepts no arguments and does not prove coverage. Use
    ``ms.additive_except(...)`` for fixed axes, including source status time.
    """
    from marivo.semantic._dsl_authoring import additive_all as build

    return build()


def additive_except(*axes: CoordinateInput) -> AdditiveAllV1:
    """Grant summing across native coordinates except explicitly fixed axes.

    Args:
        axes: One or more constructed Dimension or TimeDimension fields or lookup refs
            that must remain fixed, passed positionally. Unpack a collection with ``*axes``.

    Returns:
        A closed additive-all policy with fixed axes.

    Example:
        ``ms.additive_except(sample_at)``.

    Constraints: Retain source status-time constraints; this does not prove coverage.
    Exceptions select the actual consumer coordinates, without original-version matching.
    """
    from marivo.semantic._dsl_authoring import additive_except as build

    return build(*axes)


def bind(field: Deferred, entity_alias: ibis_ir.Table) -> ibis_ir.Value:
    """Apply a physical semantic field in a restricted Ibis field body.

    Args:
        field: Constructed physical field or native lookup symbol, supplied as a bare name.
        entity_alias: Direct Entity table parameter of the Dimension/TimeDimension body.
    Returns: The owning field's Ibis value on that Entity alias.
    Example: ``return ms.bind(region, rows).upper()`` inside @ms.dimension(entity=orders).
    Constraints: Loader binding checks field kind and ownership in the completed consumer scope; lookup symbols allow forward dependencies. Pure Measure/Metric bodies use constructed fields directly.
    """
    from marivo.semantic._expression_binding import bind as apply

    return apply(field, entity_alias)


def ai_context(
    *,
    business_definition: str | None = None,
    guardrails: _Sequence[str] | None = None,
    aliases: _Sequence[str] | None = None,
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
        aliases: Non-empty search terms. Unicode and whitespace are normalized;
            case-insensitive duplicates retain their first spelling and order.
            An empty sequence declares no aliases. Aliases never create object identities.

    Returns:
        A validated ``AiContextValue`` for use with ``ai_context=`` parameters.

    Example:
        >>> ctx = ms.ai_context(
        ...     business_definition="Total revenue from all orders",
        ...     guardrails=["Do not use for margin calculations"],
        ... )
        >>> @ms.metric(ai_context=ctx)
        ... def revenue():
        ...     return amount.sum()

    Raises:
        SemanticDecoratorError: If any value has the wrong type.
    """
    location = _caller_location()

    if aliases is not None and (
        not isinstance(aliases, _Sequence)
        or isinstance(aliases, str | bytes | bytearray)
        or not all(isinstance(item, str) and item.strip() for item in aliases)
    ):
        _raise(
            ErrorKind.INVALID_AI_CONTEXT,
            "ms.ai_context(aliases=...) requires a sequence of non-empty strings, "
            f"got {aliases!r}. Use aliases=['Revenue', '销售额'] or omit aliases.",
            cls=SemanticDecoratorError,
            location=location,
        )

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
        aliases=tuple(aliases) if aliases is not None else (),
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
        aliases=ai_context.aliases,
    )


def snapshot(
    *,
    partition_field: Deferred,
    timezone: str,
) -> SnapshotVersioningIR:
    """Declare daily snapshot partition versioning for an entity.

    Args:
        partition_field: Native TimeField or explicit TimeDimension lookup symbol.
        timezone: Required IANA zone defining the business snapshot day.
    Returns: A frozen SnapshotVersioningIR with the partition lookup key.
    Example: ``ms.snapshot(partition_field=day_ref, timezone="UTC")``.
    Constraints: The completed Entity scope resolves the partition field; lookup symbols may name forward dependencies.
    """
    partition_ref = _require_ref_id(
        partition_field,
        parameter="partition_field",
        expected=(SemanticKind.TIME_DIMENSION,),
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
        timezone=timezone,
    )


def validity(
    *,
    valid_from: Deferred,
    valid_to: Deferred,
    interval: Literal["closed_open", "closed_closed"],
    open_end: tuple[str | None, ...],
    timezone: str | None = None,
) -> ValidityVersioningIR:
    """Declare SCD2 validity interval versioning for an entity.

    Args:
        valid_from: Native TimeField or lookup symbol for the interval start.
        valid_to: Native TimeField or lookup symbol for the interval end.
        interval: ``"closed_open"`` (``[valid_from, valid_to)``) or
            ``"closed_closed"`` (``[valid_from, valid_to]``).
        open_end: Non-empty tuple of sentinel values that mean "still current"
            in the ``valid_to`` column. Use ``None`` for SQL NULL, or a string
            sentinel such as ``"9999-12-31"``.
        timezone: Business timezone required for DATE bounds; forbidden for timestamp bounds.

    Returns:
        A frozen ``ValidityVersioningIR`` with interval field lookup keys.
        Its Entity consumer resolves these fields during complete-scope loader binding.

    Example:
        ``ms.validity(valid_from=start_ref, valid_to=end_ref,
        interval="closed_open", open_end=(None,))``.

    Constraints:
        Use native fields. The completed Entity closure resolves the interval fields;
        only native lookup symbols can name forward dependencies.

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
        expected=(SemanticKind.TIME_DIMENSION,),
    )
    valid_to_ref = _require_ref_id(
        valid_to,
        parameter="valid_to",
        expected=(SemanticKind.TIME_DIMENSION,),
    )
    return ValidityVersioningIR(
        kind="validity",
        valid_from=valid_from_ref,
        valid_to=valid_to_ref,
        interval=interval,
        open_end=open_end,
        timezone=timezone,
    )


def join_on(
    from_key: Deferred,
    to_key: Deferred,
    /,
) -> JoinKey:
    """Build one relationship key pair for ``ms.relationship(keys=[...])``.

    Each call creates one (from_key, to_key) pairing. Pass a list of
    ``ms.join_on(...)`` calls to ``keys=``.

    Args:
        from_key: Native Dimension/TimeField lookup symbol or a closed field on the source Entity.
        to_key: Native Dimension/TimeField lookup symbol or a closed field on the target Entity.
    Returns: A frozen JoinKey with the endpoint field lookup keys.
    Constraints: The completed Relationship scope checks field kind and endpoint ownership; Symbols retain forward-reference semantics.

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
            expected=(SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION),
        ),
        to_key=_require_ref_id(
            to_key,
            parameter="to_key",
            expected=(SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION),
        ),
    )


def timestamp(
    *,
    timezone: str | None = None,
) -> TimestampParse:
    """Declare an already-temporal timestamp column parse.

    Use as the ``parse=`` value on ``@ms.time_dimension(...)`` when the
    source column is a native timestamp type. Projection follows the backend's
    native interpretation. Constant-bound membership of a raw local clock uses
    wall order: repeated clocks denote the later occurrence and skipped clocks
    denote the transition instant.
    This parser affects only the TimeDimension projection; Entity primary keys retain
    their output values, including naive timestamps without an implicit timezone.

    Args:
        timezone: Optional IANA source zone for naive timestamp columns. When
            omitted, naive values use frozen datasource reader authority. Aware
            values already identify instants and reject every parser timezone.


    Returns:
        A ``TimestampParse`` value object.

    Raises:
        SemanticDecoratorError: ``timezone`` is not a valid IANA name.

    Example:
        >>> @ms.time_dimension(entity=events,
        ...                    parse=ms.timestamp(timezone="UTC"))
        ... def ts(events):
        ...     return events.ts
    """
    if timezone is not None:
        _validate_timezone(timezone)
    return TimestampParse(
        timezone=timezone,
    )


def strptime(
    format: str,
    /,
    *,
    timezone: str | None = None,
) -> StrptimeParse:
    """Declare a string/integer strptime parse.

    Use as the ``parse=`` value on ``@ms.time_dimension(...)`` when the
    source column is a string or integer that must be parsed with a Python
    strptime format. The physical column type (string or integer) is inferred
    from the ibis expression at analysis time. Source values must use the
    format's canonical encoding: fixed directive widths, zero padding, exact
    separators and valid calendar values. For example, ``%Y%m%d`` requires
    ``20260701``, not ``2026071``. Integer encodings use the corresponding
    base-ten digits without losing leading zeros.

    Analysis can invert ordered calendar encodings into bare-column range
    predicates without additional queries or source validation. Unsupported
    inverses keep the parsed predicate. Ordered raw local-clock membership uses
    wall order; projected values retain native gap/fold interpretation.
    This guarantee assumes compliant source
    values; values excluded by the range are not inspected for contract violations.

    Args:
        format: Python strptime format and canonical source encoding contract
            (e.g. ``"%Y%m%d"``, ``"%Y-%m-%d %H:%M:%S"``). Must be ``%``-prefixed.
        timezone: Optional IANA timezone for time-bearing formats.


    Returns:
        A ``StrptimeParse`` value object.

    Raises:
        SemanticDecoratorError: ``format`` is not a valid strptime format, or
            ``timezone`` is not a valid IANA name.

    Example:
        >>> @ms.time_dimension(entity=orders,
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
    )


def hour_prefix(
    prefix: Deferred,
    /,
) -> HourPrefixParse:
    """Declare an hour-only partition parse using a day prefix column.

    Use as the ``parse=`` value on ``@ms.time_dimension(...)`` when the
    source column encodes an hour in 0..23 (canonical two-digit strings such as
    ``"01"`` and ``"23"``, or integers)
    and must be combined with a civil-date time dimension prefix. The combined
    clock inherits the prefix source context, using its resolved reader timezone
    when the date has no intrinsic timezone. The physical
    column type (string or integer) is inferred from the ibis expression at
    analysis time.

    Args:
        prefix: A native TimeField or native TimeDimension symbol for a day-level field that
            supplies the date context for this hour column.


    Returns:
        A frozen ``HourPrefixParse`` with a native time-field lookup key.

    Example:
        >>> @ms.time_dimension(entity=logs)
        ... def dt(logs):
        ...     return logs.dt
        >>> @ms.time_dimension(entity=logs,
        ...                    parse=ms.hour_prefix(dt))
        ... def hh(logs):
        ...     return logs.hh
        >>> @ms.time_dimension(entity=logs,
        ...                    parse=ms.hour_prefix(dt), sample_interval=(1, "hour"))
        ... def hh(logs):
        ...     return logs.hh
    """
    prefix_id = _require_ref_id(prefix, parameter="prefix", expected=(SemanticKind.TIME_DIMENSION,))
    return HourPrefixParse(
        prefix=prefix_id,
    )


_register_authoring_file(__file__)
