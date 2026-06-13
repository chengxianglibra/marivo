"""Base frame wrapper and metadata."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from marivo.analysis.errors import (
    FrameMutationError,
    FrameReadError,
    SemanticKindMismatchError,
)
from marivo.analysis.evidence.types import QualitySummary
from marivo.analysis.followups import BlockingIssue, ConfidenceScope, FollowupAction
from marivo.analysis.frames.render import format_bounded_card
from marivo.analysis.lineage import Lineage
from marivo.render import result_repr

_RENDER_PREVIEW_ROWS = 5
_RENDER_MAX_COLUMNS = 8
_PREVIEW_DEFAULT_LIMIT = 10
_PREVIEW_MAX_LIMIT = 100


def _display_column_names(columns: pd.Index) -> list[str]:
    display_columns: list[str] = []
    used_columns: set[str] = set()
    for column in columns:
        column_name = str(column)
        display_name = column_name
        suffix = 2
        while display_name in used_columns:
            display_name = f"{column_name}#{suffix}"
            suffix += 1
        used_columns.add(display_name)
        display_columns.append(display_name)
    return display_columns


def _is_missing(value: Any) -> bool:
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        return False
    if isinstance(missing, bool):
        return missing
    item = getattr(missing, "item", None)
    if callable(item):
        try:
            scalar = item()
        except (TypeError, ValueError):
            return False
        return scalar if isinstance(scalar, bool) else False
    return False


def _preview_cell(value: Any) -> Any:
    if _is_missing(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, pd.Timedelta):
        return str(value)
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except (TypeError, ValueError):
            return value
    return value


def assert_semantic_shape(*, got: str, expected: str, frame_kind: str) -> None:
    """Raise SemanticKindMismatchError unless ``got`` semantic shape matches ``expected``."""
    if got != expected:
        raise SemanticKindMismatchError(
            message=f"{frame_kind} semantic_shape is {got!r}, expected {expected!r}",
            details={
                "got_semantic_shape": got,
                "expected_semantic_shape": expected,
                "frame_kind": frame_kind,
            },
        )


def assert_attribution_shape(*, got: str, expected: str, frame_kind: str) -> None:
    """Raise SemanticKindMismatchError unless ``got`` attribution shape matches ``expected``."""
    if got != expected:
        raise SemanticKindMismatchError(
            message=f"{frame_kind} attribution_shape is {got!r}, expected {expected!r}",
            details={
                "got_attribution_shape": got,
                "expected_attribution_shape": expected,
                "frame_kind": frame_kind,
            },
        )


class FrameSummary(BaseModel):
    """Compact, stable summary of a frame without materializing a copy."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    ref: str
    row_count: int
    columns: list[str]
    null_ratios: dict[str, float]
    produced_by_job: str | None
    lineage_oneliner: str
    semantic_shape: str | None = None

    def _repr_identity(self) -> str:
        return f"FrameSummary kind={self.kind} ref={self.ref} rows={self.row_count}"

    def render(self) -> str:
        return format_bounded_card(
            identity=self._repr_identity(),
            status=self.lineage_oneliner,
            columns=list(self.columns),
            available=(".render()", ".show()"),
        )

    def __repr__(self) -> str:
        return result_repr(self._repr_identity())

    def show(self) -> None:
        print(self.render())


class FramePreview(BaseModel):
    """Bounded row projection for agent-facing frame inspection."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    ref: str
    row_count: int
    returned_row_count: int
    columns: list[str]
    rows: list[dict[str, Any]]
    is_truncated: bool


class BaseFrameMeta(BaseModel):
    """Shared ownership and provenance fields for every frame family."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    ref: str
    session_id: str
    project_root: str
    produced_by_job: str | None
    created_at: datetime
    row_count: int
    byte_size: int
    lineage: Lineage = Lineage()
    artifact_id: str | None = None
    evidence_status: Literal["complete", "partial", "unavailable"] = "unavailable"
    confidence_scope: ConfidenceScope | None = None
    quality: QualitySummary | None = None
    blocking_issues: list[BlockingIssue] = Field(default_factory=list)
    recommended_followups: list[FollowupAction] = Field(default_factory=list)


