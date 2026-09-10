"""Native and guarded local driver execution preserve exact input authority."""

from __future__ import annotations

from pathlib import Path

import ibis
import pytest

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.targets import LocalTarget
from marivo.analysis.observation.predicates import eq, gt
from marivo.analysis.observation.sampling import engine_sample
from marivo.analysis.operators.candidate_dataset import MaterializedCandidateDataset
from marivo.analysis.operators.driver_contracts import DriverCandidateEvaluationSummary
from marivo.analysis.operators.errors import CandidateError
from marivo.datasource.backends import BuiltDatasourceBackend, EffectiveDatasourceKwargs
from marivo.datasource.ir import DatasourceIR
from marivo.refs import ref
from tests.lazy_compare_runtime_fixtures import independent_sources
from tests.lazy_driver_runtime_fixtures import CHANNEL, driver_metric, setup_driver
from tests.lazy_execution_fixtures import make_execution_registry
from tests.lazy_materialization_crash_worker import snapshot

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize("states", ("LL", "LM", "ML", "MM"))
def test_driver_all_metric_operand_authorities(tmp_path: Path, states: str) -> None:
    fixture = setup_driver(tmp_path)
    left, right = driver_metric(fixture.sources), driver_metric(fixture.sources, baseline=True)
    current = left.execute() if states[0] == "M" else left
    baseline = right.execute() if states[1] == "M" else right
    logical = current.compare(baseline).discover.driver_axes(search_space=[CHANNEL])
    if states == "MM":
        fixture.database.rename(tmp_path / "source.offline")
    result = logical.execute()
    frame = result.to_pandas()
    assert frame.axis_cardinality.tolist() == [3]
    assert frame.concentration_member_count.tolist() == [1]
    assert frame.concentration_share.tolist() == pytest.approx([0.7])
    assert frame.score.tolist() == pytest.approx([1 / 1.003])
    assert frame.reason_codes.tolist() == [("axis_concentration",)]
    assert result.findings().items == ()
    record = fixture.runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and record.descriptor.candidate_evidence is not None
    assert isinstance(
        record.descriptor.candidate_evidence.evaluation, DriverCandidateEvaluationSummary
    )
    assert record.descriptor.candidate_evidence.evaluation.evaluated_axis_count == 1
    done = snapshot(fixture.runtime)
    assert logical.execute().state.artifact_ref == result.state.artifact_ref
    assert snapshot(fixture.runtime) == done
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()


@pytest.mark.parametrize("retained", (False, True))
def test_scoped_days_survive_driver_and_local_row_continuations(
    tmp_path: Path, retained: bool
) -> None:
    fixture = setup_driver(tmp_path)
    delta = driver_metric(fixture.sources, temporal=True, region=True).compare(
        driver_metric(fixture.sources, baseline=True, temporal=True, region=True)
    )
    value = delta.execute() if retained else delta
    candidates = value.discover.driver_axes(search_space=[CHANNEL]).execute()
    frame = candidates.to_pandas()
    assert len(frame) == 4 and set(frame.comparison_ordinal) == {0, 1}
    assert frame.axis_cardinality.tolist() == [3] * 4
    assert sorted(frame.concentration_share) == pytest.approx([0.5, 0.5, 0.6, 0.6])
    fixture.database.rename(tmp_path / "source.offline")
    cold = DatasetRuntime.open(tmp_path, fixture.runtime.session_ref, target=LocalTarget())
    recovered = cold.artifact(candidates.state.artifact_ref.ref)
    assert isinstance(recovered, MaterializedCandidateDataset)
    selected = recovered.where(eq(recovered.fields.get("comparison_ordinal"), 1))
    result = selected.rank(selected.fields.get("score")).limit(1).execute()
    output = result.to_pandas()
    assert output.comparison_ordinal.tolist() == [1]
    assert output.current_time.iloc[0].month == 2 and output.baseline_time.iloc[0].month == 1
    assert cold.statistics.primary_queries == 0 and cold.statistics.worker_pid is not None
    original = fixture.runtime.store.artifact(candidates.state.artifact_ref.ref)
    changed = cold.store.artifact(result.state.artifact_ref.ref)
    assert original is not None and changed is not None
    assert (
        original.descriptor.candidate_evidence is not None
        and changed.descriptor.candidate_evidence is not None
    )
    assert (
        original.descriptor.candidate_evidence.definition
        == changed.descriptor.candidate_evidence.definition
    )
    assert (
        original.descriptor.candidate_evidence.evaluation
        == changed.descriptor.candidate_evidence.evaluation
    )


def test_missing_axis_expansion_preserves_selected_original_ordinal(tmp_path: Path) -> None:
    fixture = setup_driver(tmp_path)
    delta = driver_metric(fixture.sources, axes=False, temporal=True).compare(
        driver_metric(fixture.sources, baseline=True, axes=False, temporal=True)
    )
    selected = delta.where(eq(delta.fields.get("comparison_ordinal"), 1))
    result = selected.discover.driver_axes(search_space=[CHANNEL]).execute()
    frame = result.to_pandas()
    assert frame.comparison_ordinal.tolist() == [1]
    assert frame.concentration_share.tolist() == pytest.approx([0.7])
    retained = selected.execute()
    before = snapshot(fixture.runtime)
    with pytest.raises(CandidateError, match=r"materializ|retained"):
        retained.discover.driver_axes(search_space=[CHANNEL])
    assert snapshot(fixture.runtime) == before


