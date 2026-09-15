"""Independent complete-input, allocation, output and hard-worker guard tests."""

from pathlib import Path

import pyarrow as pa
import pytest

from marivo.analysis.materialization.contracts import LocalReceipt
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.local import (
    collect_part,
    collect_primary,
)
from tests.lazy_local_fixtures import (
    REVENUE,
    setup_local,
)

pytestmark = pytest.mark.runtime


def _table(count: int) -> pa.Table:
    return pa.table(
        {
            "entity_identity": pa.array(
                [{"id": index} for index in range(count)], type=pa.struct([("id", pa.int64())])
            ),
            "revenue": pa.array([float(index) for index in range(count)]),
        }
    )


def test_late_schema_failure_never_admits_a_partial_input(tmp_path: Path) -> None:
    _, sources, _ = setup_local(tmp_path)
    source = sources.observe(REVENUE)
    valid = _table(2).to_batches()[0]
    invalid = pa.record_batch([valid.column(0), pa.array(["x", "y"])], names=valid.schema.names)
    with pytest.raises(MaterializationError, match="logical type mismatch"):
        collect_primary(
            [valid, invalid],
            source.row_contract,
            source.row_set_contract,
        )


def test_registered_part_read_checks_exact_role_schema_hash_and_bounds(tmp_path: Path) -> None:
    from marivo.analysis.materialization.storage import (
        PartWriteSpec,
        read_part_batches,
        write_local_dataset,
    )

    _, sources, _ = setup_local(tmp_path)
    source = sources.observe(REVENUE)
    table = _table(3).append_column("denominator", pa.array([10.0, 20.0, 30.0]))
    output = write_local_dataset(
        project_root=tmp_path,
        staging_path=tmp_path / "stage",
        final_path=tmp_path / "output",
        batches=table.to_batches(),
        row_contract=source.row_contract,
        row_set_contract=source.row_set_contract,
        parts=(
            PartWriteSpec(
                "selected_state",
                "metric.sufficient_components",
                1,
                ("entity_identity", "denominator"),
            ),
        ),
        event=lambda point: None,
    )
    selected = output.retained_parts[0]
    schema = pa.schema(
        [("entity_identity", pa.struct([("id", pa.int64())])), ("denominator", pa.float64())]
    )
    result = collect_part(
        read_part_batches(tmp_path, selected, expected_schema=schema),
        schema,
        ("entity_identity",),
    )
    assert result["denominator"].to_pylist() == [10.0, 20.0, 30.0]
    with pytest.raises(MaterializationError, match="schema"):
        list(
            read_part_batches(
                tmp_path, selected, expected_schema=pa.schema([("wrong", pa.int64())])
            )
        )
    receipt = selected.storage_receipt
    assert isinstance(receipt, LocalReceipt)
    data = tmp_path / receipt.project_relative_path / "data.parquet"
    data.unlink()
    with pytest.raises(MaterializationError):
        list(read_part_batches(tmp_path, selected, expected_schema=schema))


def test_exact_timestamp_decimal_and_integer_part_types() -> None:
    from datetime import datetime, timezone
    from decimal import Decimal

    schema = pa.schema(
        [
            pa.field("id", pa.int64(), nullable=False),
            pa.field("when", pa.timestamp("us", tz="UTC")),
            pa.field("value", pa.decimal128(12, 2)),
        ]
    )
    table = pa.Table.from_arrays(
        [
            pa.array([1, 2], type=pa.int64()),
            pa.array(
                [datetime(2026, 1, 1, tzinfo=timezone.utc), None], type=schema.field("when").type
            ),
            pa.array([Decimal("10.25"), None], type=schema.field("value").type),
        ],
        schema=schema,
    )
    assert collect_part(table.to_batches(), schema, ("id",)).equals(table)
    for wrong in (
        pa.schema(
            [schema.field("id"), pa.field("when", pa.timestamp("ms")), schema.field("value")]
        ),
        pa.schema(
            [schema.field("id"), schema.field("when"), pa.field("value", pa.decimal128(12, 3))]
        ),
    ):
        with pytest.raises(MaterializationError, match="schema mismatch"):
            collect_part(
                table.to_batches(),
                wrong,
                ("id",),
            )
    widened = pa.table({"id": pa.array([2**63], type=pa.uint64())})
    with pytest.raises(MaterializationError, match="schema mismatch"):
        collect_part(
            widened.to_batches(),
            pa.schema([("id", pa.int64())]),
            ("id",),
        )
