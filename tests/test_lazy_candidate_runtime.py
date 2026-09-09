"""Actual Candidate source/checkpoint execution and origin-free row continuations."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest

from marivo.analysis.datasets.base import Dataset
from marivo.analysis.materialization.targets import EngineTarget, LocalTarget
from marivo.analysis.observation.predicates import eq, gt
from marivo.analysis.operators.candidate_contracts import (
    CandidateEvaluationSummary,
    CandidateObjective,
)
from tests.lazy_candidate_fixtures import candidate_input, discover, setup_candidate

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize("objective", ["point_anomalies", "interesting_windows", "period_shifts"])
@pytest.mark.parametrize("input_kind", ["logical", "local", "engine"])
def test_real_candidate_authorities(
    tmp_path: Path,
    objective: CandidateObjective,
    input_kind: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime, source, database = setup_candidate(tmp_path)
    incoming = candidate_input(source, objective, panel=True)
    value: Dataset = incoming
    if input_kind != "logical":
        runtime.target = EngineTarget("warehouse") if input_kind == "engine" else LocalTarget()
        value = incoming.execute()
        database.rename(tmp_path / "origin.offline")
        runtime.target = LocalTarget()
    logical = discover(value, objective)
    result = logical.execute()
    frame = result.to_pandas()
    assert not frame.empty and frame.score.min() >= 1.0
    assert all(isinstance(value, tuple) and len(value) == 1 for value in frame.reason_codes)
    assert frame.channel.tolist() == ["web"] * len(frame)
    assert result.evidence_digest.finding_count == 0 and result.findings().items == ()
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and record.descriptor.candidate_evidence is not None
    evidence = record.descriptor.candidate_evidence
    assert evidence.definition.input_state_kind == (
        "logical" if input_kind == "logical" else "materialized"
    )
    if objective == "period_shifts":
        assert all(value.month == 2 for value in frame.window_start)
        assert all(value.month == 1 for value in frame.baseline_start)
    result.show()
    shown = capsys.readouterr().out
    assert f"Discovery objective: {objective}" in shown
    assert "scores compare only within this definition" in shown
    assert "filtering and ranking never establish causality or significance" in shown
    assert "Discovery threshold: 1.0" in shown and "Discovery discovery_limit: 50" in shown
    assert "evaluated_series=1/1" in shown
    assert f"discovery_output={len(frame)}; current_rows={len(frame)}" in shown
    assert "CandidateEvaluationSummary(" not in shown
    assert len(shown.encode()) <= 8192
    if database.exists():
        database.rename(tmp_path / "origin.offline")
    selected = result.where(eq(result.fields.get("item_id"), frame.item_id.iloc[0])).execute()
    selected_frame = selected.to_pandas()
    assert selected_frame.item_id.tolist() == [frame.item_id.iloc[0]]
    selected_record = runtime.store.artifact(selected.state.artifact_ref.ref)
    assert selected_record is not None and selected_record.descriptor.candidate_evidence is not None
    assert selected_record.descriptor.candidate_evidence.definition == evidence.definition
    assert selected_record.descriptor.candidate_evidence.evaluation == evidence.evaluation
    assert runtime.statistics.primary_queries == 0
    assert logical.execute().state.artifact_ref == result.state.artifact_ref


@pytest.mark.parametrize("objective", ["point_anomalies", "interesting_windows", "period_shifts"])
def test_direct_local_successors_and_empty_selection(
    tmp_path: Path, objective: CandidateObjective, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime, source, _ = setup_candidate(tmp_path)
    candidates = discover(candidate_input(source, objective), objective, threshold=0.5)
    filtered = candidates.where(gt(candidates.fields.get("score"), 0.75))
    result = filtered.rank(filtered.fields.get("score")).limit(2).execute()
    links = runtime.statistics.local_handoffs
    assert len(links) == 4 and all(a[1] == b[0] for a, b in pairwise(links))
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and record.descriptor.candidate_evidence is not None
    evaluation = record.descriptor.candidate_evidence.evaluation
    empty = result.where(gt(result.fields.get("score"), 100.0)).execute()
    assert empty.to_pandas().empty and empty.evidence_digest.finding_count == 0
    empty_record = runtime.store.artifact(empty.state.artifact_ref.ref)
    assert empty_record is not None and empty_record.descriptor.candidate_evidence is not None
    assert empty_record.descriptor.candidate_evidence.evaluation == evaluation
    assert empty_record.descriptor.candidate_evidence.row_count == 0
    empty.show(max_output_bytes=2048)
    shown = capsys.readouterr().out
    assert "current_rows=0" in shown
    assert f"qualifying={evaluation.pre_limit_candidate_count}" in shown
    assert f"discovery_output={evaluation.emitted_candidate_count}" in shown
    assert "CandidateEvaluationSummary(" not in shown
    assert len(shown.encode()) <= 2048


def test_evaluated_empty_and_sampling_meaning(tmp_path: Path) -> None:
    from marivo._temporal import builtin_grain, time_scope
    from marivo.analysis.observation.sampling import engine_sample
    from marivo.refs import ref

    runtime, source, _ = setup_candidate(tmp_path)
    population = source.population(ref.entity("sales.orders")).sample(
        engine_sample(target_rows=100, seed=1)
    )
    metric = (
        source.observe(
            ref.metric("sales.revenue"),
            population=population,
            time_scope=time_scope(start="2026-02-01", end="2026-02-20"),
        )
        .with_time_axis(ref.time_dimension("sales.orders.order_time"), grain=builtin_grain("day"))
        .aggregate()
    )
    result = metric.discover.point_anomalies(threshold=100.0).execute()
    assert result.to_pandas().empty and result.evidence_digest.finding_count == 0
    assert "sampled_population" in result.contract().render()
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and record.descriptor.candidate_evidence is not None
    evaluation = record.descriptor.candidate_evidence.evaluation
    assert isinstance(evaluation, CandidateEvaluationSummary)
    assert evaluation.evaluated_series_count == 1 and evaluation.pre_limit_candidate_count == 0
    selected = result.limit(1).execute()
    assert selected.to_pandas().empty and "sampled_population" in selected.contract().render()
