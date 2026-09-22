"""Review acceptance for inherited sampling disclosure and substantial object inputs."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

import duckdb
import pytest

from marivo.analysis.datasets.base import MaterializedDataset
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import LocalReceipt
from marivo.analysis.observation.metric import MaterializedMetricDataset
from marivo.analysis.observation.population import MaterializedPopulationDataset
from marivo.analysis.observation.predicates import gt
from marivo.refs import ref
from tests.lazy_adapter_runtime_worker import snapshot
from tests.lazy_local_fixtures import pandas_methods
from tests.lazy_retained_fixtures import setup_retained

pytestmark = pytest.mark.runtime

REVENUE = ref.metric("sales.revenue")
MEAN = ref.metric("sales.mean_amount")
CUSTOMERS = ref.entity("sales.customers")


def _show(dataset: MaterializedDataset) -> str:
    assert isinstance(dataset, (MaterializedMetricDataset, MaterializedPopulationDataset))
    with redirect_stdout(io.StringIO()) as rendered:
        dataset.show()
    return rendered.getvalue()


def test_substantial_local_metric_checkpoint_folds_with_source_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = setup_retained(tmp_path, "local")
    count = 20_000
    with duckdb.connect(str(fixture.database)) as backend:
        backend.execute("DELETE FROM orders")
        backend.execute(
            "INSERT INTO orders (id, amount) SELECT i, (i % 7) + 1 FROM range(1, ?) AS ids(i)",
            [count + 1],
        )
    checkpoint = fixture.sources.observe([REVENUE, MEAN]).execute()
    assert checkpoint.state.realized_row_count == count
    original = fixture.runtime.store.artifact(checkpoint.state.artifact_ref.ref)
    assert original is not None and len(original.descriptor.retained_parts) == 2
    receipts = (
        original.descriptor.storage_receipt,
        *(part.storage_receipt for part in original.descriptor.retained_parts),
    )
    assert all(
        isinstance(receipt, LocalReceipt) and receipt.realized_row_count == count
        for receipt in receipts
    )
    paths = []
    for receipt in receipts:
        assert isinstance(receipt, LocalReceipt)
        path = tmp_path / receipt.project_relative_path / "data.parquet"
        assert path.stat().st_size > 0
        paths.append(path)
    assert len(set(paths)) == 3
    fixture.database.rename(tmp_path / "warehouse.offline")

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("retained local continuation attempted source work")

    for name in ("_build_backend_from_effective", "_effective_kwargs", "compile_dataset"):
        monkeypatch.setattr(admission, name, forbidden)
    with pandas_methods("metric.where"):
        selected = checkpoint.where(gt(REVENUE, 4))
        logical = selected.aggregate()
        result = logical.execute()
        selected_values = [
            (identity % 7) + 1 for identity in range(1, count + 1) if (identity % 7) + 1 > 4
        ]
        frame = result.to_pandas()
        assert frame["revenue"].tolist() == [sum(selected_values)]
        assert frame["mean_amount"].tolist() == pytest.approx(
            [sum(selected_values) / len(selected_values)]
        )
        assert fixture.runtime.statistics.primary_queries == 0
        assert fixture.runtime.statistics.events.get("profile_resolution", 0) == 0
        assert fixture.runtime.statistics.events.get("credential_resolution", 0) == 0
        assert fixture.runtime.statistics.events.get("local_execution_started", 0) > 0
        assert len(fixture.runtime.statistics.local_handoffs) == 2
        assert (
            fixture.runtime.statistics.local_handoffs[0][1]
            == fixture.runtime.statistics.local_handoffs[1][0]
        )
        output = fixture.runtime.store.artifact(result.state.artifact_ref.ref)
        assert output is not None and isinstance(output.descriptor.storage_receipt, LocalReceipt)
        assert all(
            part.storage_receipt.realized_row_count == 1
            for part in output.descriptor.retained_parts
        )
        assert fixture.runtime.store.artifact(checkpoint.state.artifact_ref.ref) == original
        assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()
        before = snapshot(fixture.runtime)
        assert logical.execute().state.artifact_ref == result.state.artifact_ref
        assert snapshot(fixture.runtime) == before
        assert fixture.runtime.statistics.events.get("local_execution_started", 0) == 0
        assert fixture.runtime.statistics.events == {"reconciliation": 1}
        reopened = DatasetRuntime.open(tmp_path, fixture.runtime.session_ref)
        recovered = reopened.artifact(result.state.artifact_ref)
        assert isinstance(recovered, MaterializedMetricDataset)
        assert recovered.to_pandas().equals(frame)
        assert snapshot(reopened) == before
