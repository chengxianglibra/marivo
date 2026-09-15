"""Real guarded source-to-worker distribution execution and retained cold authority."""

from pathlib import Path

import pytest

from marivo.analysis import time_scope
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.targets import LocalTarget
from marivo.analysis.operators.attribution_contracts import AttributionSemantics
from marivo.analysis.operators.delta import LogicalDeltaDataset, MaterializedDeltaDataset
from marivo.semantic._quantile import QuantileMethod, quantile_metric
from tests.lazy_distribution_fixtures import (
    CHANNEL,
    METRIC,
    make_distribution_registry,
    seed_distribution_database,
)

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize("method", ["linear_interpolation@v1", "duckdb_tdigest@v1"])
@pytest.mark.parametrize("retained", [False, True])
def test_distribution_runtime_preserves_method_authority(
    tmp_path: Path, method: QuantileMethod, retained: bool
) -> None:
    database = tmp_path / "warehouse.duckdb"
    seed_distribution_database(database)
    registry, sidecar = make_distribution_registry(database)
    runtime = DatasetRuntime.create(tmp_path, "distribution", target=LocalTarget())
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    current = (
        sources.observe(
            quantile_metric(METRIC, method=method),
            time_scope=time_scope(start="2026-02-01", end="2026-02-05"),
        )
        .with_dimensions(CHANNEL)
        .aggregate()
    )
    baseline = (
        sources.observe(
            quantile_metric(METRIC, method=method),
            time_scope=time_scope(start="2026-01-01", end="2026-01-05"),
        )
        .with_dimensions(CHANNEL)
        .aggregate()
    )
    delta: LogicalDeltaDataset | MaterializedDeltaDataset = current.compare(baseline)
    if retained:
        assert isinstance(delta, LogicalDeltaDataset)
        delta = delta.execute()
        database.rename(tmp_path / "source.offline")
        runtime.target = LocalTarget()
    drivers = delta.attribute(axes=(CHANNEL,))
    result = drivers.execute()
    frame = result.to_pandas()
    assert frame.contribution.sum() == pytest.approx(1.0)
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and record.descriptor.attribution_evidence is not None
    assert record.descriptor.attribution_evidence.approximate is (method == "duckdb_tdigest@v1")
    assert isinstance(record.descriptor.row_contract.family_semantics, AttributionSemantics)
    assert record.descriptor.row_contract.family_semantics.approximation_class == (
        "semantic_percentile" if method == "duckdb_tdigest@v1" else "exact"
    )
    from dataclasses import replace

    from marivo.analysis.materialization.attribution_publication import validate_descriptor
    from marivo.analysis.materialization.errors import IntegrityError
    from marivo.analysis.observation.fold_contracts import decode_fold_authority

    pair = record.descriptor.attribution_fold_authority
    assert pair is not None
    authority = decode_fold_authority(pair[1])
    metric_authority = authority.metrics[0]
    distribution = metric_authority.distribution
    assert distribution is not None
    changed_method = (
        "linear_interpolation@v1" if method == "duckdb_tdigest@v1" else "duckdb_tdigest@v1"
    )
    changed_distribution = distribution.model_copy(
        update={"quantile": distribution.quantile.model_copy(update={"method": changed_method})}
    )
    changed_authority = authority.model_copy(
        update={
            "metrics": (metric_authority.model_copy(update={"distribution": changed_distribution}),)
        }
    )
    with pytest.raises(IntegrityError, match="identical distribution methods"):
        validate_descriptor(
            replace(
                record.descriptor, attribution_fold_authority=(pair[0], changed_authority.to_json())
            )
        )


@pytest.mark.parametrize("topology", ["LL", "LM", "ML", "MM"])
def test_metric_operand_orders_and_no_raw_distribution_transfer(
    tmp_path: Path, topology: str
) -> None:
    from tests.lazy_distribution_fixtures import guard_distribution_transport

    database = tmp_path / "warehouse.duckdb"
    seed_distribution_database(database)
    registry, sidecar = make_distribution_registry(database, q=0.7)
    runtime = DatasetRuntime.create(tmp_path, "operands", target=LocalTarget())
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
    left = current.execute() if topology[0] == "M" else current
    right = baseline.execute() if topology[1] == "M" else baseline
    runtime.target = LocalTarget()
    if topology == "MM":
        database.rename(tmp_path / "source.offline")
    with guard_distribution_transport():
        result = left.compare(right).attribute(axes=(CHANNEL,)).execute()
    assert result.to_pandas().contribution.sum() == pytest.approx(-1.0)


