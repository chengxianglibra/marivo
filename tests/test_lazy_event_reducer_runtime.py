"""Native logical and source-offline Event reducer acceptance."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest

from marivo.analysis.domains.contracts import EventJourneySemantics
from marivo.analysis.domains.event import MaterializedEventDataset
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.observation.population import MaterializedPopulationDataset
from marivo.analysis.observation.predicates import eq, is_null
from marivo.analysis.subject import dropped_before
from marivo.refs import ref
from tests.lazy_adapter_runtime_worker import forbidden, snapshot
from tests.lazy_event_runtime_fixtures import journey, setup_event

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize("membership", ("logical", "retained", "missing"))
def test_selected_population_metric_enriches_current_subject_dimensions(
    tmp_path: Path, membership: str
) -> None:
    runtime, sources, database = setup_event(tmp_path, engine=True)
    input = journey(sources)
    semantics = input.row_contract.family_semantics
    assert isinstance(semantics, EventJourneySemantics)
    selected = input.select_subjects(dropped_before(step=semantics.pattern.steps[-1]))
    population = selected.execute() if membership in ("retained", "missing") else selected
    if membership in ("retained", "missing"):
        with duckdb.connect(str(database), config={"threads": 1}) as connection:
            connection.execute("DROP TABLE started_rows")
            connection.execute("DROP TABLE finished_rows")
            if membership == "missing":
                connection.execute("DELETE FROM customers WHERE id = 2")
    enriched = sources.observe(ref.metric("sales.revenue"), population=population).with_dimensions(
        ref.dimension("sales.customers.region")
    )
    if membership == "missing":
        before = snapshot(runtime)
        with pytest.raises(MaterializationError):
            enriched.execute()
        after = snapshot(runtime)
        assert after["dataset_artifacts"] == before["dataset_artifacts"]
        assert after["dataset_evidence"] == before["dataset_evidence"]
        assert runtime.store.resources(runtime.session_ref) == ()
        return
    result = enriched.execute()
    frame = result.to_pandas()
    assert frame.entity_identity.tolist() == [(2,)]
    assert frame.region.isna().all()
    assert frame.revenue.tolist() == [100.0]
    assert runtime.statistics.transferred_rows == 1


@pytest.mark.parametrize("retained", [False, True])
def test_reducers_and_selection_consume_exact_journey(tmp_path: Path, retained: bool) -> None:
    runtime, sources, database = setup_event(tmp_path, engine=True)
    logical = journey(sources)
    meaning = logical.row_contract.family_semantics
    assert isinstance(meaning, EventJourneySemantics)
    first, last = meaning.pattern.steps
    receiver = logical
    if retained:
        materialized = logical.execute()
        with duckdb.connect(str(database), config={"threads": 1}) as connection:
            connection.execute("DROP TABLE started_rows")
            connection.execute("DROP TABLE finished_rows")
        cold = DatasetRuntime.open(tmp_path, runtime.session_ref, target=runtime.target)
        recovered = cold.artifact(materialized.state.artifact_ref)
        assert isinstance(recovered, MaterializedEventDataset)
        runtime = cold
        funnel = recovered.funnel()
        durations = recovered.time_to_event(from_step=first, to_step=last)
        selected = recovered.select_subjects(dropped_before(step=last))
    else:
        funnel = receiver.funnel()
        durations = receiver.time_to_event(from_step=first, to_step=last)
        selected = receiver.select_subjects(dropped_before(step=last))
    with patch("marivo.analysis.materialization.admission.execute_local", forbidden):
        funnel_result = funnel.execute()
        duration_result = durations.execute()
        selection_result = selected.execute()
    cells = funnel_result.to_pandas()
    assert cells.step_key.tolist() == ["start", "finish"]
    assert cells.reached_count.tolist() == [2, 1]
    assert cells.lost_count.tolist() == [0, 1]
    attempts = duration_result.to_pandas()
    assert attempts.completion_status.tolist() == ["complete", "incomplete"]
    assert selection_result.to_pandas().entity_identity.tolist() == [(2,)]
    before = snapshot(runtime)
    assert funnel.execute().state.artifact_ref == funnel_result.state.artifact_ref
    assert snapshot(runtime) == before
    assert runtime.statistics.transferred_rows == 0


def test_selection_metric_loop_and_uncertainty_barrier(tmp_path: Path) -> None:
    runtime, sources, _ = setup_event(tmp_path, engine=True)
    metric = sources.observe(
        ref.metric("sales.revenue"), population=sources.population(ref.entity("sales.customers"))
    )
    logical = journey(sources, population=metric)
    meaning = logical.row_contract.family_semantics
    assert isinstance(meaning, EventJourneySemantics)
    selected = logical.select_subjects(dropped_before(step=meaning.pattern.steps[-1]))
    output = sources.observe(ref.metric("sales.revenue"), population=selected).execute()
    assert output.to_pandas().entity_identity.tolist() == [(2,)]
    uncertain = journey(sources, complete=False).select_subjects(
        dropped_before(step=meaning.pattern.steps[-1])
    )
    before = snapshot(runtime)
    with pytest.raises(MaterializationError):
        uncertain.where(eq(ref.dimension("sales.customers.region"), "absent")).execute()
    after = snapshot(runtime)
    assert after["dataset_artifacts"] == before["dataset_artifacts"]
    assert after["dataset_evidence"] == before["dataset_evidence"]
    assert runtime.store.resources(runtime.session_ref) == ()


def test_recovered_selection_current_dimension_filter(tmp_path: Path) -> None:
    runtime, sources, _ = setup_event(tmp_path, engine=True)
    logical = journey(sources)
    meaning = logical.row_contract.family_semantics
    assert isinstance(meaning, EventJourneySemantics)
    selected = logical.select_subjects(dropped_before(step=meaning.pattern.steps[-1])).execute()
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref, target=runtime.target)
    recovered = cold.artifact(selected.state.artifact_ref)
    assert isinstance(recovered, MaterializedPopulationDataset)
    cold.sources(semantic_registry=sources._owner.semantic_registry, sidecar=sources._owner.sidecar)
    result = recovered.where(is_null(ref.dimension("sales.customers.region"))).execute()
    assert result.to_pandas().entity_identity.tolist() == [(2,)]


def test_nested_event_selection_keeps_each_inputs_coverage_authority(tmp_path: Path) -> None:
    runtime, sources, _ = setup_event(tmp_path, engine=True)
    first_journey = journey(sources)
    meaning = first_journey.row_contract.family_semantics
    assert isinstance(meaning, EventJourneySemantics)
    selected = first_journey.select_subjects(dropped_before(step=meaning.pattern.steps[-1]))
    second_journey = journey(sources, population=selected, complete=False)
    cells = second_journey.funnel().execute().to_pandas()
    assert cells.cohort_count.tolist() == [1, 1]
    assert cells.resolved_cohort_count.tolist() == [1, 0]
    assert cells.coverage_censored_count.tolist() == [0, 1]
    second_selection = second_journey.select_subjects(
        dropped_before(step=meaning.pattern.steps[-1])
    )
    before = snapshot(runtime)
    with pytest.raises(MaterializationError):
        sources.observe(ref.metric("sales.revenue"), population=second_selection).execute()
    after = snapshot(runtime)
    assert after["dataset_artifacts"] == before["dataset_artifacts"]
    assert after["dataset_evidence"] == before["dataset_evidence"]
    assert runtime.store.resources(runtime.session_ref) == ()
