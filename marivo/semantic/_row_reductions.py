"""Capture source-clocked reductions over typed row values."""

from __future__ import annotations

from typing import (
    Generic,
    Literal,
    TypeVar,
)

from marivo._definition.types import (
    NullPolicy,
)
from marivo.semantic._expression_carrier import (
    _merge,
    _RowCarrier,
    _RowRoleT,
    _source_arguments,
)
from marivo.semantic._metric_expression import MetricExpression, NumericMetric
from marivo.semantic.expressions.inputs import (
    _OMITTED_TIME,
    _OmittedTime,
)
from marivo.telemetry import traced

_TimeT = TypeVar("_TimeT")
_PredicateT = TypeVar("_PredicateT")
_NumericT = TypeVar("_NumericT")


class _RowReductions(_RowCarrier[_RowRoleT], Generic[_RowRoleT, _TimeT, _PredicateT, _NumericT]):
    """Capture a typed row contribution as a Metric reduction."""

    @traced(surface="semantic", operation="row.sum", kind="method")
    def sum(
        self: _RowReductions[Literal["numeric"], _TimeT, _PredicateT, _NumericT],
        *,
        time: _TimeT | _OmittedTime | None = _OMITTED_TIME,
        nulls: NullPolicy = "ignore",
        where: _PredicateT | None = None,
    ) -> NumericMetric:
        """Capture the sum reduction using the existing row rules.

        Args:
            time: Contribution TimeDimension. Omit to inherit the Entity default; None or no default makes the branch report independent.
            nulls: The explicit nulls captured for this operation.
            where: Optional same-root source-row condition captured before reduction.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.sum()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method(
                "sum", kwargs=_source_arguments(self._recipe, time, nulls=nulls, where=where)
            ),
            _merge(self, time, where),
            "numeric",
        )

    @traced(surface="semantic", operation="row.mean", kind="method")
    def mean(
        self: _RowReductions[Literal["numeric"], _TimeT, _PredicateT, _NumericT],
        *,
        time: _TimeT | _OmittedTime | None = _OMITTED_TIME,
        nulls: NullPolicy = "ignore",
        where: _PredicateT | None = None,
    ) -> NumericMetric:
        """Capture the mean reduction using the existing row rules.

        Args:
            time: Contribution TimeDimension. Omit to inherit the Entity default; None or no default makes the branch report independent.
            nulls: The explicit nulls captured for this operation.
            where: Optional same-root source-row condition captured before reduction.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.mean()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method(
                "mean", kwargs=_source_arguments(self._recipe, time, nulls=nulls, where=where)
            ),
            _merge(self, time, where),
            "numeric",
        )

    @traced(surface="semantic", operation="row.min", kind="method")
    def min(
        self: _RowReductions[Literal["numeric"], _TimeT, _PredicateT, _NumericT],
        *,
        time: _TimeT | _OmittedTime | None = _OMITTED_TIME,
        nulls: NullPolicy = "ignore",
        where: _PredicateT | None = None,
    ) -> NumericMetric:
        """Capture the min reduction using the existing row rules.

        Args:
            time: Contribution TimeDimension. Omit to inherit the Entity default; None or no default makes the branch report independent.
            nulls: The explicit nulls captured for this operation.
            where: Optional same-root source-row condition captured before reduction.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.min()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method(
                "min", kwargs=_source_arguments(self._recipe, time, nulls=nulls, where=where)
            ),
            _merge(self, time, where),
            "numeric",
        )

    @traced(surface="semantic", operation="row.max", kind="method")
    def max(
        self: _RowReductions[Literal["numeric"], _TimeT, _PredicateT, _NumericT],
        *,
        time: _TimeT | _OmittedTime | None = _OMITTED_TIME,
        nulls: NullPolicy = "ignore",
        where: _PredicateT | None = None,
    ) -> NumericMetric:
        """Capture the max reduction using the existing row rules.

        Args:
            time: Contribution TimeDimension. Omit to inherit the Entity default; None or no default makes the branch report independent.
            nulls: The explicit nulls captured for this operation.
            where: Optional same-root source-row condition captured before reduction.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.max()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method(
                "max", kwargs=_source_arguments(self._recipe, time, nulls=nulls, where=where)
            ),
            _merge(self, time, where),
            "numeric",
        )

    @traced(surface="semantic", operation="row.median", kind="method")
    def median(
        self: _RowReductions[Literal["numeric"], _TimeT, _PredicateT, _NumericT],
        *,
        time: _TimeT | _OmittedTime | None = _OMITTED_TIME,
        nulls: NullPolicy = "ignore",
        where: _PredicateT | None = None,
    ) -> NumericMetric:
        """Capture the median reduction using the existing row rules.

        Args:
            time: Contribution TimeDimension. Omit to inherit the Entity default; None or no default makes the branch report independent.
            nulls: The explicit nulls captured for this operation.
            where: Optional same-root source-row condition captured before reduction.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.median()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method(
                "median", kwargs=_source_arguments(self._recipe, time, nulls=nulls, where=where)
            ),
            _merge(self, time, where),
            "numeric",
        )

    @traced(surface="semantic", operation="row.count", kind="method")
    def count(
        self,
        *,
        time: _TimeT | _OmittedTime | None = _OMITTED_TIME,
        nulls: NullPolicy = "ignore",
        where: _PredicateT | None = None,
    ) -> NumericMetric:
        """Capture the count reduction using the existing row rules.

        Args:
            time: Contribution TimeDimension. Omit to inherit the Entity default; None or no default makes the branch report independent.
            nulls: The explicit nulls captured for this operation.
            where: Optional same-root source-row condition captured before reduction.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.count()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method(
                "count", kwargs=_source_arguments(self._recipe, time, nulls=nulls, where=where)
            ),
            _merge(self, time, where),
            "numeric",
        )

    @traced(surface="semantic", operation="row.count_distinct", kind="method")
    def count_distinct(
        self,
        *,
        time: _TimeT | _OmittedTime | None = _OMITTED_TIME,
        nulls: NullPolicy = "ignore",
        where: _PredicateT | None = None,
    ) -> NumericMetric:
        """Capture the count_distinct reduction using the existing row rules.

        Args:
            time: Contribution TimeDimension. Omit to inherit the Entity default; None or no default makes the branch report independent.
            nulls: The explicit nulls captured for this operation.
            where: Optional same-root source-row condition captured before reduction.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.count_distinct()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method(
                "count_distinct",
                kwargs=_source_arguments(self._recipe, time, nulls=nulls, where=where),
            ),
            _merge(self, time, where),
            "numeric",
        )

    @traced(surface="semantic", operation="row.approx_nunique", kind="method")
    def approx_nunique(
        self,
        *,
        time: _TimeT | _OmittedTime | None = _OMITTED_TIME,
        nulls: NullPolicy = "ignore",
        where: _PredicateT | None = None,
    ) -> NumericMetric:
        """Capture the approx_nunique reduction using the existing row rules.

        Args:
            time: Contribution TimeDimension. Omit to inherit the Entity default; None or no default makes the branch report independent.
            nulls: The explicit nulls captured for this operation.
            where: Optional same-root source-row condition captured before reduction.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.approx_nunique()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method(
                "approx_nunique",
                kwargs=_source_arguments(self._recipe, time, nulls=nulls, where=where),
            ),
            _merge(self, time, where),
            "numeric",
        )

    @traced(surface="semantic", operation="row.approx_median", kind="method")
    def approx_median(
        self: _RowReductions[Literal["numeric"], _TimeT, _PredicateT, _NumericT],
        *,
        time: _TimeT | _OmittedTime | None = _OMITTED_TIME,
        nulls: NullPolicy = "ignore",
        where: _PredicateT | None = None,
    ) -> NumericMetric:
        """Capture the approx_median reduction using the existing row rules.

        Args:
            time: Contribution TimeDimension. Omit to inherit the Entity default; None or no default makes the branch report independent.
            nulls: The explicit nulls captured for this operation.
            where: Optional same-root source-row condition captured before reduction.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.approx_median()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method(
                "approx_median",
                kwargs=_source_arguments(self._recipe, time, nulls=nulls, where=where),
            ),
            _merge(self, time, where),
            "numeric",
        )

    @traced(surface="semantic", operation="row.quantile", kind="method")
    def quantile(
        self: _RowReductions[Literal["numeric"], _TimeT, _PredicateT, _NumericT],
        q: float,
        *,
        time: _TimeT | _OmittedTime | None = _OMITTED_TIME,
        nulls: NullPolicy = "ignore",
        where: _PredicateT | None = None,
    ) -> NumericMetric:
        """Capture the quantile reduction using the existing row rules.

        Args:
            q: The explicit q captured for this operation.
            time: Contribution TimeDimension. Omit to inherit the Entity default; None or no default makes the branch report independent.
            nulls: The explicit nulls captured for this operation.
            where: Optional same-root source-row condition captured before reduction.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.quantile(0.95)``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method(
                "quantile", (q,), _source_arguments(self._recipe, time, nulls=nulls, where=where)
            ),
            _merge(self, time, where),
            "numeric",
        )

    @traced(surface="semantic", operation="row.approx_quantile", kind="method")
    def approx_quantile(
        self: _RowReductions[Literal["numeric"], _TimeT, _PredicateT, _NumericT],
        q: float,
        *,
        time: _TimeT | _OmittedTime | None = _OMITTED_TIME,
        nulls: NullPolicy = "ignore",
        where: _PredicateT | None = None,
    ) -> NumericMetric:
        """Capture the approx_quantile reduction using the existing row rules.

        Args:
            q: The explicit q captured for this operation.
            time: Contribution TimeDimension. Omit to inherit the Entity default; None or no default makes the branch report independent.
            nulls: The explicit nulls captured for this operation.
            where: Optional same-root source-row condition captured before reduction.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.approx_quantile(0.95)``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method(
                "approx_quantile",
                (q,),
                _source_arguments(self._recipe, time, nulls=nulls, where=where),
            ),
            _merge(self, time, where),
            "numeric",
        )

    @traced(surface="semantic", operation="row.weighted_mean", kind="method")
    def weighted_mean(
        self: _RowReductions[Literal["numeric"], _TimeT, _PredicateT, _NumericT],
        weight: _NumericT,
        *,
        time: _TimeT | _OmittedTime | None = _OMITTED_TIME,
        nulls: NullPolicy = "ignore",
        where: _PredicateT | None = None,
    ) -> NumericMetric:
        """Capture the weighted_mean reduction using the existing row rules.

        Args:
            weight: The explicit weight captured for this operation.
            time: Contribution TimeDimension. Omit to inherit the Entity default; None or no default makes the branch report independent.
            nulls: The explicit nulls captured for this operation.
            where: Optional same-root source-row condition captured before reduction.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.weighted_mean(weight=quantity)``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return MetricExpression(
            self._method(
                "weighted_mean",
                kwargs=_source_arguments(
                    self._recipe, time, weight=weight, nulls=nulls, where=where
                ),
            ),
            _merge(self, weight, time, where),
            "numeric",
        )