@pytest.mark.parametrize("damage", ["missing", "mutated"])
def test_retained_distribution_corruption_blocks_consumption_but_not_primary_reads(
    tmp_path: Path, damage: str
) -> None:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    from marivo.analysis.materialization.contracts import LocalReceipt
    from marivo.analysis.materialization.errors import MaterializationError

    database = tmp_path / "warehouse.duckdb"
    seed_distribution_database(database)
    registry, sidecar = make_distribution_registry(database)
    runtime = DatasetRuntime.create(tmp_path, "corruption", target=LocalTarget())
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    metric = sources.observe(METRIC).with_dimensions(CHANNEL).aggregate()
    delta = metric.compare(metric).execute()
    record = runtime.store.artifact(delta.state.artifact_ref.ref)
    assert record is not None
    part = next(
        part
        for part in record.descriptor.retained_parts
        if part.role == "delta_distribution.baseline"
    )
    assert isinstance(part.storage_receipt, LocalReceipt)
    path = tmp_path / part.storage_receipt.project_relative_path / "data.parquet"
    database.rename(tmp_path / "source.offline")
    frame = delta.to_pandas()
    assert runtime.revalidate(delta.state.artifact_ref).artifact_integrity == "valid"
    if damage == "missing":
        path.unlink()
    else:
        path.chmod(0o600)
        table = pq.read_table(path)
        name = "__mv_distribution_frequency"
        changed = table.set_column(
            table.schema.get_field_index(name), name, pc.add(table[name], pa.scalar(1))
        )
        pq.write_table(changed, path)
    assert delta.to_pandas().equals(frame)
    runtime.target = LocalTarget()
    with pytest.raises(MaterializationError):
        delta.attribute(axes=(CHANNEL,)).execute()
    assert len(runtime.graph().artifacts) == 1


@pytest.mark.parametrize("top_k", [None, 8, 7])
def test_complete_players_and_other_survive_mapping_above_former_cap(
    tmp_path: Path, top_k: int | None
) -> None:
    import duckdb

    database = tmp_path / "warehouse.duckdb"
    seed_distribution_database(database)
    with duckdb.connect(str(database), config={"threads": 1}) as con:
        con.execute("delete from orders")
        con.executemany(
            "insert into orders (id, customer_id, channel, amount, day) values (?,1,?,?,?)",
            [
                (month * 100 + i, str(i), float(i + month), f"2026-0{month}-02")
                for month in (1, 2)
                for i in range(9)
            ],
        )
    registry, sidecar = make_distribution_registry(database)
    runtime = DatasetRuntime.create(tmp_path, "players", target=LocalTarget())
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
    logical = current.compare(baseline).attribute(axes=(CHANNEL,), top_k=top_k)
    output = logical.execute().to_pandas()
    assert len(output) == (9 if top_k is None else top_k + 1)
    assert output.contribution.sum() == pytest.approx(1.0)
    assert sum(tuple(mask) == (True,) for mask in output.other_mask) == (0 if top_k is None else 1)


@pytest.mark.parametrize("method", ["linear_interpolation@v1", "duckdb_tdigest@v1"])
def test_shared_sampling_preserves_method_and_one_realization(
    tmp_path: Path, method: QuantileMethod
) -> None:
    from marivo.analysis.observation.sampling import engine_sample
    from marivo.refs import ref
    from tests.lazy_distribution_fixtures import guard_distribution_transport

    database = tmp_path / "warehouse.duckdb"
    seed_distribution_database(database)
    registry, sidecar = make_distribution_registry(database)
    runtime = DatasetRuntime.create(tmp_path, "sampling", target=LocalTarget())
    source = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    population = source.population(ref.entity("sales.orders")).sample(
        engine_sample(target_rows=7, seed=11)
    )
    metric = (
        source.observe(quantile_metric(METRIC, method=method), population=population)
        .with_dimensions(CHANNEL)
        .aggregate()
    )
    with guard_distribution_transport():
        result = metric.compare(metric).attribute(axes=(CHANNEL,)).execute()
    assert result.to_pandas().contribution.sum() == pytest.approx(0.0)
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and record.descriptor.sampling_execution is not None
    assert len(record.descriptor.sampling_execution) == 1
    assert isinstance(record.descriptor.row_contract.family_semantics, AttributionSemantics)
    assert record.descriptor.row_contract.family_semantics.approximation_class == (
        "sampled_semantic_percentile" if method == "duckdb_tdigest@v1" else "sampled_population"
    )
    assert any(
        name.startswith("attribution.distribution.coalitions")
        for name, _ in record.descriptor.population_authority.validation_results
    )


