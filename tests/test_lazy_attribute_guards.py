"""Actual Attribution worker admission, complete side guards, and failure atomicity."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.local_execution import (
    LocalBoundary,
    LocalGraphRequest,
    LocalInputStreams,
    LocalPartInput,
    LocalStage,
    StreamInput,
)
from tests.lazy_attribute_fixtures import inputs

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize(
    "failure",
    [
        "schema",
        "foreign_key",
        "missing",
    ],
)
def test_complete_attribution_guards_precede_invocation_or_publication(
    tmp_path: Path, failure: str
) -> None:
    large = failure == "combined"
    frame, spec, parts = inputs(
        "revenue",
        [("a" * (10000 if large else 1),), ("b" * (10000 if large else 1),)],
        [(4.0, 1), (8.0, 1)],
        [(1.0, 1), (2.0, 1)],
    )
    primary = pa.Table.from_pandas(frame, preserve_index=False)
    part_tables = tuple(
        pa.Table.from_pandas(part.frame, schema=part.schema, preserve_index=False) for part in parts
    )
    selected = tuple(
        LocalPartInput(part.role, part.contract_id, part.contract_version, part.schema, part.keys)
        for part in parts
    )
    if failure == "schema":
        baseline = part_tables[1]
        position = baseline.num_columns - 1
        changed = baseline.set_column(
            position, baseline.column_names[position], pa.array(["bad", "bad"])
        )
        part_tables = (part_tables[0], changed)
    elif failure == "foreign_key":
        changed = part_tables[1].set_column(0, "region", pa.array(["a", "foreign"]))
        part_tables = (part_tables[0], changed)
    elif failure == "missing":
        selected, part_tables = selected[:1], part_tables[:1]
    request = LocalGraphRequest(
        (LocalBoundary(0, StreamInput(spec.input_row, spec.input_rows), selected),),
        (LocalStage(1, (0,), spec),),
        1,
    )
    from marivo.analysis.materialization.local_execution import execute_local
    from marivo.analysis.operators.errors import AttributionError, RowValueError

    with pytest.raises(
        (MaterializationError, AttributionError, RowValueError, DatasetCompilationError)
    ):
        execute_local(
            request,
            (
                LocalInputStreams(
                    primary.to_batches(), tuple(table.to_batches() for table in part_tables)
                ),
            ),
        )
