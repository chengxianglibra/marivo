"""view obligations on the frozen int64/table/key/grid profiles."""

import json
import os
from pathlib import Path
from typing import Literal, TypeAlias
from unittest.mock import patch

import pandas as pd
import pytest
from ibis.backends import BaseBackend

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._help.render import help as help_api
from marivo.analysis.core.model import DerivedQuantity, RowStatisticQuantity, part_role
from marivo.analysis.core.rules import AssociationFit, DeviationFit, ForecastFit, TimeRuns
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import (
    deviation_execution,
    runs_execution,
    statistical_execution,
)
from marivo.analysis.materialization.graph_protocol import descriptor_plan, receipt_digest
from marivo.analysis.methods.physical import Qualified
from marivo.datasource import adapters
from tests.analysis.statistics.deviation_fixture import prepare_profiles
from tests.shared_fixtures import DslCaseFactory
from tests.support.json import key_json

METHODS = (
    "deviation.zscore@v1",
    "deviation.mad@v1",
    "time.runs@v1",
    "association.pearson@v1",
    "association.spearman@v1",
    "association.kendall@v1",
    "forecast.naive@v1",
    "forecast.drift@v1",
    "forecast.seasonal_naive@v1",
)
Result: TypeAlias = (
    mv.MaterializedDeviationResult
    | mv.MaterializedTimeRunResult
    | mv.MaterializedAssociationResult
    | mv.MaterializedForecastResult
)
View: TypeAlias = (
    mv.MaterializedNumericRelation
    | mv.MaterializedTemporalRelation
    | mv.MaterializedCoefficientRelation
    | mv.MaterializedBooleanRelation
)


