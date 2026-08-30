"""Typed frame-local transform namespaces."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Generic, TypeVar

import pandas as pd

from marivo._temporal import Grain as TemporalGrain
from marivo.analysis.frames.delta import DeltaFrame
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.intents.transform import NormalizeBaseline, NormalizeKind, RankMethod
from marivo.analysis.session._runtime import require_current_session
from marivo.analysis.session.core import _track_materializing_operation
from marivo.analysis.slice_types import SliceValue
from marivo.analysis.windows import TimeScope
from marivo.refs import DimensionKind, TimeDimensionKind
from marivo.semantic.catalog import _SemanticInput

TFrame = TypeVar("TFrame", MetricFrame, DeltaFrame)


@dataclass(frozen=True)
class _FrameTransforms(Generic[TFrame]):
    _frame: TFrame

    def filter(
        self,
        *,
        predicate: Callable[[pd.DataFrame], pd.Series],
        analysis_purpose: str | None = None,
    ) -> TFrame:
        """Filter rows using a boolean pandas predicate.

        Args:
            predicate: Callable receiving the frame DataFrame and returning a
                boolean Series aligned to the input index.
            analysis_purpose: Optional durable label explaining why this
                transform exists.

        Returns:
            A transformed frame of the same family as the receiver.

        Example:
            >>> value_column = frame.value_columns[0] if isinstance(frame, mv.MetricFrame) else "delta"
            >>> focused = frame.transform.filter(predicate=lambda df: df[value_column] > 0)

        Constraints:
            The predicate receives the same public columns exposed by
            ``frame.columns`` and ``frame.to_pandas()``. Requires the frame's
            owning session to be current and writable.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.transform import transform_filter

        validate_capability_inputs("transform.filter", receiver=self._frame)
        session = require_current_session()
        with _track_materializing_operation(
            session,
            "marivo.analysis.frame.transform.filter",
            capability_id="transform.filter",
            family="transform",
            intent="filter",
            arguments={"receiver": self._frame, "predicate": predicate},
            analysis_purpose=analysis_purpose,
        ):
            return transform_filter(
                self._frame,
                predicate=predicate,
                analysis_purpose=analysis_purpose,
            )

    def slice(
        self,
        *,
        slice_by: Mapping[_SemanticInput[DimensionKind | TimeDimensionKind], SliceValue],
        analysis_purpose: str | None = None,
    ) -> TFrame:
        """Filter rows by catalog-backed axis values.

        Args:
            slice_by: Mapping from exact current-catalog dimension/time-
                dimension entries or refs to scalar, list, or range values.
            analysis_purpose: Optional durable label explaining why this
                transform exists.

        Returns:
            A transformed frame of the same family as the receiver.

        Example:
            >>> us = frame.transform.slice(slice_by={country: "US"})

        Constraints:
            String keys and stale or cross-catalog entries are rejected.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.transform import transform_slice

        validate_capability_inputs("transform.slice", receiver=self._frame)
        session = require_current_session()
        with _track_materializing_operation(
            session,
            "marivo.analysis.frame.transform.slice",
            capability_id="transform.slice",
            family="transform",
            intent="slice",
            arguments={"receiver": self._frame, "slice_by": slice_by},
            analysis_purpose=analysis_purpose,
            attributes={"marivo.analysis.slice_count": len(slice_by)},
        ):
            return transform_slice(
                self._frame,
                slice_by=slice_by,
                analysis_purpose=analysis_purpose,
            )

    def rollup(
        self,
        *,
        drop_axes: list[_SemanticInput[DimensionKind | TimeDimensionKind]] | None = None,
        grain: TemporalGrain | None = None,
        analysis_purpose: str | None = None,
    ) -> TFrame:
        """Aggregate a frame by dropping axes or re-bucketing the time axis.

        Args:
            drop_axes: Exact current-catalog dimension/time-dimension entries
                or refs to remove before grouping.
            grain: Target time grain coarser than the current time axis
                (e.g. ``mv.grain("month")`` or a certified ``ms.calendar_grain(...)``).
                Semantic grains require a certified containment edge and complete
                source periods. Cumulative frames take the last bucket per period
                (``rollup_fold="last"``).
            analysis_purpose: Optional durable label explaining why this
                transform exists.

        Returns:
            A transformed frame of the same family as the receiver.

        Example:
            >>> daily = frame.transform.rollup(drop_axes=[country])
            >>> monthly = frame.transform.rollup(grain=mv.grain("month"))

        Constraints:
            At least one of ``drop_axes`` or ``grain`` is required.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.transform import transform_rollup

        validate_capability_inputs("transform.rollup", receiver=self._frame)
        session = require_current_session()
        axis_count = len(drop_axes) if drop_axes is not None else 0
        with _track_materializing_operation(
            session,
            "marivo.analysis.frame.transform.rollup",
            capability_id="transform.rollup",
            family="transform",
            intent="rollup",
            arguments={"receiver": self._frame, "drop_axes": drop_axes, "grain": grain},
            analysis_purpose=analysis_purpose,
            attributes={"marivo.analysis.axis_count": axis_count},
        ):
            return transform_rollup(
                self._frame,
                drop_axes=drop_axes,
                grain=grain,
                analysis_purpose=analysis_purpose,
            )

    def topk(self, *, by: str, limit: int, analysis_purpose: str | None = None) -> TFrame:
        """Keep the largest `limit` rows ordered by a public frame column.

        Args:
            by: Public frame column to sort descending.
            limit: Positive row count to keep.
            analysis_purpose: Optional durable label explaining why this
                transform exists.

        Returns:
            A transformed frame of the same family as the receiver.

        Example:
            >>> value_column = frame.value_columns[0] if isinstance(frame, mv.MetricFrame) else "delta"
            >>> biggest = frame.transform.topk(by=value_column, limit=10)

        Constraints:
            `by` is taken from `frame.columns`, not the canonical stored schema
            or a catalog ref.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.transform import transform_topk

        validate_capability_inputs("transform.topk", receiver=self._frame)
        session = require_current_session()
        with _track_materializing_operation(
            session,
            "marivo.analysis.frame.transform.topk",
            capability_id="transform.topk",
            family="transform",
            intent="topk",
            arguments={"receiver": self._frame, "by": by, "limit": limit},
            analysis_purpose=analysis_purpose,
            attributes={"marivo.analysis.limit": limit},
        ):
            return transform_topk(
                self._frame,
                by=by,
                limit=limit,
                analysis_purpose=analysis_purpose,
            )

    def bottomk(self, *, by: str, limit: int, analysis_purpose: str | None = None) -> TFrame:
        """Keep the smallest `limit` rows ordered by a public frame column.

        Args:
            by: Public frame column to sort ascending.
            limit: Positive row count to keep.
            analysis_purpose: Optional durable label explaining why this
                transform exists.

        Returns:
            A transformed frame of the same family as the receiver.

        Example:
            >>> value_column = frame.value_columns[0] if isinstance(frame, mv.MetricFrame) else "delta"
            >>> smallest = frame.transform.bottomk(by=value_column, limit=10)

        Constraints:
            `by` is taken from `frame.columns`. For deltas, the largest decline
            is the most-negative `delta`.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.transform import transform_bottomk

        validate_capability_inputs("transform.bottomk", receiver=self._frame)
        session = require_current_session()
        with _track_materializing_operation(
            session,
            "marivo.analysis.frame.transform.bottomk",
            capability_id="transform.bottomk",
            family="transform",
            intent="bottomk",
            arguments={"receiver": self._frame, "by": by, "limit": limit},
            analysis_purpose=analysis_purpose,
            attributes={"marivo.analysis.limit": limit},
        ):
            return transform_bottomk(
                self._frame,
                by=by,
                limit=limit,
                analysis_purpose=analysis_purpose,
            )

    def rank(
        self,
        *,
        by: str,
        method: RankMethod = "ordinal",
        rank_column: str = "rank",
        analysis_purpose: str | None = None,
    ) -> TFrame:
        """Add a rank column ordered by a public frame column.

        Args:
            by: Public frame column to rank descending.
            method: Tie-handling method: `ordinal`, `dense`, `min`, or `max`.
            rank_column: New public output column name. ``value`` is reserved
                for canonical MetricFrame persistence.
            analysis_purpose: Optional durable label explaining why this
                transform exists.

        Returns:
            A transformed frame of the same family as the receiver.

        Example:
            >>> value_column = frame.value_columns[0] if isinstance(frame, mv.MetricFrame) else "delta"
            >>> ranked = frame.transform.rank(
            ...     by=value_column, method="dense", rank_column="rank"
            ... )

        Constraints:
            `by` is taken from `frame.columns`; `rank_column` must not already
            exist and cannot use a canonical storage-reserved name.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.transform import transform_rank

        validate_capability_inputs("transform.rank", receiver=self._frame)
        session = require_current_session()
        with _track_materializing_operation(
            session,
            "marivo.analysis.frame.transform.rank",
            capability_id="transform.rank",
            family="transform",
            intent="rank",
            arguments={
                "receiver": self._frame,
                "by": by,
                "method": method,
                "rank_column": rank_column,
            },
            analysis_purpose=analysis_purpose,
        ):
            return transform_rank(
                self._frame,
                by=by,
                method=method,
                rank_column=rank_column,
                analysis_purpose=analysis_purpose,
            )

    def window(self, *, window: TimeScope, analysis_purpose: str | None = None) -> TFrame:
        """Restrict a time-series or panel frame to a half-open time window.

        Args:
            window: Time scope with `start` and `end` bounds.
            analysis_purpose: Optional durable label explaining why this
                transform exists.

        Returns:
            A transformed frame of the same family as the receiver.

        Example:
            >>> recent = frame.transform.window(
            ...     window=mv.time_scope(start="2026-02-01", end="2026-03-01")
            ... )

        Constraints:
            Requires a persisted time axis.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.transform import transform_window

        validate_capability_inputs("transform.window", receiver=self._frame, window=window)
        session = require_current_session()
        with _track_materializing_operation(
            session,
            "marivo.analysis.frame.transform.window",
            capability_id="transform.window",
            family="transform",
            intent="window",
            arguments={"receiver": self._frame, "window": window},
            analysis_purpose=analysis_purpose,
        ):
            return transform_window(
                self._frame,
                window=window,
                analysis_purpose=analysis_purpose,
            )