@dataclass(repr=False)
class BaseFrame:
    _df: pd.DataFrame
    meta: BaseFrameMeta

    _NEXT_INTENTS: tuple[str, ...] = ()
    _AVAILABLE_ENTRIES: tuple[str, ...] = (
        ".summary()",
        ".preview(limit=...)",
        ".to_pandas()",
        ".render()",
    )

    @property
    def ref(self) -> str:
        return self.meta.ref

    @property
    def lineage(self) -> Lineage:
        return self.meta.lineage

    def next_intents(self) -> tuple[str, ...]:
        """Return the intent names that accept this frame as input."""
        return type(self)._NEXT_INTENTS

    def to_pandas(self) -> pd.DataFrame:
        """Return a defensive copy of the wrapped DataFrame."""
        return self._df.copy()

    def __getitem__(self, key: Any) -> Any:
        return self._df[key]

    def describe(self) -> pd.DataFrame:
        return self._df.describe()

    @property
    def shape(self) -> tuple[int, int]:
        return self._df.shape

    @property
    def columns(self) -> list[str]:
        return list(self._df.columns)

    def plot(self, *args: Any, **kwargs: Any) -> Any:
        return self._df.plot(*args, **kwargs)

    def __len__(self) -> int:
        return len(self._df)

    def __iter__(self) -> Iterator[str]:
        return iter(self.columns)

    def __setitem__(self, key: Any, value: Any) -> None:
        raise FrameMutationError(
            message="frame is immutable; call .to_pandas() to operate on a copy",
        )

    def __add__(self, other: Any) -> Any:
        raise FrameMutationError(
            message="frame arithmetic is blocked; call .to_pandas() first",
        )

    def __sub__(self, other: Any) -> Any:
        raise FrameMutationError(
            message="frame arithmetic is blocked; call .to_pandas() first",
        )

    def __mul__(self, other: Any) -> Any:
        raise FrameMutationError(
            message="frame arithmetic is blocked; call .to_pandas() first",
        )

    def __truediv__(self, other: Any) -> Any:
        raise FrameMutationError(
            message="frame arithmetic is blocked; call .to_pandas() first",
        )

    def preview(self, limit: int = _PREVIEW_DEFAULT_LIMIT) -> FramePreview:
        if limit < 1 or limit > _PREVIEW_MAX_LIMIT:
            raise FrameReadError(
                message="preview limit must be between 1 and 100",
                details={"limit": limit, "min": 1, "max": _PREVIEW_MAX_LIMIT},
            )

        row_count = len(self._df)
        columns = _display_column_names(self._df.columns)
        preview_source = self._df.head(limit)
        rows = [
            {column: _preview_cell(value) for column, value in zip(columns, row, strict=True)}
            for row in preview_source.itertuples(index=False, name=None)
        ]
        return FramePreview(
            kind=self.meta.kind,
            ref=self.meta.ref,
            row_count=row_count,
            returned_row_count=len(rows),
            columns=columns,
            rows=rows,
            is_truncated=row_count > limit,
        )

    def summary(self) -> FrameSummary:
        n = len(self._df)
        columns = _display_column_names(self._df.columns)

        null_ratios = {
            column: 0.0 if n == 0 else float(self._df.iloc[:, idx].isna().sum()) / n
            for idx, column in enumerate(columns)
        }
        step_intents = [step.intent for step in self.meta.lineage.steps]
        lineage_oneliner = " -> ".join(step_intents) if step_intents else "(empty)"

        raw_shape = getattr(self.meta, "semantic_kind", None)
        semantic_shape = raw_shape if isinstance(raw_shape, str) else None

        return FrameSummary(
            kind=self.meta.kind,
            ref=self.meta.ref,
            row_count=n,
            columns=columns,
            null_ratios=null_ratios,
            produced_by_job=self.meta.produced_by_job,
            lineage_oneliner=lineage_oneliner,
            semantic_shape=semantic_shape,
        )

    def _repr_identity(self) -> str:
        return f"{type(self).__name__} ref={self.meta.ref} rows={self.meta.row_count}"

    def _render_status(self) -> str | None:
        parts: list[str] = []
        if self.meta.evidence_status != "unavailable":
            parts.append(f"evidence={self.meta.evidence_status}")
        if self.meta.quality is not None:
            compat = self.meta.quality.metric_definition_compatibility
            if compat is not None:
                parts.append(f"quality={compat}")
        return " ".join(parts) if parts else None

    def __repr__(self) -> str:
        return f"<{self._repr_identity()}; call .show() to inspect>"

    def _repr_html_(self) -> None:
        return None

    def render(self) -> str:
        """Return bounded plain-text result card without a trailing newline.

        Returns:
            Bounded plain text suitable for terminal/agent inspection.

        Example:
            >>> print(frame.render())
            MetricFrame ref=frame_ab12 metric=sales.revenue shape=time_series rows=7
            ...
        """
        columns = _display_column_names(self._df.columns)
        visible_columns = columns[:_RENDER_MAX_COLUMNS]
        preview_rows: list[list[str]] = []
        for row in self._df.head(_RENDER_PREVIEW_ROWS).itertuples(index=False, name=None):
            preview_rows.append([str(_preview_cell(v)) for v in row[:_RENDER_MAX_COLUMNS]])
        return format_bounded_card(
            identity=self._repr_identity(),
            status=self._render_status(),
            columns=visible_columns,
            rows=preview_rows,
            row_count=len(self._df),
            preview_truncation_hint="call .preview(limit=...) or .to_pandas()",
            available=self._AVAILABLE_ENTRIES,
        )

    def show(self) -> None:
        """Print render() output followed by a trailing newline and return None.

        Returns:
            None

        Example:
            >>> frame.show()
            MetricFrame ref=frame_ab12 ...
        """
        print(self.render())