@pytest.mark.runtime
@pytest.mark.parametrize("method", METHODS)
def test_frozen_views_scope_sharing_quantity_and_k(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j2")
    prepare_profiles(case, "KC", "table", "us", "UTC", False, followup=True)
    model_file = case.root / "models/semantic/sales/models.py"
    model_file.write_text(
        model_file.read_text()
        + "\ncopy_0 = ms.measure_column(name='copy_0', entity=orders, column='profile_0', additivity=ms.additive_all(), unit='CNY')\n"
    )
    ms.load(workspace_dir=case.root)
    session = mv.session.get_or_create("r85-" + method, report_timezone="UTC")
    members = session.members(ms.ref.entity("sales.order"))
    entity_values = members.read(ms.ref.measure("sales.order.profile_0"))
    assert isinstance(entity_values, mv.LogicalNumericRelation)
    other = members.read(ms.ref.measure("sales.order.copy_0"))
    assert isinstance(other, mv.LogicalNumericRelation)
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-08-04"), grain=mv.grain("day")
    )
    time_values = (
        members.each(grid)
        .observe(
            ms.ref.metric("sales.total_0"), during=grid.window, by=(ms.ref.entity("sales.order"),)
        )
        .group_by(grid)
        .rollup()
    )
    issued: list[str] = []
    submitted: list[str] = []
    original_batches, original_cursor = adapters.SourceSession.batches, adapters._native_cursor

    def audited(
        self: adapters.SourceSession, read: adapters.CompiledRead, *, chunk_size: int
    ) -> adapters.SourceBatchStream:
        assert read.sql == self._backend.compile(
            read.expression.as_table(), params=dict(read.params), limit=None
        )
        issued.append(read.sql)
        return original_batches(self, read, chunk_size=chunk_size)

    def cursor(backend: BaseBackend, name: str, sql: str) -> adapters._Cursor:
        submitted.append(sql)
        return original_cursor(backend, name, sql)

    monkeypatch.setattr(adapters.SourceSession, "batches", audited)
    monkeypatch.setattr(adapters, "_native_cursor", cursor)
    result: Result
    numeric: mv.MaterializedNumericRelation | mv.MaterializedCoefficientRelation
    views: dict[str, View]
    if method.startswith("deviation."):
        algorithm: Literal["zscore", "mad"] = "zscore" if "zscore" in method else "mad"
        logical_d = entity_values.deviation(method=algorithm)
        with patch.object(
            deviation_execution, "execute", wraps=deviation_execution.execute
        ) as compute:
            table = mv.table(
                observed=logical_d.observed,
                reference=logical_d.reference,
                deviation=logical_d.deviation,
                score=logical_d.score,
            ).execute()
        assert (
            sum(
                isinstance(
                    call.args[0].parameters, (AssociationFit, ForecastFit, DeviationFit, TimeRuns)
                )
                for call in compute.call_args_list
            )
            == 1
        )
        result = logical_d.execute()
        views = {
            "observed": result.observed,
            "reference": result.reference,
            "deviation": result.deviation,
            "score": result.score,
        }
        selected: Result = result.where(result.score.value.gt(0)).execute()
        numeric = result.score
        assert result.observed.to_pandas().value.tolist() == [1, 2, 7]
        assert isinstance(result.observed._node.root.signature.quantity, DerivedQuantity)
    elif method.startswith("association."):
        assoc: Literal["pearson", "spearman", "kendall"] = (
            "pearson" if "pearson" in method else "spearman" if "spearman" in method else "kendall"
        )
        logical_a = entity_values.correlate(other, method=assoc)
        with patch.object(
            statistical_execution, "execute", wraps=statistical_execution.execute
        ) as compute:
            table = mv.table(
                coefficient=logical_a.coefficient, selected=logical_a.selected
            ).execute()
        assert (
            sum(
                isinstance(
                    call.args[0].parameters, (AssociationFit, ForecastFit, DeviationFit, TimeRuns)
                )
                for call in compute.call_args_list
            )
            == 1
        )
        result = logical_a.execute()
        views = {"coefficient": result.coefficient, "selected": result.selected}
        selected = result.where(result.selected.value.eq(False)).execute()
        numeric = result.coefficient
        assert result.coefficient.to_pandas().value.tolist() == [1.0]
        assert isinstance(numeric._node.root.signature.quantity, DerivedQuantity)
        descriptive = numeric.summarize(mv.mean()).execute()
        assert isinstance(descriptive._node.root.signature.quantity, RowStatisticQuantity)
        assert descriptive.to_pandas().value.tolist() == [1.0]
    elif method == "time.runs@v1":
        logical_r = time_values.runs(where=time_values.value.gt(0))
        with patch.object(runs_execution, "execute", wraps=runs_execution.execute) as compute:
            table = mv.table(
                start=logical_r.start,
                end=logical_r.end,
                count=logical_r.count,
                duration=logical_r.duration,
            ).execute()
        assert (
            sum(
                isinstance(
                    call.args[0].parameters, (AssociationFit, ForecastFit, DeviationFit, TimeRuns)
                )
                for call in compute.call_args_list
            )
            == 1
        )
        result = logical_r.execute()
        views = {
            "start": result.start,
            "end": result.end,
            "count": result.count,
            "duration": result.duration,
        }
        selected = result.where(result.count.value.gt(3)).execute()
        numeric = result.count
        assert result.count.to_pandas().value.tolist() == [3]
        assert result.duration.to_pandas().value.iloc[0] == pd.Timedelta(days=3)
    else:
        model = (
            mv.naive()
            if method == "forecast.naive@v1"
            else mv.drift()
            if method == "forecast.drift@v1"
            else mv.seasonal_naive(periods=2)
        )
        logical_f = time_values.forecast(horizon=mv.periods(2), model=model)
        with patch.object(
            statistical_execution, "execute", wraps=statistical_execution.execute
        ) as compute:
            table = mv.table(
                prediction=logical_f.prediction, lower=logical_f.lower, upper=logical_f.upper
            ).execute()
        assert (
            sum(
                isinstance(
                    call.args[0].parameters, (AssociationFit, ForecastFit, DeviationFit, TimeRuns)
                )
                for call in compute.call_args_list
            )
            == 1
        )
        result = logical_f.execute()
        views = {"prediction": result.prediction, "lower": result.lower, "upper": result.upper}
        selected = result.where(result.prediction.value.gt(100)).execute()
        numeric = result.prediction
        assert result.prediction.to_pandas().value.tolist() == (
            [7.0, 7.0]
            if method == "forecast.naive@v1"
            else [10.0, 13.0]
            if "drift" in method
            else [2.0, 7.0]
        )
        assert isinstance(numeric._node.root.signature.quantity, DerivedQuantity)
        assert isinstance(
            numeric.summarize(mv.mean())._node.root.signature.quantity, RowStatisticQuantity
        )
        for bound in (result.lower, result.upper):
            with pytest.raises(AnalysisError):
                bound.summarize(mv.sum())
    assert issued and submitted == issued
    assert not any(
        token in sql.lower()
        for sql in submitted
        for token in ("create macro", "bignum", "__mv_exact", "axis_concentration")
    )
    assert len(table.to_pandas()) == len(result.to_pandas())
    frames = [v.to_pandas() for v in views.values()]
    keys = [
        tuple(c for c in frame.columns if c not in ("value", "cell_tag", "cell_reason"))
        for frame in frames
    ]
    assert all(k == keys[0] for k in keys)
    assert all(frame[list(keys[0])].equals(frames[0][list(keys[0])]) for frame in frames)
    selected_views: tuple[View, ...]
    if isinstance(selected, mv.MaterializedDeviationResult):
        selected_views = (selected.observed, selected.reference, selected.deviation, selected.score)
    elif isinstance(selected, mv.MaterializedTimeRunResult):
        selected_views = (selected.start, selected.end, selected.count, selected.duration)
    elif isinstance(selected, mv.MaterializedAssociationResult):
        selected_views = (selected.coefficient, selected.selected)
    else:
        selected_views = (selected.prediction, selected.lower, selected.upper)
    selected_frames = [v.to_pandas() for v in selected_views]
    selected_keys = [
        tuple(c for c in frame.columns if c not in ("value", "cell_tag", "cell_reason"))
        for frame in selected_frames
    ]
    assert all(k == selected_keys[0] for k in selected_keys)
    assert all(
        frame[list(selected_keys[0])].equals(selected_frames[0][list(selected_keys[0])])
        for frame in selected_frames
    )
    assert all(len(frame) == len(selected.to_pandas()) for frame in selected_frames)
    assert selected.evidence_digest().finding_count == result.evidence_digest().finding_count
    original_facts, selected_facts = (
        dict(result.contract()._facts),
        dict(selected.contract()._facts),
    )
    for name, fact in original_facts.items():
        if name.startswith("original_") or name in (
            "history_lengths",
            "horizon",
            "run_scope",
            "fit_scope",
            "scope",
        ):
            assert selected_facts[name] == fact
    ranking = numeric.rank(order="descending", ties="dense").limit(1).execute()
    assert len(ranking.values.to_pandas()) == 1
    assert ranking.ranks.to_pandas().value.tolist() == [1]
    for value in (numeric, selected):
        calls = tuple(a.call for a in value.contract().actions)
        assert not any(".rollup(" in c or ".attribute(" in c for c in calls)
        assert not hasattr(value, "attribute")
        if isinstance(value, mv.MaterializedNumericRelation):
            with pytest.raises(AnalysisError):
                value.rollup()
        else:
            assert not hasattr(value, "rollup")
    for view in views.values():
        quantity = view._node.root.signature.quantity
        assert quantity is not None
        assert "original_state" not in {part_role(p) for p in view._node.root.signature.parts}
        if isinstance(view, mv.MaterializedNumericRelation):
            with pytest.raises(AnalysisError):
                view.group_by(grid).rollup()
    foreign_session = mv.session.get_or_create("foreign-r85-" + method, report_timezone="UTC")
    foreign = foreign_session.members(ms.ref.entity("sales.order")).read(
        ms.ref.measure("sales.order.profile_0")
    )
    assert isinstance(foreign, mv.LogicalNumericRelation)
    before = session.runs().items
    with pytest.raises(AnalysisError) as invalid:
        result.where(foreign.value.gt(0))
    assert invalid.value.expected and invalid.value.received and invalid.value.repair is not None
    assert invalid.value.repair.action
    if invalid.value.repair.help_target is not None:
        help_api(invalid.value.repair.help_target.display)
        capsys.readouterr()
    assert session.runs().items == before
    result.show(max_output_bytes=4096)
    shown = capsys.readouterr().out
    assert len(shown.encode()) <= 4096 and "\n" not in repr(result)
    assert ".show()" in repr(result)
    assert result._dataset is not None
    dataset = result._dataset
    retained = dataset.verified()
    actual_roles = {p.role for p in retained.parts}
    assert actual_roles == {part_role(p) for p in result._node.root.signature.parts}
    assert set(result.contract().retained_parts) == actual_roles
    assert set(result.contract().required_parts) <= actual_roles
    physical = next(
        p
        for p in descriptor_plan(
            dataset.artifact.descriptor, result._node.definition
        ).physical_requirements
        if str(p.key.method) == method
    )
    assert isinstance(physical.implementation.qualification, Qualified)
    evidence_dir = os.environ.get("MARIVO_R85_EVIDENCE")
    if evidence_dir:
        Path(evidence_dir).mkdir(parents=True, exist_ok=True)
        Path(evidence_dir, method.replace("@", "-") + ".json").write_text(
            json.dumps(
                {
                    "method": method,
                    "qualification_key": key_json(physical.key),
                    "implementation_id": physical.implementation.qualification.implementation_id,
                    "retained_parts": sorted(actual_roles),
                    "artifact": dataset.artifact.artifact_ref,
                    "primary_receipt_digest": receipt_digest(
                        dataset.artifact.descriptor.primary_receipt
                    ),
                    "part_receipt_digests": {
                        p.role: receipt_digest(p) for p in dataset.artifact.descriptor.parts
                    },
                    "precision_contract": physical.implementation.precision,
                    "key_profile": "composite(string,int64)"
                    if method.startswith(("deviation.", "association."))
                    else "time_cell:string",
                    "origin_profile": "TABLE-NONE"
                    if method.startswith(("deviation.", "association."))
                    else "TABLE-US-UTC-builtin_day",
                    "time_profile": "none"
                    if method.startswith(("deviation.", "association."))
                    else "builtin_day:grid_us:UTC",
                    "numeric_policy": "r8_numeric_v1",
                    "implementation_contract_version": "v1",
                    "state_version": "v1",
                    "source_sql": issued,
                    "driver_sql": submitted,
                    "original_facts": original_facts,
                    "selected_facts": selected_facts,
                    "oracle": "raw int64 1,2,7; duplicate quantities association one; independent point vectors and contiguous three-day runs; synchronized keys, retained original scope, one consumer per table, typed quantity, actual parts/K",
                }
            )
        )
