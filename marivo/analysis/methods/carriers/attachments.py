"""Owned in-memory data for one current coordinate attachment."""

from dataclasses import dataclass

import pandas as pd

from marivo.analysis.model.data_schema import DataSchema
from marivo.analysis.model.data_types import require_data_text
from marivo.analysis.model.errors import reject


@dataclass(frozen=True, slots=True)
class OutputAttachment:
    name: str
    schema: DataSchema
    rows: pd.DataFrame

    def __post_init__(self) -> None:
        require_data_text(self.name, "publication.attachment")
        _output_table(self.schema, self.rows)


def _output_table(schema: DataSchema, rows: pd.DataFrame) -> None:
    if type(schema) is not DataSchema or not isinstance(rows, pd.DataFrame):
        reject(
            "a final data schema and pandas DataFrame",
            type(rows).__name__,
            "Finish computation before requesting publication.",
            "artifact.publication",
        )
    if tuple(rows.columns) != tuple(column.slot_id for column in schema.columns):
        reject(
            "final pandas columns in frozen output slot order",
            repr(tuple(rows.columns)),
            "Project only the declared final output columns before publication.",
            "artifact.publication",
        )