@dataclass(frozen=True)
class MetricFrameTransforms(_FrameTransforms[MetricFrame]):
    _frame: MetricFrame

    def normalize(
        self,
        *,
        mode: NormalizeKind,
        baseline: NormalizeBaseline | None = None,
        analysis_purpose: str | None = None,
    ) -> MetricFrame:
        """Normalize MetricFrame values.

        Args:
            mode: One of `index`, `share`, `pct_change`, `per_unit`, or `z_score`.
            baseline: Optional baseline value or row selector for `index` and
                `per_unit` modes.
            analysis_purpose: Optional durable label explaining why this
                transform exists.

        Returns:
            A transformed MetricFrame.

        Example:
            >>> share = frame.transform.normalize(mode="share")

        Constraints:
            Only MetricFrame exposes normalize; DeltaFrameTransforms has no
            normalize method. Normalized outputs are not plain-sum
            reaggregatable in v1; re-observe at the target grain or dimensions
            before normalizing when a coarser result is required.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.transform import transform_normalize

        validate_capability_inputs("transform.normalize", receiver=self._frame)
        session = require_current_session()
        with _track_materializing_operation(
            session,
            "marivo.analysis.frame.transform.normalize",
            capability_id="transform.normalize",
            family="transform",
            intent="normalize",
            arguments={"receiver": self._frame, "mode": mode, "baseline": baseline},
            analysis_purpose=analysis_purpose,
            attributes={"marivo.analysis.normalize_mode": str(mode)},
        ):
            return transform_normalize(
                self._frame,
                mode=mode,
                baseline=baseline,
                analysis_purpose=analysis_purpose,
            )


@dataclass(frozen=True)
class DeltaFrameTransforms(_FrameTransforms[DeltaFrame]):
    _frame: DeltaFrame
