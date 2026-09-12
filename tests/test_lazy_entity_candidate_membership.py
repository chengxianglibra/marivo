"""Exact Candidate membership admission and source-side identity continuation."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest

from marivo._temporal import time_scope
from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.datasets.errors import DatasetConstructionError, DatasetOwnershipError
from marivo.analysis.datasets.handles import LogicalRootHandle, MaterializedScanLeafHandle
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.targets import LocalTarget
from marivo.analysis.observation.contracts import metric_definition
from marivo.analysis.observation.predicates import gt
from marivo.analysis.observation.sampling import engine_sample
from marivo.analysis.operators.candidate_contracts import CandidateObjective
from marivo.analysis.operators.candidate_dataset import MaterializedCandidateDataset
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.refs import ref
from tests.lazy_candidate_fixtures import candidate_input, discover
from tests.lazy_entity_candidate_fixtures import entity_metric, setup_entity_candidate
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database
from tests.lazy_materialization_crash_worker import snapshot
from tests.lazy_observation_fixtures import make_sources

REVENUE = ref.metric("sales.revenue")
LINE_REVENUE = ref.metric("sales.line_revenue")
JANUARY = time_scope(start="2026-01-01", end="2026-02-01")
FEBRUARY = time_scope(start="2026-02-01", end="2026-03-01")


def test_logical_entity_candidate_admits_exact_selected_authority_without_io() -> None:
    sources = make_sources()
    candidate = entity_metric(sources).discover.entity_outliers()
    filtered = candidate.where(gt(candidate.fields.get("score"), 3.5))
    selected = filtered.rank(filtered.fields.get("score")).limit(1)
    observed = sources.observe(LINE_REVENUE, population=selected)
    assert observed._inputs == (selected,)
    assert metric_definition(observed).population_definition == selected.definition_fingerprint
    assert metric_definition(observed).time_scope is None
    assert str(selected.row_contract.shape_id) == "candidate/entity-outlier@v1"


@pytest.mark.parametrize("objective", ["point_anomalies", "interesting_windows", "period_shifts"])
def test_other_candidate_shapes_never_gain_membership_from_limit(
    objective: CandidateObjective,
) -> None:
    sources = make_sources()
    candidate = discover(candidate_input(sources, objective), objective)
    for selected in (candidate, candidate.limit(1)):
        with pytest.raises(DatasetConstructionError, match="candidate/entity-outlier@v1"):
            sources.observe(REVENUE, population=selected)


@pytest.mark.parametrize("foreign_store", [False, True])
def test_logical_candidate_membership_retains_session_and_store_ownership(
    foreign_store: bool,
) -> None:
    original = make_sources()
    other = make_sources(
        session_id="other-session", store_id="other-store" if foreign_store else "store-observation"
    )
    selected = entity_metric(original).discover.entity_outliers().limit(1)
    with pytest.raises(DatasetOwnershipError, match="foreign input authority"):
        other.observe(REVENUE, population=selected)


def test_candidate_membership_rejects_changed_governed_identity_signature() -> None:
    sources = make_sources()
    candidate = entity_metric(sources).discover.entity_outliers()
    original = sources._owner.semantic_registry
    registry = replace(original, entities=dict(original.entities))
    registry.entities["sales.orders"] = replace(
        registry.entities["sales.orders"], primary_key=("tenant", "id")
    )
    registry.freeze()
    changed = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sources._owner.sidecar,
        action_port=sources._owner.action_port,
        session_id=sources._owner.session_id,
        store_id=sources._owner.store_id,
    )
    with pytest.raises(DatasetConstructionError, match="changed Entity key contract"):
        changed.observe(REVENUE, population=candidate)


@pytest.mark.runtime
def test_logical_candidate_membership_uses_current_selected_rows(tmp_path: Path) -> None:
    runtime, sources, _ = setup_entity_candidate(tmp_path)
    candidate = entity_metric(sources).discover.entity_outliers()
    filtered = candidate.where(gt(candidate.fields.get("score"), 3.5))
    selected = filtered.rank(filtered.fields.get("score")).limit(1)
    result = sources.observe(LINE_REVENUE, population=selected).execute()
    frame = result.to_pandas()
    assert frame.entity_identity.tolist() == [(4,)]
    assert frame.line_revenue.tolist() == [40.0]
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    assert (
        record.descriptor.population_authority.definition_fingerprint
        == selected.definition_fingerprint
    )
    assert record.descriptor.candidate_evidence is None
    assert runtime.statistics.local_handoffs == ()
    assert runtime.store.resources(runtime.session_ref) == ()


@pytest.mark.runtime
def test_empty_candidate_membership_does_not_reintroduce_unselected_entities(
    tmp_path: Path,
) -> None:
    runtime, sources, _ = setup_entity_candidate(tmp_path)
    candidate = entity_metric(sources).discover.entity_outliers(threshold=100.0)
    result = sources.observe(LINE_REVENUE, population=candidate).execute()
    assert result.to_pandas().empty
    assert result.evidence_digest.finding_count == 0
    assert runtime.store.resources(runtime.session_ref) == ()


@pytest.mark.runtime
def test_candidate_selection_time_does_not_bind_downstream_observation_time(tmp_path: Path) -> None:
    runtime, sources, database = setup_entity_candidate(tmp_path)
    with duckdb.connect(str(database)) as connection:
        connection.execute("UPDATE orders SET day = DATE '2026-01-02'")
        connection.execute(
            "INSERT INTO orders (id, customer_id, amount, day) VALUES "
            "(11,1,100,DATE '2026-02-02'),(12,2,200,DATE '2026-02-02'),"
            "(13,3,300,DATE '2026-02-02'),(14,4,400,DATE '2026-02-02')"
        )
    population = sources.population(ref.entity("sales.customers"))
    selected = sources.observe(
        ref.metric("sales.mean_amount"), population=population, time_scope=JANUARY
    ).discover.entity_outliers()
    february = sources.observe(REVENUE, population=selected, time_scope=FEBRUARY).aggregate()
    unscoped = sources.observe(REVENUE, population=selected).aggregate()
    assert metric_definition(february).time_scope == FEBRUARY
    assert metric_definition(unscoped).time_scope is None
    assert february.definition_fingerprint != unscoped.definition_fingerprint
    assert february.execute().to_pandas().revenue.tolist() == [400.0]
    assert unscoped.execute().to_pandas().revenue.tolist() == [410.0]
    assert runtime.store.resources(runtime.session_ref) == ()


@pytest.mark.runtime
def test_engine_candidate_membership_reads_checkpoint_after_selection_source_drop(
    tmp_path: Path,
) -> None:
    runtime, sources, database = setup_entity_candidate(tmp_path)
    runtime.target = LocalTarget()
    checkpoint = entity_metric(sources).discover.entity_outliers().execute()
    with duckdb.connect(str(database)) as connection:
        connection.execute("DROP TABLE orders")
    start = len(runtime.statistics.statements)
    selected = checkpoint.where(gt(checkpoint.fields.get("score"), 3.5)).limit(1)
    observed = sources.observe(LINE_REVENUE, population=selected).aggregate()
    result = observed.execute()
    assert result.to_pandas().line_revenue.tolist() == [40.0]
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    assert (
        record.descriptor.population_authority.definition_fingerprint
        == selected.definition_fingerprint
    )
    assert record.descriptor.candidate_evidence is None
    assert all('"orders"' not in sql for _, sql in runtime.statistics.statements[start:])
    assert runtime.store.resources(runtime.session_ref) == ()
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref, target=runtime.target)
    retained = cold.artifact(checkpoint.state.artifact_ref)
    assert isinstance(retained, MaterializedCandidateDataset)
    cold_sources = cold.sources(
        semantic_registry=sources._owner.semantic_registry, sidecar=sources._owner.sidecar
    )
    cold_selected = retained.where(gt(retained.fields.get("score"), 3.5)).limit(1)
    with patch.object(admission, "place", side_effect=AssertionError("cold binding placed input")):
        assert (
            cold_sources.observe(LINE_REVENUE, population=cold_selected)
            .aggregate()
            .execute()
            .state.artifact_ref
            == result.state.artifact_ref
        )
    assert not cold.statistics.statements and cold.statistics.worker_pid is None


@pytest.mark.runtime
@pytest.mark.parametrize("kind", ["local", "foreign_engine"])
def test_candidate_identity_requires_registered_adapter_version_before_run(
    tmp_path: Path, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, sources, _ = setup_entity_candidate(tmp_path)
    if kind == "foreign_engine":
        runtime.target = LocalTarget()
    checkpoint = entity_metric(sources).discover.entity_outliers().execute()
    runtime.target = LocalTarget()
    if kind == "foreign_engine":
        foreign = tmp_path / "foreign.duckdb"
        seed_execution_database(foreign)
        registry, sidecar = make_execution_registry(foreign)
        sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    monkeypatch.setattr(duckdb, "__version__", "unsupported")
    observed = sources.observe(LINE_REVENUE, population=checkpoint)
    assert isinstance(observed._root, LogicalRootHandle)
    assert isinstance(observed._root.inputs[0].root, MaterializedScanLeafHandle)
    before = snapshot(runtime)
    with pytest.raises(DatasetCompilationError, match="source-required"):
        observed.execute()
    assert snapshot(runtime) == before
    assert runtime.store.resources(runtime.session_ref) == ()


@pytest.mark.runtime
def test_sampled_candidate_preserves_realization_through_selection_and_membership(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime, sources, _ = setup_entity_candidate(tmp_path)
    runtime.target = LocalTarget()
    population = sources.population(ref.entity("sales.orders")).sample(
        engine_sample(target_rows=100, seed=19)
    )
    candidate = sources.observe(REVENUE, population=population).discover.entity_outliers().execute()
    original = runtime.store.artifact(candidate.state.artifact_ref.ref)
    assert original is not None and original.descriptor.candidate_evidence is not None
    receipts = original.descriptor.sampling_execution
    assert receipts is not None and len(receipts) == 1
    assert "sampled_population" in candidate.contract().render()
    selected = candidate.where(gt(candidate.fields.get("score"), 3.5)).execute()
    selection_record = runtime.store.artifact(selected.state.artifact_ref.ref)
    assert (
        selection_record is not None and selection_record.descriptor.candidate_evidence is not None
    )
    assert selection_record.descriptor.sampling_execution == receipts
    assert (
        selection_record.descriptor.candidate_evidence.evaluation
        == original.descriptor.candidate_evidence.evaluation
    )
    result = sources.observe(LINE_REVENUE, population=selected).aggregate().execute()
    assert result.to_pandas().line_revenue.tolist() == [40.0]
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    assert record.descriptor.sampling_execution == receipts
    assert record.descriptor.candidate_evidence is None
    assert (
        record.descriptor.population_authority.definition_fingerprint
        == selected.definition_fingerprint
    )
    result.show()
    assert "Sampling: approximate Entity sample; realizations=1" in capsys.readouterr().out
    assert runtime.store.resources(runtime.session_ref) == ()
