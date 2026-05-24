"""Base frame wrapper and metadata."""
# mypy: disable-error-code=import-untyped

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

import pandas as pd
from pydantic import BaseModel, ConfigDict

from marivo.analysis_py.errors import FrameMutationError
from marivo.analysis_py.lineage import Lineage


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

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}[ref={self.meta.ref}, "
            f"kind={self.meta.kind}, row_count={self.meta.row_count}]"
        )

    def _repr_html_(self) -> str:
        try:
            body = self._df._repr_html_()
        except Exception:
            body = f"<pre>{self._df.head(5)}</pre>"
        return f"<div><strong>{self!r}</strong>{body}</div>"
