"""Capture final reductions of an admitted internal sample domain."""

from __future__ import annotations

from typing import (
    Generic,
    Literal,
    TypeAlias,
    TypeVar,
)

from ibis.common.deferred import Resolver

from marivo._definition.types import (
    InputContext,
    NullPolicy,
)
from marivo.semantic._expression_carrier import (
    _ExpressionCarrier,
    _merge,
    _RowCarrier,
)
from marivo.telemetry import traced

_SampleMetricT = TypeVar("_SampleMetricT")
TimeExpression: TypeAlias = _RowCarrier[Literal["time"]]


class _SampleReductions(_ExpressionCarrier, Generic[_SampleMetricT]):
    """The existing intermediate sample role, requiring a final reduction."""

    _carrier_stage = "sample"

    def _numeric_metric(
        self, recipe: Resolver, context: InputContext, role: Literal["numeric"]
    ) -> _SampleMetricT:
        raise NotImplementedError

    @traced(surface="semantic", operation="metric_samples.sum", kind="method")
    def sum(self, *, nulls: NullPolicy = "ignore") -> _SampleMetricT:
        """Capture the sum reduction using the existing metric rules.

        Args:
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``revenue.sum()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return self._numeric_metric(
            self._method("sum", kwargs={"nulls": nulls}), self._context, "numeric"
        )

    @traced(surface="semantic", operation="metric_samples.mean", kind="method")
    def mean(self, *, nulls: NullPolicy = "ignore") -> _SampleMetricT:
        """Capture the mean reduction using the existing metric rules.

        Args:
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``revenue.mean()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return self._numeric_metric(
            self._method("mean", kwargs={"nulls": nulls}), self._context, "numeric"
        )

    @traced(surface="semantic", operation="metric_samples.min", kind="method")
    def min(self, *, nulls: NullPolicy = "ignore") -> _SampleMetricT:
        """Capture the min reduction using the existing metric rules.

        Args:
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``revenue.min()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return self._numeric_metric(
            self._method("min", kwargs={"nulls": nulls}), self._context, "numeric"
        )

    @traced(surface="semantic", operation="metric_samples.max", kind="method")
    def max(self, *, nulls: NullPolicy = "ignore") -> _SampleMetricT:
        """Capture the max reduction using the existing metric rules.

        Args:
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``revenue.max()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return self._numeric_metric(
            self._method("max", kwargs={"nulls": nulls}), self._context, "numeric"
        )

    @traced(surface="semantic", operation="metric_samples.median", kind="method")
    def median(self, *, nulls: NullPolicy = "ignore") -> _SampleMetricT:
        """Capture the median reduction using the existing metric rules.

        Args:
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``revenue.median()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return self._numeric_metric(
            self._method("median", kwargs={"nulls": nulls}), self._context, "numeric"
        )

    @traced(surface="semantic", operation="metric_samples.count", kind="method")
    def count(self, *, nulls: NullPolicy = "ignore") -> _SampleMetricT:
        """Capture the count reduction using the existing metric rules.

        Args:
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``revenue.count()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return self._numeric_metric(
            self._method("count", kwargs={"nulls": nulls}), self._context, "numeric"
        )

    @traced(surface="semantic", operation="metric_samples.count_distinct", kind="method")
    def count_distinct(self, *, nulls: NullPolicy = "ignore") -> _SampleMetricT:
        """Capture the count_distinct reduction using the existing metric rules.

        Args:
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``revenue.count_distinct()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return self._numeric_metric(
            self._method("count_distinct", kwargs={"nulls": nulls}), self._context, "numeric"
        )

    @traced(surface="semantic", operation="metric_samples.approx_nunique", kind="method")
    def approx_nunique(self, *, nulls: NullPolicy = "ignore") -> _SampleMetricT:
        """Capture the approx_nunique reduction using the existing metric rules.

        Args:
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``revenue.approx_nunique()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return self._numeric_metric(
            self._method("approx_nunique", kwargs={"nulls": nulls}), self._context, "numeric"
        )

    @traced(surface="semantic", operation="metric_samples.approx_median", kind="method")
    def approx_median(self, *, nulls: NullPolicy = "ignore") -> _SampleMetricT:
        """Capture the approx_median reduction using the existing metric rules.

        Args:
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``revenue.approx_median()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return self._numeric_metric(
            self._method("approx_median", kwargs={"nulls": nulls}), self._context, "numeric"
        )

    @traced(surface="semantic", operation="metric_samples.first", kind="method")
    def first(
        self,
        *,
        order_by: TimeExpression | None = None,
        include_null: bool = False,
        nulls: NullPolicy = "ignore",
    ) -> _SampleMetricT:
        """Capture the first reduction using the existing metric rules.

        Args:
            order_by: The explicit order by captured for this operation.
            include_null: The explicit include null captured for this operation.
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``revenue.first()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        kwargs: dict[str, object] = {"include_null": include_null, "nulls": nulls}
        if order_by is not None:
            kwargs["order_by"] = order_by
        return self._numeric_metric(
            self._method("first", kwargs=kwargs), _merge(self, order_by), "numeric"
        )

    @traced(surface="semantic", operation="metric_samples.last", kind="method")
    def last(
        self,
        *,
        order_by: TimeExpression | None = None,
        include_null: bool = False,
        nulls: NullPolicy = "ignore",
    ) -> _SampleMetricT:
        """Capture the last reduction using the existing metric rules.

        Args:
            order_by: The explicit order by captured for this operation.
            include_null: The explicit include null captured for this operation.
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``revenue.last()``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        kwargs: dict[str, object] = {"include_null": include_null, "nulls": nulls}
        if order_by is not None:
            kwargs["order_by"] = order_by
        return self._numeric_metric(
            self._method("last", kwargs=kwargs), _merge(self, order_by), "numeric"
        )

    @traced(surface="semantic", operation="metric_samples.weighted_mean", kind="method")
    def weighted_mean(
        self, *, weight: _SampleMetricT, nulls: NullPolicy = "ignore"
    ) -> _SampleMetricT:
        """Capture the weighted_mean reduction using the existing metric rules.

        Args:
            weight: The explicit weight captured for this operation.
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.weighted_mean(weight=quantity)``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return self._numeric_metric(
            self._method("weighted_mean", kwargs={"weight": weight, "nulls": nulls}),
            _merge(self, weight),
            "numeric",
        )

    @traced(surface="semantic", operation="metric_samples.quantile", kind="method")
    def quantile(self, q: float, *, nulls: NullPolicy = "ignore") -> _SampleMetricT:
        """Capture the quantile reduction using the existing metric rules.

        Args:
            q: The explicit q captured for this operation.
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.quantile(0.95)``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return self._numeric_metric(
            self._method("quantile", (q,), {"nulls": nulls}), self._context, "numeric"
        )

    @traced(surface="semantic", operation="metric_samples.approx_quantile", kind="method")
    def approx_quantile(self, q: float, *, nulls: NullPolicy = "ignore") -> _SampleMetricT:
        """Capture the approx_quantile reduction using the existing metric rules.

        Args:
            q: The explicit q captured for this operation.
            nulls: The explicit nulls captured for this operation.

        Returns:
            A completed numeric Metric expression.

        Examples:
            ``amount.approx_quantile(0.95)``.

        Notes:
            Constraints: Captures immutable inputs without source access. The receiving
            scope validates roots, roles, units, time and operation-specific rules.
        """
        return self._numeric_metric(
            self._method("approx_quantile", (q,), {"nulls": nulls}), self._context, "numeric"
        )
