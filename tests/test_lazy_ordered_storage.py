"""Complete ordered-stream validation stays distinct from source row-key uniqueness."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pyarrow as pa
import pytest

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.storage import write_local_dataset
from marivo.refs import ref
from tests.lazy_observation_fixtures import make_sources


def test_metric_order_requires_independent_key_proof_and_checks_full_stream(tmp_path: Path) -> None:
    dataset = make_sources().observe([ref.metric("sales.revenue")])
    by_name = {column.name: column for column in dataset.schema.columns}
    rows = replace(
        dataset.row_set_contract,
        _token=d._CORE_TOKEN,
        ordering=d._ordered_ordering(
            (
                d._make_order_term(
                    by_name["revenue"].field_id,
                    direction="descending",
                    nulls="last",
                    value_order_contract_id="observation.scalar_order@v1",
                    ids=dataset._registration.ids,
                ),
                d._make_order_term(
                    by_name["entity_identity"].field_id,
                    direction="ascending",
                    nulls="last",
                    value_order_contract_id="observation.identity_tuple@v1",
                    ids=dataset._registration.ids,
                ),
            )
        ),
    )
    table = pa.table(
        {
            "entity_identity": pa.array(
                [{"id": 3}, {"id": 1}, {"id": 2}], type=pa.struct([pa.field("id", pa.int64())])
            ),
            "revenue": pa.array([30.0, 10.0, 10.0]),
        }
    )
    with pytest.raises(MaterializationError, match="does not prove row-key uniqueness"):
        write_local_dataset(
            project_root=tmp_path,
            staging_path=tmp_path / "unproved",
            final_path=tmp_path / "invalid",
            batches=table.to_batches(),
            row_contract=dataset.row_contract,
            row_set_contract=rows,
            event=lambda _: None,
        )
    result = write_local_dataset(
        project_root=tmp_path,
        staging_path=tmp_path / "staging",
        final_path=tmp_path / "output",
        batches=table.to_batches(max_chunksize=1),
        row_contract=dataset.row_contract,
        row_set_contract=rows,
        source_key_validation=True,
        event=lambda _: None,
    )
    assert result.realized_row_count == 3
    unsorted = table.take(pa.array([1, 0, 2]))
    with pytest.raises(MaterializationError, match="unordered ordering tuple"):
        write_local_dataset(
            project_root=tmp_path,
            staging_path=tmp_path / "unordered",
            final_path=tmp_path / "unordered-output",
            batches=unsorted.to_batches(max_chunksize=1),
            row_contract=dataset.row_contract,
            row_set_contract=rows,
            source_key_validation=True,
            event=lambda _: None,
        )
