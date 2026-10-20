"""Reauthoring errors for retired declarations; these paths never build definitions."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import NoReturn, ParamSpec, TypeVar

from marivo.semantic._authoring_context import _caller_location, _register_authoring_file
from marivo.semantic.errors import ErrorKind, SemanticExpressionError

P = ParamSpec("P")
R = TypeVar("R")

_REPAIRS = {
    "name": "Rename the zero-argument declaration function; names cannot be overridden.",
    "entity": "Remove entity= from the Measure decorator; its row expression determines the Entity.",
    "domain": "Remove domain= from the Measure decorator; its row expression determines the Entity namespace.",
    "entities": "Return receiver reductions and Metric arithmetic from a zero-argument @ms.metric() body.",
    "root_entity": "Keep each source reduction on its own Entity and combine the completed Metrics explicitly.",
    "additivity": "Declare business sum permission on source Measures; Metric declarations inherit their graph facts.",
    "unit": "Declare compatible units on source Measures; Metric declarations inherit units from their computation.",
    "time": "Set time= on each source receiver reduction in the expression.",
    "status_time_dimension": "Keep the status-time role on the physical Measure and express temporal reduction with per(time_field).reducer().",
    "status_time_fold": "Express the original spatial-before-temporal order explicitly with per(status_time_field).reducer(); keep the source status axis and sampling interval.",
    "fold": "Express the original spatial-before-temporal order explicitly with per(status_time_field).reducer().",
    "time_via": "Pass time=time_field.via((relationship, ...)) on the source reduction, preserving the original time field and ordered path.",
    "fanout_policy": (
        "Remove fanout_policy. For existence filtering, use Population.preimage(...) and "
        "mv.in_population(...); for overlapping labels, pass mv.membership(...) through "
        "Metric.per(samples, memberships=(rule,)). When association or allocation rows are contributions, model them "
        "as a separately keyed Entity and define the row measure and any allocation weight; "
        "preserve each source root."
    ),
    "empty": "Set empty='null' (default) or 'zero' on each actual source/internal sum, preserving the selected contributions and output domain.",
    "nulls": "Set nulls='ignore' or 'error' on the actual reducer in the expression.",
    "zero_denominator": "Set zero_denominator='null' or 'error' on the actual divide or weighted_mean operation.",
}


def reject_parameters(*names: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Preserve the supported signature while rejecting explicitly supplied old keywords."""

    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        @wraps(function)
        def checked(*args: P.args, **kwargs: P.kwargs) -> R:
            rejected = tuple(name for name in names if name in kwargs)
            if rejected:
                location = _caller_location()
                raise SemanticExpressionError(
                    kind=ErrorKind.INVALID_COMPONENT_BODY,
                    message=f"ms.{function.__name__} received retired declaration parameters.",
                    expected=f"the current ms.{function.__name__} signature and a stage-correct expression",
                    received=", ".join(f"{name}={kwargs[name]!r}" for name in rejected),
                    action=" ".join(_REPAIRS[name] for name in rejected),
                    location_label=f"{location.file}:{location.line}",
                )
            return function(*args, **kwargs)

        return checked

    return decorate


def retired_factory(name: str, arguments: dict[str, object]) -> NoReturn:
    """Reject an old factory without registering, translating, or running it."""
    location = _caller_location()
    actions = {
        "aggregate": "Use the constructed Measure's matching source reducer; retain its time and contribution predicate.",
        "count": "Use Entity.count() for complete rows or Field.count()/count_distinct() for field values; retain the original time and predicate.",
        "weighted_mean": "Use value.weighted_mean(weight=weight, ...) with a row weight on the same contribution root.",
        "where": "Build typed field comparisons and pass the predicate to the single source Metric.where(...).",
        "ratio": "Divide completed Metrics with / or divide(..., zero_denominator='error'); explicitly choose the new zero-denominator policy.",
        "linear": "Combine completed Metrics with + and - in a pure @ms.metric() declaration.",
        "cumulative": (
            "In a zero-argument @ms.metric() body, return base.window(rule) using a rule "
            "that preserves the original cumulative anchor and evaluation boundary; this "
            "retired declaration is not translated."
        ),
        "empty": _REPAIRS["empty"],
        "nulls": _REPAIRS["nulls"],
        "zero_denominator": _REPAIRS["zero_denominator"],
        "grain_to_date": (
            "Return base.window(mv.to_date(grain=...)) from a zero-argument @ms.metric() "
            "body, preserving the original reset grain."
        ),
        "trailing": (
            "Return base.window(mv.trailing_duration(duration=...)) from a zero-argument "
            "@ms.metric() body, preserving the original elapsed duration and evaluation boundary."
        ),
    }
    action = actions[name]
    for parameter in arguments:
        if parameter in _REPAIRS:
            action += " " + _REPAIRS[parameter]
    aggregation = arguments.get("agg")
    if "approx" in str(aggregation):
        action += (
            " Use the corresponding native approximate reducer: approx_count_distinct maps "
            "to approx_nunique(), approx_median to approx_median(), and approx_percentile "
            "to approx_quantile(q). Preserve the field and q; do not replace it with an exact reducer."
        )
    raise SemanticExpressionError(
        kind=ErrorKind.INVALID_COMPONENT_BODY,
        message=f"ms.{name} is a retired declaration factory.",
        expected="receiver expressions and zero-argument @ms.measure()/@ms.metric() declarations",
        received=f"ms.{name}({', '.join(f'{key}={value!r}' for key, value in arguments.items())})",
        action=action,
        location_label=f"{location.file}:{location.line}",
    )


_register_authoring_file(__file__)
