"""Derived-metric compositions and cumulative anchors for semantic authoring.

Internal module: public symbols are re-exported from
``marivo.semantic.authoring``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from marivo.refs import (
    DomainKind,
    MetricKind,
    Ref,
    SemanticKind,
    TimeDimensionKind,
)
from marivo.refs import (
    ref as ref_factory,
)
from marivo.semantic._authoring_context import (
    _caller_location,
    _check_duplicate,
    _push_ir,
    _register_authoring_file,
    _require_ctx,
    _require_ref_id,
    _resolve_domain,
)
from marivo.semantic._authoring_validation import _validate_unit
from marivo.semantic._authoring_values import _build_ai_context
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import ErrorKind, SemanticDecoratorError, _raise
from marivo.semantic.ir import (
    _GRAIN_TO_DATE_RESETS,
    _TRAILING_FIXED_UNITS,
    Composition,
    CumulativeAnchor,
    LinearComposition,
    LinearTerm,
    MetricIR,
    RatioComposition,
)
from marivo.semantic.ir import (
    CumulativeComposition as CumulativeComposition,
)
from marivo.semantic.typing import AiContextValue


@dataclass(frozen=True)
class GrainToDate:
    """Value object selecting a grain-to-date cumulative anchor (MTD/QTD/YTD)."""

    grain: str
    kind: Literal["grain_to_date"] = "grain_to_date"

    def __post_init__(self) -> None:
        if self.grain not in _GRAIN_TO_DATE_RESETS:
            _raise(
                ErrorKind.INVALID_REF,
                f"ms.grain_to_date(grain={self.grain!r}) is not a reset grain; "
                "expected one of: week, month, quarter, year. Use a plain "
                "ms.cumulative(...) for all-history, or ms.trailing(count=..., unit='day') "
                "for a fixed-size rolling window.",
                cls=SemanticDecoratorError,
                constraint_id=ConstraintId.CUMULATIVE_ANCHOR,
            )


@dataclass(frozen=True)
class Trailing:
    """Value object selecting a fixed-size trailing cumulative anchor (rolling N)."""

    count: int
    unit: str
    kind: Literal["trailing"] = "trailing"

    def __post_init__(self) -> None:
        if not isinstance(self.count, int) or isinstance(self.count, bool) or self.count < 1:
            _raise(
                ErrorKind.INVALID_REF,
                f"ms.trailing(count={self.count!r}) requires a positive integer count.",
                cls=SemanticDecoratorError,
                constraint_id=ConstraintId.CUMULATIVE_ANCHOR,
            )
        if self.unit not in _TRAILING_FIXED_UNITS:
            _raise(
                ErrorKind.INVALID_REF,
                f"ms.trailing(unit={self.unit!r}) is a calendar-variable unit; trailing "
                "windows accept fixed-size units only (second, minute, hour, day, week). "
                "For a sliding-months reset use ms.grain_to_date(grain='month'); for a "
                "fixed-length month window use ms.trailing(count=..., unit='day').",
                cls=SemanticDecoratorError,
                constraint_id=ConstraintId.CUMULATIVE_ANCHOR,
            )


def grain_to_date(*, grain: str) -> GrainToDate:
    """Select a grain-to-date cumulative anchor (MTD / QTD / YTD resets).

    The running total resets at each reset-grain boundary (start of the
    month/quarter/year/week). Within a reset period the value accumulates;
    at the boundary it drops to the period's first-bucket flow.

    Args:
        grain: Reset grain, one of ``week``, ``month``, ``quarter``, ``year``.

    Returns:
        A frozen ``GrainToDate`` value object to pass as ``anchor=`` on
        ``ms.cumulative(...)``.

    Example:
        >>> mtd_revenue = ms.cumulative(
        ...     name="mtd_revenue", base=revenue, over=event_time,
        ...     anchor=ms.grain_to_date(grain="month"),
        ... )

    Constraints:
        The query grain must satisfy the grain-compatibility rule: every
        display bucket must lie within one reset period (week grain under a
        month/quarter/year reset is illegal). ``day`` and ``hour`` are legal.
    """
    return GrainToDate(grain=grain)


def trailing(*, count: int, unit: str) -> Trailing:
    """Select a fixed-size trailing cumulative anchor (rolling N).

    The value at each bucket is the base aggregation over the span ending at
    that bucket's end boundary. Empty windows are true zero, not carried
    forward. Partial windows (the span reaches before the data start) show
    the actual partial accumulation and are marked ``partial`` in coverage.

    Args:
        count: Positive integer window length.
        unit: Fixed-size unit, one of ``second``, ``minute``, ``hour``,
            ``day``, ``week``. Calendar-variable units (``month``,
            ``quarter``, ``year``) are rejected.

    Returns:
        A frozen ``Trailing`` value object to pass as ``anchor=`` on
        ``ms.cumulative(...)``.

    Example:
        >>> rolling7_active = ms.cumulative(
        ...     name="rolling7_active", base=active_users, over=event_time,
        ...     anchor=ms.trailing(count=7, unit="day"),
        ... )

    Constraints:
        The window span must be an integer multiple of the query grain
        (``W_buckets = span / grain``). Trailing requires a time grain; for a
        windowed scalar use a plain ``session.observe(...)`` window instead.
    """
    return Trailing(count=count, unit=unit)


def _compute_composition_hash(composition: Composition) -> str:
    if isinstance(composition, RatioComposition):
        text = repr(("ratio", composition.numerator, composition.denominator))
    elif isinstance(composition, CumulativeComposition):
        text = repr(("cumulative", composition.base, composition.over, composition.anchor))
    else:  # LinearComposition
        text = repr(("linear", tuple((t.sign, t.metric) for t in composition.terms)))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _derived(
    *,
    name: str,
    composition: Composition,
    unit: str | None,
    domain: Ref[DomainKind] | None,
    ai_context: AiContextValue | None,
) -> Ref[MetricKind]:
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)
    semantic_id = f"{resolved_domain}.{name}"
    ref = ref_factory.metric(semantic_id)
    _check_duplicate(ctx, semantic_id, MetricIR)
    _validate_unit(unit, semantic_id)
    ai_ctx = _build_ai_context(ai_context)
    location = _caller_location()
    metric_ir = MetricIR(
        semantic_id=semantic_id,
        domain=resolved_domain,
        name=name,
        metric_type="derived",
        entities=(),
        aggregation=None,
        measure=None,
        composition=composition,
        additivity=None,
        provenance=None,
        ai_context=ai_ctx,
        body_ast_hash=_compute_composition_hash(composition),
        python_symbol=name,
        location=location,
        unit=unit,
        unit_override=unit,
    )
    _push_ir(ctx, ref, metric_ir, None)
    return ref


def ratio(
    *,
    name: str,
    numerator: Ref[MetricKind],
    denominator: Ref[MetricKind],
    unit: str | None = None,
    domain: Ref[DomainKind] | None = None,
    ai_context: AiContextValue | None = None,
) -> Ref[MetricKind]:
    """Declare a derived ratio metric (no body). Override the unit derived from the components at load.

    Components may themselves be derived metrics. Each nested ratio must satisfy
    its own unit, source, scope, and bounded-graph contract before analysis.

    Example::

        loss_rate = ms.ratio(name="loss_rate", numerator=lost, denominator=total, unit="1")
    """
    return _derived(
        name=name,
        composition=RatioComposition(
            numerator=_require_ref_id(
                numerator,
                parameter="numerator",
                expected=(SemanticKind.METRIC,),
            ),
            denominator=_require_ref_id(
                denominator,
                parameter="denominator",
                expected=(SemanticKind.METRIC,),
            ),
        ),
        unit=unit,
        domain=domain,
        ai_context=ai_context,
    )


def linear(
    *,
    name: str,
    add: list[Ref[MetricKind]],
    subtract: list[Ref[MetricKind]] | tuple[Ref[MetricKind], ...] = (),
    unit: str | None = None,
    domain: Ref[DomainKind] | None = None,
    ai_context: AiContextValue | None = None,
) -> Ref[MetricKind]:
    """Declare a derived linear metric (no body): sum of ``add`` minus ``subtract``. Override the unit derived from the components at load.

    Terms may be derived metrics. Every recursively lowered term must be
    commensurable and satisfy the shared source, scope, and graph budgets.

    Example::

        net_revenue = ms.linear(name="net_revenue", add=[gross], subtract=[refunds])
    """
    terms = tuple(
        LinearTerm(
            "+",
            _require_ref_id(
                m,
                parameter=f"add[{idx}]",
                expected=(SemanticKind.METRIC,),
            ),
        )
        for idx, m in enumerate(add)
    ) + tuple(
        LinearTerm(
            "-",
            _require_ref_id(
                m,
                parameter=f"subtract[{idx}]",
                expected=(SemanticKind.METRIC,),
            ),
        )
        for idx, m in enumerate(subtract)
    )
    if len(terms) < 2:
        _raise(
            ErrorKind.INVALID_REF,
            f"ms.linear(name={name!r}) requires at least two metric terms.",
            refs=(name,),
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    return _derived(
        name=name,
        composition=LinearComposition(terms=terms),
        unit=unit,
        domain=domain,
        ai_context=ai_context,
    )


def cumulative(
    *,
    name: str,
    base: Ref[MetricKind],
    over: Ref[TimeDimensionKind] | None = None,
    anchor: GrainToDate | Trailing | None = None,
    unit: str | None = None,
    domain: Ref[DomainKind] | None = None,
    ai_context: AiContextValue | None = None,
) -> Ref[MetricKind]:
    """Declare a cumulative metric over a tier-1 base metric.

    The ``anchor`` selects the accumulation shape. ``None`` (default) is the
    v1 all-history running total: the observe window clips displayed rows but
    does not reset the value. ``ms.grain_to_date(grain=...)`` resets at each
    reset-grain boundary (MTD/QTD/YTD). ``ms.trailing(count=..., unit=...)``
    is a fixed-size rolling window where empty windows are true zero.

    Args:
        name: Metric name.
        base: Tier-1 simple aggregate metric ref to accumulate.
        over: Time dimension ref defining the accumulation axis. Prefer
            passing this explicitly. When omitted, load succeeds only if the
            base metric root entity has exactly one time dimension.
        anchor: Accumulation anchor. ``None`` for all history (default),
            ``ms.grain_to_date(...)`` for period resets, or
            ``ms.trailing(...)`` for a rolling window.
        unit: Optional output unit override. Defaults to the base metric unit at load.
        domain: Override the active domain namespace.
        ai_context: Optional agent-facing context.

    Returns:
        A ``Ref[metric]`` for the derived cumulative metric.

    Example:
        >>> # MTD revenue
        >>> mtd_revenue = ms.cumulative(
        ...     name="mtd_revenue", base=revenue, over=event_time,
        ...     anchor=ms.grain_to_date(grain="month"),
        ... )
        >>> # Rolling-7d active users
        >>> rolling7_active = ms.cumulative(
        ...     name="rolling7_active", base=active_users, over=event_time,
        ...     anchor=ms.trailing(count=7, unit="day"),
        ... )

    Constraints:
        The base aggregation must be ``sum``, ``count``, or
        ``count_distinct``. ``grain_to_date`` requires a grain-compatible
        query grain; ``trailing`` requires a fixed-size span that is an
        integer multiple of the query grain and a time grain.
    """
    if type(base) is not Ref or base.kind is not SemanticKind.METRIC:
        _raise(
            ErrorKind.INVALID_REF,
            "base= accepts Ref[metric] returned by semantic authoring or a catalog entry's .ref.",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    if over is not None and (type(over) is not Ref or over.kind is not SemanticKind.TIME_DIMENSION):
        _raise(
            ErrorKind.INVALID_REF,
            "over= accepts Ref[time_dimension] returned by semantic authoring.",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
    if anchor is not None and not isinstance(anchor, (GrainToDate, Trailing)):
        _raise(
            ErrorKind.INVALID_REF,
            "anchor= accepts ms.grain_to_date(...) or ms.trailing(...), or None for "
            "all-history accumulation.",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.CUMULATIVE_ANCHOR,
        )
    if isinstance(anchor, GrainToDate):
        ir_anchor: CumulativeAnchor = ("grain_to_date", anchor.grain)
    elif isinstance(anchor, Trailing):
        ir_anchor = ("trailing", anchor.count, anchor.unit)
    else:
        ir_anchor = "all_history"
    return _derived(
        name=name,
        composition=CumulativeComposition(
            base=base.path,
            over=over.path if over is not None else None,
            anchor=ir_anchor,
        ),
        unit=unit,
        domain=domain,
        ai_context=ai_context,
    )


_register_authoring_file(__file__)
