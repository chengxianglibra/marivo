"""Non-canonical scratch exploration result frame."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import copy
from dataclasses import dataclass
from typing import Literal

import pandas as pd
from pandas.api.types import is_object_dtype
from pydantic import ConfigDict, Field

from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta


class ExplorationResultMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["exploration_result"] = "exploration_result"
    source_kind: Literal["pandas", "ibis"]
    description: str | None = None
    source_query: str | None = None
    source_datasource: str | None = None
    source_artifact_refs: list[str] = Field(default_factory=list)
    promotion_refs: list[str] = Field(default_factory=list)


def _isolate_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    isolated = df.copy(deep=True)
    for column in [col for col in isolated.columns if is_object_dtype(isolated[col].dtype)]:
        isolated[column] = isolated[column].map(copy.deepcopy)
    return isolated


@dataclass
class ExplorationResult(BaseFrame):
    meta: ExplorationResultMeta

    def to_pandas(self) -> pd.DataFrame:
        """Return a recursively isolated copy of the wrapped DataFrame."""
        return _isolate_dataframe(self._df)