@pytest.mark.parametrize("source_type", ["decimal", "float32"])
@pytest.mark.parametrize("method", ["linear_interpolation@v1", "duckdb_tdigest@v1"])
def test_numeric_source_types_replay_the_declared_float64_percentile(
    tmp_path: Path, source_type: str, method: QuantileMethod
) -> None:
    from dataclasses import replace

    import duckdb

    from marivo.semantic.ir import TableSourceIR

    database = tmp_path / "warehouse.duckdb"
    seed_distribution_database(database)
    with duckdb.connect(str(database), config={"threads": 1}) as con:
        con.execute("delete from orders")
        con.execute(
            "alter table orders alter column amount type "
            + ("decimal(18,2)" if source_type == "decimal" else "float")
        )
        con.executemany(
            "insert into orders (id,customer_id,channel,amount,day) values (?,1,'web',?,?)",
            [
                (1, 1.00, "2026-01-02"),
                (2, 1.01, "2026-01-02"),
                (3, 1.01, "2026-02-02"),
                (4, 1.02, "2026-02-02"),
            ],
        )
        expected = con.execute(
            "select quantile_cont(amount::double,0.5) from orders where day >= '2026-02-01'"
        ).fetchone()
        assert expected is not None
        expected_current = float(expected[0])
    registry, sidecar = make_distribution_registry(database)
    entity = registry.entities["sales.orders"]
    assert isinstance(entity.source, TableSourceIR)
    entities = dict(registry.entities)
    entities[entity.semantic_id] = replace(
        entity,
        source=replace(
            entity.source,
            columns=tuple(
                (name, replace(binding, data_type=source_type) if name == "amount" else binding)
                for name, binding in entity.source.columns
            ),
        ),
    )
    registry = replace(registry, entities=entities)
    registry.freeze()
    runtime = DatasetRuntime.create(tmp_path, "types", target=LocalTarget())
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    current = (
        sources.observe(
            quantile_metric(METRIC, method=method),
            time_scope=time_scope(start="2026-02-01", end="2026-02-05"),
        )
        .with_dimensions(CHANNEL)
        .aggregate()
    )
    baseline = (
        sources.observe(
            quantile_metric(METRIC, method=method),
            time_scope=time_scope(start="2026-01-01", end="2026-01-05"),
        )
        .with_dimensions(CHANNEL)
        .aggregate()
    )
    retained = current.compare(baseline).execute()
    assert float(retained.to_pandas().current_value.iloc[0]) == pytest.approx(
        expected_current, abs=1e-12
    )
    database.rename(tmp_path / "source.offline")
    runtime.target = LocalTarget()
    result = retained.attribute(axes=(CHANNEL,)).execute()
    assert float(result.to_pandas().current_value.iloc[0]) == pytest.approx(
        expected_current, abs=1e-12
    )


def test_projection_preserves_method_and_preview_preserves_authored_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from dataclasses import replace

    from marivo.analysis.observation.distribution_contracts import distribution_part_authorities
    from marivo.refs import ref

    database = tmp_path / "warehouse.duckdb"
    seed_distribution_database(database)
    registry, sidecar = make_distribution_registry(database)
    second = ref.metric("sales.percentile_revenue")
    metrics = dict(registry.metrics)
    metrics[second.path] = replace(
        metrics[METRIC.path], semantic_id=second.path, aggregation=("percentile", 0.7)
    )
    registry = replace(registry, metrics=metrics)
    registry.freeze()
    runtime = DatasetRuntime.create(tmp_path, "preview", target=LocalTarget())
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    metric = (
        sources.observe((METRIC, quantile_metric(second, method="duckdb_tdigest@v1")))
        .with_dimensions(CHANNEL)
        .aggregate()
    )
    checkpoint = metric.execute()
    checkpoint.show()
    preview = capsys.readouterr().out
    methods = [line for line in preview.splitlines() if line.startswith("Percentile:")]
    assert methods == [
        "Percentile: method=linear_interpolation@v1; q=0.5; exact linear interpolation",
        "Percentile: method=duckdb_tdigest@v1; q=0.7; semantic approximation; error_bound=unknown",
    ]
    database.rename(tmp_path / "source.offline")
    projected = checkpoint.metric(second).execute()
    ((_, authority),) = distribution_part_authorities(projected.row_contract)
    assert authority.metric_ref == second.path and authority.distribution is not None
    assert authority.distribution.quantile.method == "duckdb_tdigest@v1"
    assert authority.distribution.quantile.q == 0.7
    field = next(field for field in projected.schema.columns if field.role_id == "metric")
    source_field = next(
        item for item in checkpoint.schema.columns if item.field_id == field.field_id
    )
    assert (
        projected.to_pandas().sort_values("channel")[field.name].tolist()
        == checkpoint.to_pandas().sort_values("channel")[source_field.name].tolist()
    )
