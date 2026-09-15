"""Distribution preparation and publication fail atomically at guarded boundaries."""

from pathlib import Path

import pytest

from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import LocalReceipt
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.targets import LocalTarget
from tests.lazy_distribution_fixtures import (
    CHANNEL,
    METRIC,
    make_distribution_registry,
    seed_distribution_database,
)
from tests.lazy_materialization_crash_worker import snapshot

pytestmark = pytest.mark.runtime


def test_distribution_receipt_mutation_after_output_rename_rolls_back(tmp_path: Path) -> None:
    database = tmp_path / "warehouse.duckdb"
    seed_distribution_database(database)
    registry, sidecar = make_distribution_registry(database)
    runtime = DatasetRuntime.create(tmp_path, "mutation", target=LocalTarget())
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    metric = sources.observe(METRIC).with_dimensions(CHANNEL).aggregate()
    baseline = metric.execute()
    record = runtime.store.artifact(baseline.state.artifact_ref.ref)
    assert record is not None
    receipt = next(
        part.storage_receipt
        for part in record.descriptor.retained_parts
        if part.contract_id == "metric.distribution"
    )
    assert isinstance(receipt, LocalReceipt)
    changed: list[str] = []

    def mutate(point: str) -> None:
        if point == "after_rename":
            path = tmp_path / receipt.project_relative_path / "data.parquet"
            path.chmod(0o600)
            with path.open("ab") as stream:
                stream.write(b"private-distribution-mutation-canary")
            changed.append(point)

    runtime.target = LocalTarget()
    runtime._hook = mutate
    before = snapshot(runtime)
    with pytest.raises(MaterializationError, match="backing size changed"):
        metric.compare(baseline).attribute(axes=(CHANNEL,)).execute()
    assert changed == ["after_rename"]
    after = snapshot(runtime)
    before_tables, after_tables = before["tables"], after["tables"]
    assert isinstance(before_tables, dict) and isinstance(after_tables, dict)
    for field in ("dataset_artifacts", "dataset_evidence", "action_resource_journal"):
        assert before_tables[field] == after_tables[field]
    assert runtime.last_run_ref is not None
    run = runtime.store.run(runtime.last_run_ref)
    assert run is not None and run.lifecycle == "failed" and run.output_artifact_ref is None


@pytest.mark.parametrize("mode", ["one_sided", "undefined_coalition", "null_endpoint"])
def test_one_sided_distribution_and_undefined_games(tmp_path: Path, mode: str) -> None:
    import duckdb

    from marivo.analysis import time_scope

    database = tmp_path / "warehouse.duckdb"
    seed_distribution_database(database)
    with duckdb.connect(str(database), config={"threads": 1}) as con:
        con.execute("delete from orders")
        con.executemany(
            "insert into orders (id,customer_id,channel,amount,day) values (?,1,?,?,?)",
            [
                (1, "web", 1.0, "2026-01-02"),
                (2, "store", 3.0 if mode != "null_endpoint" else None, "2026-02-02"),
            ]
            + ([(3, "web", 2.0, "2026-02-02")] if mode == "one_sided" else []),
        )
    registry, sidecar = make_distribution_registry(database)
    runtime = DatasetRuntime.create(tmp_path, "undefined", target=LocalTarget())
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    current = (
        sources.observe(METRIC, time_scope=time_scope(start="2026-02-01", end="2026-02-05"))
        .with_dimensions(CHANNEL)
        .aggregate()
    )
    baseline = (
        sources.observe(METRIC, time_scope=time_scope(start="2026-01-01", end="2026-01-05"))
        .with_dimensions(CHANNEL)
        .aggregate()
    )
    result = current.compare(baseline).attribute(axes=(CHANNEL,))
    if mode == "one_sided":
        assert result.execute().to_pandas().contribution.sum() == pytest.approx(1.5)
    else:
        with pytest.raises(MaterializationError):
            result.execute()
        assert runtime.graph().artifacts == ()
        assert runtime.store.resources(runtime.session_ref) == ()