def test_missing_axis_expansion_shares_and_retains_one_sample(tmp_path: Path) -> None:
    fixture = setup_driver(tmp_path)
    population = fixture.sources.population(ref.entity("sales.orders")).sample(
        engine_sample(target_rows=3, seed=1)
    )
    metric = fixture.sources.observe(
        ref.metric("sales.order_count"), population=population
    ).aggregate()
    result = metric.compare(metric).discover.driver_axes(search_space=[CHANNEL]).execute()
    assert fixture.runtime.statistics.sampling_fences == 1
    assert result.to_pandas().empty and result.findings().items == ()
    assert "sampled_population" in result.contract().render()
    original = fixture.runtime.store.artifact(result.state.artifact_ref.ref)
    assert original is not None and original.descriptor.candidate_evidence is not None
    evaluation = original.descriptor.candidate_evidence.evaluation
    assert isinstance(evaluation, DriverCandidateEvaluationSummary)
    assert evaluation.evaluated_axis_count == evaluation.zero_contribution_axis_count == 1
    assert original.descriptor.sampling_execution is not None
    assert len(original.descriptor.sampling_execution) == 1
    fixture.database.rename(tmp_path / "source.offline")
    selected = (
        result.where(gt(result.fields.get("score"), 0))
        .rank(result.fields.get("score"))
        .limit(1)
        .execute()
    )
    assert selected.to_pandas().empty and "sampled_population" in selected.contract().render()
    retained = fixture.runtime.store.artifact(selected.state.artifact_ref.ref)
    assert retained is not None and retained.descriptor.candidate_evidence is not None
    assert retained.descriptor.sampling_execution == original.descriptor.sampling_execution
    assert retained.descriptor.candidate_evidence.evaluation == evaluation


def test_independent_sources_use_exact_local_driver_frontier(tmp_path: Path) -> None:
    with independent_sources(tmp_path) as (runtime, left, right, calls):
        current = left.with_dimensions(CHANNEL).aggregate()
        baseline = right.with_dimensions(CHANNEL).aggregate()
        result = current.compare(baseline).discover.driver_axes(search_space=[CHANNEL]).execute()
        assert runtime.statistics.worker_pid is not None and len(calls) == 2
        frame = result.to_pandas()
        assert frame.axis_cardinality.tolist() == [1]
        assert frame.concentration_share.tolist() == [1.0]
        assert frame.score.tolist() == pytest.approx([1 / 1.001])
        assert runtime.store.resources(runtime.session_ref) == ()


def test_independent_source_expansion_preserves_original_time_ordinals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = setup_driver(tmp_path)
    second_database = tmp_path / "second.duckdb"
    second_database.write_bytes(fixture.database.read_bytes())
    first_registry, first_sidecar = make_execution_registry(Path(":memory:"))
    second_registry, second_sidecar = make_execution_registry(Path(":memory:"))
    first = fixture.runtime.sources(semantic_registry=first_registry, sidecar=first_sidecar)
    second = fixture.runtime.sources(semantic_registry=second_registry, sidecar=second_sidecar)

    def supplied(
        datasource: DatasourceIR, effective: EffectiveDatasourceKwargs, *, read_only: bool
    ) -> BuiltDatasourceBackend:
        assert effective.kwargs == {"path": ":memory:"} and read_only
        path = (
            fixture.database
            if datasource is first_registry.datasources["warehouse"]
            else second_database
        )
        assert (
            datasource is first_registry.datasources["warehouse"]
            or datasource is second_registry.datasources["warehouse"]
        )
        return BuiltDatasourceBackend(ibis.duckdb.connect(str(path), read_only=True), ())

    monkeypatch.setattr(admission, "_build_backend_from_effective", supplied)
    delta = driver_metric(first, axes=False, temporal=True).compare(
        driver_metric(second, baseline=True, axes=False, temporal=True)
    )
    selected = delta.where(eq(delta.fields.get("comparison_ordinal"), 1))
    result = selected.discover.driver_axes(search_space=[CHANNEL]).execute()
    frame = result.to_pandas()
    assert frame.comparison_ordinal.tolist() == [1]
    assert frame.concentration_share.tolist() == pytest.approx([0.7])
    assert frame.current_time.iloc[0].month == 2 and frame.baseline_time.iloc[0].month == 1
    assert fixture.runtime.statistics.worker_pid is not None
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()


@pytest.mark.parametrize("kind", ("local", "engine"))
def test_entity_driver_scope_privacy_and_retained_permission(tmp_path: Path, kind: str) -> None:
    fixture = setup_driver(tmp_path, "engine" if kind == "engine" else "local")
    delta = driver_metric(fixture.sources, entity=True, axes=False).compare(
        driver_metric(fixture.sources, baseline=True, entity=True, axes=False)
    )
    result = delta.discover.driver_axes(search_space=[CHANNEL]).execute()
    assert result.to_pandas().axis_cardinality.tolist() == [3, 3]
    fixture.database.rename(tmp_path / "source.offline")
    selected = result.where(gt(result.fields.get("score"), 0))
    if kind == "local":
        before = snapshot(fixture.runtime)
        with pytest.raises(DatasetCompilationError, match="source-required"):
            selected.execute()
        assert snapshot(fixture.runtime) == before
    else:
        continued = selected.execute()
        assert len(continued.to_pandas()) == 2
        assert fixture.runtime.statistics.worker_pid is None
