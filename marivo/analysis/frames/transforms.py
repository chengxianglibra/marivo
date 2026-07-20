"""Typed frame-local transform namespaces."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import pandas as pd

from marivo.analysis.frames.delta import DeltaFrame
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.intents.transform import NormalizeBaseline, NormalizeKind, RankMethod
from marivo.analysis.session._runtime import require_current_session
from marivo.analysis.session.core import _track_session_operation
from marivo.analysis.slice_types import SliceValue
from marivo.analysis.windows import TimeScopeInput
from marivo.refs import FieldKind, Ref


@dataclass(frozen=True)
class _FrameTransforms[TFrame: (MetricFrame, DeltaFrame)]:
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
            >>> focused = frame.transform.filter(predicate=lambda df: df["value"] > 0)

        Constraints:
            Requires the frame's owning session to be current and writable.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.transform import transform_filter

        validate_capability_inputs("transform.filter", receiver=self._frame)
        session = require_current_session()
        with _track_session_operation(
            session,
            "marivo.analysis.frame.transform.filter",
            family="transform",
            intent="filter",
        ):
            return transform_filter(
                self._frame,
                predicate=predicate,
                analysis_purpose=analysis_purpose,
            )

    def slice(
        self,
        *,
        slice_by: Mapping[Ref[FieldKind], SliceValue],
        analysis_purpose: str | None = None,
    ) -> TFrame:
        """Filter rows by catalog-backed axis values.

        Args:
            slice_by: Mapping from exact dimension refs to scalar, list, or
                range selector values.
            analysis_purpose: Optional durable label explaining why this
                transform exists.

        Returns:
            A transformed frame of the same family as the receiver.

        Example:
            >>> us = frame.transform.slice(slice_by={country.ref: "US"})

        Constraints:
            String dimension keys and loaded catalog objects are rejected; pass exact refs.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.transform import transform_slice

        validate_capability_inputs("transform.slice", receiver=self._frame)
        session = require_current_session()
        with _track_session_operation(
            session,
            "marivo.analysis.frame.transform.slice",
            family="transform",
            intent="slice",
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
        drop_axes: list[Ref[FieldKind]] | None = None,
        grain: str | None = None,
        analysis_purpose: str | None = None,
    ) -> TFrame:
        """Aggregate a frame by dropping axes or re-bucketing the time axis.

        Args:
            drop_axes: Exact catalog dimension refs to remove before grouping.
            grain: Target time grain coarser than the current time axis
                (e.g. ``"month"``). Cumulative frames take the last bucket per
                period (``rollup_fold="last"``).
            analysis_purpose: Optional durable label explaining why this
                transform exists.

        Returns:
            A transformed frame of the same family as the receiver.

        Example:
            >>> daily = frame.transform.rollup(drop_axes=[country.ref])
            >>> monthly = frame.transform.rollup(grain="month")

        Constraints:
            At least one of ``drop_axes`` or ``grain`` is required.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.transform import transform_rollup

        validate_capability_inputs("transform.rollup", receiver=self._frame)
        session = require_current_session()
        axis_count = len(drop_axes) if drop_axes is not None else 0
        with _track_session_operation(
            session,
            "marivo.analysis.frame.transform.rollup",
            family="transform",
            intent="rollup",
            attributes={"marivo.analysis.axis_count": axis_count},
        ):
            return transform_rollup(
                self._frame,
                drop_axes=drop_axes,
                grain=grain,
                analysis_purpose=analysis_purpose,
            )

    def topk(self, *, by: str, limit: int, analysis_purpose: str | None = None) -> TFrame:
        """Keep the largest `limit` rows ordered by a persisted column.

        Args:
            by: Persisted frame column to sort descending.
            limit: Positive row count to keep.
            analysis_purpose: Optional durable label explaining why this
                transform exists.

        Returns:
            A transformed frame of the same family as the receiver.

        Example:
            >>> biggest = delta.transform.topk(by="delta", limit=10)

        Constraints:
            `by` is a raw column name, not a catalog ref.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.transform import transform_topk

        validate_capability_inputs("transform.topk", receiver=self._frame)
        session = require_current_session()
        with _track_session_operation(
            session,
            "marivo.analysis.frame.transform.topk",
            family="transform",
            intent="topk",
            attributes={"marivo.analysis.limit": limit},
        ):
            return transform_topk(
                self._frame,
                by=by,
                limit=limit,
                analysis_purpose=analysis_purpose,
            )

    def bottomk(self, *, by: str, limit: int, analysis_purpose: str | None = None) -> TFrame:
        """Keep the smallest `limit` rows ordered by a persisted column.

        Args:
            by: Persisted frame column to sort ascending.
            limit: Positive row count to keep.
            analysis_purpose: Optional durable label explaining why this
                transform exists.

        Returns:
            A transformed frame of the same family as the receiver.

        Example:
            >>> declines = delta.transform.bottomk(by="delta", limit=10)

        Constraints:
            For deltas, the largest decline is the most-negative `delta`.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.transform import transform_bottomk

        validate_capability_inputs("transform.bottomk", receiver=self._frame)
        session = require_current_session()
        with _track_session_operation(
            session,
            "marivo.analysis.frame.transform.bottomk",
            family="transform",
            intent="bottomk",
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
        """Add a rank column ordered by a persisted value column.

        Args:
            by: Persisted frame column to rank descending.
            method: Tie-handling method: `ordinal`, `dense`, `min`, or `max`.
            rank_column: New output column name.
            analysis_purpose: Optional durable label explaining why this
                transform exists.

        Returns:
            A transformed frame of the same family as the receiver.

        Example:
            >>> ranked = frame.transform.rank(by="value", method="dense", rank_column="rank")

        Constraints:
            `rank_column` must not already exist.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.transform import transform_rank

        validate_capability_inputs("transform.rank", receiver=self._frame)
        session = require_current_session()
        with _track_session_operation(
            session,
            "marivo.analysis.frame.transform.rank",
            family="transform",
            intent="rank",
        ):
            return transform_rank(
                self._frame,
                by=by,
                method=method,
                rank_column=rank_column,
                analysis_purpose=analysis_purpose,
            )

    def window(self, *, window: TimeScopeInput, analysis_purpose: str | None = None) -> TFrame:
        """Restrict a time-series or panel frame to a half-open time window.

        Args:
            window: Time scope with `start` and `end` bounds.
            analysis_purpose: Optional durable label explaining why this
                transform exists.

        Returns:
            A transformed frame of the same family as the receiver.

        Example:
            >>> recent = frame.transform.window(window={"start": "2026-02-01", "end": "2026-03-01"})

        Constraints:
            Requires a persisted time axis.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.transform import transform_window

        validate_capability_inputs("transform.window", receiver=self._frame, window=window)
        session = require_current_session()
        with _track_session_operation(
            session,
            "marivo.analysis.frame.transform.window",
            family="transform",
            intent="window",
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
            normalize method.
        """
        from marivo.analysis._capabilities.validation import validate_capability_inputs
        from marivo.analysis.intents.transform import transform_normalize

        validate_capability_inputs("transform.normalize", receiver=self._frame)
        session = require_current_session()
        with _track_session_operation(
            session,
            "marivo.analysis.frame.transform.normalize",
            family="transform",
            intent="normalize",
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
