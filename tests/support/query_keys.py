"""Typed complete-key carriers shared by parameter transport regressions."""

import pandas as pd

from marivo.analysis.methods.carriers.stages import StageOutput
from marivo.analysis.model.data_types import DataColumn, ScalarDataType


def integer_string_keys(rows: tuple[tuple[int, str | None], ...]) -> StageOutput:
    return StageOutput.from_frame(
        pd.DataFrame(rows, columns=["id", "tenant"]),
        (
            DataColumn("id", "id", ScalarDataType("int64"), False),
            DataColumn("tenant", "tenant", ScalarDataType("string"), True),
        ),
        ("id", "tenant"),
    )
