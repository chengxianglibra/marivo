"""Event matching consumes the current selected subject identity relation."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from unittest.mock import patch

import duckdb
import pytest

from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.observation.metric import MaterializedMetricDataset, PopulationInput
from marivo.analysis.observation.population import MaterializedPopulationDataset
from marivo.analysis.observation.predicates import eq, gt
from marivo.analysis.observation.sampling import engine_sample
from marivo.analysis.operators.candidate_dataset import MaterializedCandidateDataset
from marivo.refs import ref
from tests.lazy_adapter_runtime_worker import forbidden
from tests.lazy_event_runtime_fixtures import journey, setup_event

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize("kind", ["population", "metric", "candidate"])
@pytest.mark.parametrize("retained", [False, True])
def test_selected_identity_authority_drives_events_without_origin_replay(
    tmp_path: Path, kind: Literal["population", "metric", "candidate"], retained: bool
) -> None:
    runtime, sources, database = setup_event(tmp_path, engine=True)
    selected: PopulationInput
    if kind == "population":
        selected = sources.population(ref.entity("sales.customers")).where(
            eq(ref.dimension("sales.customers.region"), "EU")
        )
        expected = [(1,), (1,)]
    else:
        base = sources.observe(
            ref.metric("sales.revenue"),
            population=sources.population(ref.entity("sales.customers")),
        )
        if kind == "metric":
            selected = base.where(gt(base.fields.metric(ref.metric("sales.revenue")), 50))
        else:
            candidate = base.discover.entity_outliers(threshold=1.0)
            selected = candidate.rank(candidate.fields.get("score")).limit(1)
        expected = [(2,), (2,)]
    if retained:
        selected = selected.execute()
        if kind != "population":
            with duckdb.connect(str(database), config={"threads": 1}) as connection:
                connection.execute("DROP TABLE orders")
        cold = DatasetRuntime.open(tmp_path, runtime.session_ref, target=runtime.target)
        recovered = cold.artifact(selected.state.artifact_ref)
        assert isinstance(
            recovered,
            (
                MaterializedPopulationDataset,
                MaterializedMetricDataset,
                MaterializedCandidateDataset,
            ),
        )
        selected = recovered
        runtime = cold
        sources = runtime.sources(
            semantic_registry=sources._owner.semantic_registry, sidecar=sources._owner.sidecar
        )
    with (
        patch.object(admission, "supervise", forbidden),
        patch("marivo.analysis.materialization.reads.payload_batches", forbidden),
    ):
        result = journey(sources, population=selected).execute()
    assert result.to_pandas().entity_identity.tolist() == expected
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    assert (
        record.descriptor.population_authority.definition_fingerprint
        == selected.definition_fingerprint
    )
    assert record.evidence.finding_count == 0
    assert runtime.statistics.transferred_rows == 0
    assert runtime.statistics.local_handoffs == ()
    if retained and kind != "population":
        assert all('"orders"' not in sql for _, sql in runtime.statistics.statements)


def test_empty_and_sampled_identity_keep_their_current_membership(tmp_path: Path) -> None:
    runtime, sources, _ = setup_event(tmp_path, engine=True)
    population = sources.population(ref.entity("sales.customers"))
    empty = population.where(eq(ref.dimension("sales.customers.region"), "missing"))
    assert journey(sources, population=empty).execute().to_pandas().empty
    sampled = population.sample(engine_sample(target_rows=4, seed=42))
    result = journey(sources, population=sampled).execute()
    assert result.to_pandas().entity_identity.tolist() == [(1,), (1,), (2,), (2,)]
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and record.descriptor.sampling_execution is not None
    assert record.descriptor.sampling_execution[0].realized_entity_count == 4
    assert {part.role for part in record.descriptor.retained_parts} == {"population_sampling_state"}
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref, target=runtime.target)
    assert cold.artifact(result.state.artifact_ref).to_pandas().equals(result.to_pandas())
