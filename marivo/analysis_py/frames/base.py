"""Base frame wrapper and metadata."""
# mypy: disable-error-code=import-untyped

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import Any, cast

import pandas as pd
from pydantic import BaseModel, ConfigDict

from marivo.analysis_py.errors import FrameMutationError
from marivo.analysis_py.lineage import Lineage

_REPR_MAX_ROWS = 3
_REPR_MAX_COLUMNS = 8
_REPR_MAX_TEXT_WIDTH = 40


def _truncate_repr_text(value: Any) -> str:
    text = str(value)
    if len(text) <= _REPR_MAX_TEXT_WIDTH:
        return text
    return f"{text[: _REPR_MAX_TEXT_WIDTH - 3]}..."


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


@dataclass
class BaseFrame:
    _df: pd.DataFrame
    meta: BaseFrameMeta

    @property
    def ref(self) -> str:
        return self.meta.ref

    @property
    def lineage(self) -> Lineage:
        return self.meta.lineage

    def to_pandas(self) -> pd.DataFrame:
        """Return a defensive copy of the wrapped DataFrame."""
        return self._df.copy()

    def __getitem__(self, key: Any) -> Any:
        return self._df[key]

    def head(self, n: int = 10) -> pd.DataFrame:
        return self._df.head(n)

    def describe(self) -> pd.DataFrame:
        return self._df.describe()

    @property
    def shape(self) -> tuple[int, int]:
        return cast("tuple[int, int]", self._df.shape)

    @property
    def columns(self) -> list[str]:
        return list(self._df.columns)

    def plot(self, *args: Any, **kwargs: Any) -> Any:
        return self._df.plot(*args, **kwargs)

    def __len__(self) -> int:
        return len(self._df)

    def __iter__(self) -> Iterator[str]:
        return iter(self._df)

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

    def summary(self) -> FrameSummary:
        n = len(self._df)
        columns: list[str] = []
        used_columns: set[str] = set()
        for column in self._df.columns:
            column_name = str(column)
            display_name = column_name
            suffix = 2
            while display_name in used_columns:
                display_name = f"{column_name}#{suffix}"
                suffix += 1
            used_columns.add(display_name)
            columns.append(display_name)

        null_ratios = {
            column: 0.0 if n == 0 else float(self._df.iloc[:, idx].isna().sum()) / n
            for idx, column in enumerate(columns)
        }
        step_intents = [step.intent for step in self.meta.lineage.steps]
        lineage_oneliner = " -> ".join(step_intents) if step_intents else "(empty)"

        return FrameSummary(
            kind=self.meta.kind,
            ref=self.meta.ref,
            row_count=n,
            columns=columns,
            null_ratios=null_ratios,
            produced_by_job=self.meta.produced_by_job,
            lineage_oneliner=lineage_oneliner,
        )

    def __repr__(self) -> str:
        visible_columns = list(self._df.columns[:_REPR_MAX_COLUMNS])
        omitted_columns = max(0, len(self._df.columns) - _REPR_MAX_COLUMNS)
        header_columns = [_truncate_repr_text(column) for column in visible_columns]
        if omitted_columns:
            header_columns.append(f"...+{omitted_columns}")
        cols = ",".join(header_columns)
        header = (
            f"<{type(self).__name__} ref={self.meta.ref} kind={self.meta.kind} "
            f"rows={self.meta.row_count} cols=[{cols}]>"
        )
        if len(self._df) == 0:
            return header

        notes: list[str] = []
        if omitted_columns:
            notes.append(f"  ... (+{omitted_columns} more columns)")
        if len(self._df) > _REPR_MAX_ROWS:
            remaining = len(self._df) - _REPR_MAX_ROWS
            notes.append(
                f"  ... ({remaining} more rows, use .to_pandas() to materialize)",
            )

        preview_source = self._df.iloc[:_REPR_MAX_ROWS, :_REPR_MAX_COLUMNS]
        preview = pd.DataFrame(
            [
                [_truncate_repr_text(value) for value in row]
                for row in preview_source.itertuples(index=False, name=None)
            ],
            columns=[_truncate_repr_text(column) for column in preview_source.columns],
        )
        preview_text = preview.to_string(index=False)
        return "\n".join([header, preview_text, *notes])

    def _repr_html_(self) -> str:
        return f"<pre>{escape(repr(self))}</pre>"
