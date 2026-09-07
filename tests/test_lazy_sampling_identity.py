"""Sampling cannot hide Entity keys outside the registered finite value order."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import duckdb
import pytest

from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.observation.sampling import engine_sample
from marivo.analysis.session._lazy_sources import LazySources
from marivo.datasource.ir import TableSourceIR
from marivo.refs import ref
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database


def _float_identity_source(
    project: Path, entity_name: str, column: str, *, invalid: str | None = None
) -> tuple[DatasetRuntime, LazySources]:
    database = project / "warehouse.duckdb"
    seed_execution_database(database)
    with duckdb.connect(str(database)) as connection:
        if column == "tenant":
            connection.execute(
                "UPDATE composite SET tenant = CASE tenant WHEN 'a' THEN '1' ELSE '2' END"
            )
        connection.execute(f'ALTER TABLE {entity_name} ALTER COLUMN "{column}" TYPE DOUBLE')
        if invalid is not None:
            connection.execute(
                f"UPDATE {entity_name} SET \"{column}\" = '{invalid}'::DOUBLE "
                f"WHERE rowid = (SELECT min(rowid) FROM {entity_name})"
            )
    registry, sidecar = make_execution_registry(database)
    path = f"sales.{entity_name}"
    entity = registry.entities[path]
    assert isinstance(entity.source, TableSourceIR)
    source = replace(
        entity.source,
        columns=tuple(
            (name, replace(binding, data_type="float64") if name == column else binding)
            for name, binding in entity.source.columns
        ),
    )
    registry = replace(
        registry, entities={**registry.entities, path: replace(entity, source=source)}
    )
    registry.freeze()
    runtime = DatasetRuntime.create(project, "finite-identity-sampling")
    return runtime, runtime.sources(semantic_registry=registry, sidecar=sidecar)


@pytest.mark.parametrize(
    "entity,column", [("customers", "id"), ("composite", "id"), ("composite", "tenant")]
)
@pytest.mark.parametrize("invalid", ["NaN", "Infinity", "-Infinity"])
def test_every_nonfinite_identity_component_fails_before_the_sample_is_realized(
    tmp_path: Path, entity: str, column: str, invalid: str
) -> None:
    runtime, sources = _float_identity_source(tmp_path, entity, column, invalid=invalid)
    population = sources.population(ref.entity(f"sales.{entity}"))
    with pytest.raises(MaterializationError, match="identity_finite") as caught:
        population.sample(engine_sample(target_rows=1, seed=0)).execute()
    assert caught.value.stage == "output_validation"
    assert runtime.statistics.sampling_fences == 0
    assert runtime.statistics.primary_queries == 0
    assert runtime.statistics.transferred_rows == 0
    assert runtime.statistics.events.get("sampling_reserved", 0) == 0
    assert runtime.last_run_ref is not None
    run = runtime.store.run(runtime.last_run_ref)
    assert run is not None and run.lifecycle == "failed"
    assert run.output_artifact_ref is None
    assert runtime.store.resources(runtime.session_ref) == ()


@pytest.mark.parametrize(
    "entity,column", [("customers", "id"), ("composite", "id"), ("composite", "tenant")]
)
def test_finite_floating_identity_components_still_admit_native_sampling(
    tmp_path: Path, entity: str, column: str
) -> None:
    runtime, sources = _float_identity_source(tmp_path, entity, column)
    result = (
        sources.population(ref.entity(f"sales.{entity}"))
        .sample(engine_sample(target_rows=2, seed=0))
        .execute()
    )
    assert len(result.to_pandas()) == 2
    assert runtime.statistics.sampling_fences == 1
    assert runtime.statistics.primary_queries == 1
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    assert (
        f"sales.{entity}.identity_finite.{column}",
        0,
    ) in record.descriptor.population_authority.validation_results
