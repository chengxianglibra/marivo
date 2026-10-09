"""Public retained-input statistics with independent carrier-aware oracles."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from fractions import Fraction
from itertools import pairwise
from pathlib import Path
from statistics import NormalDist
from typing import Literal
from zoneinfo import ZoneInfo

import ibis
import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError, StatisticalRelationError
from marivo.analysis.materialization.deviation_execution import _decode as decode_fit
from marivo.analysis.materialization.deviation_execution import load
from marivo.analysis.materialization.graph_protocol import descriptor_plan
from marivo.analysis.materialization.runs_execution import _decode as decode_runs
from marivo.analysis.materialization.statistical_execution import decode_forecast, decode_pairs
from marivo.analysis.methods.physical import FixedShape, Qualified
from marivo.datasource.adapters import SourceBatchStream, SourceSession
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.statistics.deviation_oracle import expected as deviation_oracle
from tests.datasource.source_cases import SourceData, source_case
from tests.support.json import key_json

Carrier = int | float | Decimal
Profile = Literal["int64-near-extremes", "finite-float64", "decimal-exact"]
Method = Literal["pearson", "spearman", "kendall", "zscore", "mad"]


@pytest.fixture
def profile() -> Profile:
    return "int64-near-extremes"


@pytest.fixture
def scenario() -> str:
    return "numeric-profile"


@dataclass(frozen=True)
class RetainedPair:
    a: mv.MaterializedNumericRelation
    b: mv.MaterializedNumericRelation
    x: tuple[Carrier | None, ...]
    y: tuple[Carrier, ...]
    history: mv.MaterializedNumericRelation
    history_b: mv.MaterializedNumericRelation
    entity_history: mv.MaterializedNumericRelation
    source_batches: tuple[tuple[int, ...], ...]


def originals(
    relation: mv.MaterializedNumericRelation | mv.MaterializedSelectedNumericRelation,
) -> tuple[Carrier, ...]:
    values: list[Carrier] = []
    for value in relation.to_pandas().value:
        assert isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)
        values.append(value)
    return tuple(values)


def ranks(values: tuple[Fraction, ...]) -> tuple[Fraction, ...]:
    return tuple(
        Fraction(sum(other < value for other in values))
        + Fraction(sum(other == value for other in values) + 1, 2)
        for value in values
    )


def coefficient(xs: tuple[Carrier, ...], ys: tuple[Carrier, ...], method: Method) -> float:
    x, y = tuple(map(Fraction, xs)), tuple(map(Fraction, ys))
    if method == "spearman":
        x, y = ranks(x), ranks(y)
    if method == "kendall":
        numerator = untied_x = untied_y = 0
        for i in range(len(x)):
            for j in range(i):
                dx, dy = (x[i] > x[j]) - (x[i] < x[j]), (y[i] > y[j]) - (y[i] < y[j])
                numerator += dx * dy
                untied_x += dx != 0
                untied_y += dy != 0
        denominator = Fraction(untied_x * untied_y)
        numerator_q = Fraction(numerator)
    else:
        mean_x, mean_y = sum(x, Fraction()) / len(x), sum(y, Fraction()) / len(y)
        numerator_q = sum(
            ((a - mean_x) * (b - mean_y) for a, b in zip(x, y, strict=True)), Fraction()
        )
        denominator = sum(((a - mean_x) * (a - mean_x) for a in x), Fraction()) * sum(
            ((b - mean_y) * (b - mean_y) for b in y), Fraction()
        )
    with localcontext(Context(prec=240, rounding=ROUND_HALF_EVEN)):
        return float(
            (Decimal(numerator_q.numerator) / Decimal(numerator_q.denominator))
            / (Decimal(denominator.numerator) / Decimal(denominator.denominator)).sqrt()
        )


@pytest.fixture
def retained_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    profile: Profile,
    scenario: str,
) -> Iterator[RetainedPair]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    if scenario == "decimal-center-scale-extremes":
        profile = "decimal-exact"
    typ = (
        "BIGINT"
        if profile == "int64-near-extremes"
        else "DOUBLE"
        if profile == "finite-float64"
        else "DECIMAL(38,6)"
    )
    base = (
        -(2**63) + 1
        if profile == "int64-near-extremes"
        else 10**32 - 1000
        if profile == "decimal-exact"
        else 0
    )
    xs = [
        str(base + value) if profile != "decimal-exact" else f"{base + value}.000001"
        for value in (0, 1, 1, 4)
    ]
    ys = ["1", "2", "1", "4"]
    if profile == "int64-near-extremes":
        xs = [str(-(2**63) + 1), str(2**63 - 2), str(2**63 - 2), str(-(2**63) + 5)]
    if profile == "finite-float64":
        xs = ["1e-250", "2e-250", "2e-250", "5e-250"]
        ys = ["1e200", "2e200", "1e200", "4e200"]
    if scenario == "unavailable-breaks":
        xs = ["1", "NULL", "1", "1"]
    if scenario == "empty-null-unavailable":
        xs, ys = ["1", "NULL", "3", "5"], ["1", "2", "0", "4"]
    dst = scenario in {"future-grid-interval", "full-grid-dst-adjacency-duration"}
    zone = "America/New_York" if dst else "UTC"
    start = datetime(2026, 10, 28) if dst else datetime(2026, 8, 1)
    days = (
        1027
        if scenario == "cross-batch-long-run"
        else 6
        if scenario == "full-grid-dst-adjacency-duration"
        else 4
    )
    instants = [
        (start + timedelta(days=i))
        .replace(tzinfo=ZoneInfo(zone))
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
        for i in range(4)
    ]
    data = SourceData(
        f"id BIGINT, tenant VARCHAR(10), revision BIGINT, happened TIMESTAMP, x {typ}, y {typ}",
        ",".join(
            f"(9007199254740993,'{tenant}',{i},'{instants[i].isoformat(sep=' ')}',{x},{y})"
            for i, (tenant, x, y) in enumerate(zip(("a", "a", "b", "b"), xs, ys, strict=True))
        ),
        "",
        [],
    )
    with source_case("duckdb", "table", tmp_path, monkeypatch, data=data) as case:
        assert isinstance(case.source, TableSourceIR)
        semantic_project_factory(
            {
                "datasources/warehouse.py": f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={case.session.datasource.fields['path']!r}, read_only=True)\n",
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='R9', default=True)\n",
                "sales/models.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"facts = ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table({case.source.table!r}), primary_key=['tenant','id','revision'])\n"
                + "x = ms.measure_column(name='x', entity=facts, column='x', additivity=ms.additive_all())\n"
                + "y = ms.measure_column(name='y', entity=facts, column='y', additivity=ms.additive_all())\n"
                + "event_time = ms.time_dimension_column(name='event_time', entity=facts, column='happened', granularity='second', parse=ms.timestamp(timezone='UTC'), is_default=True)\n"
                + f"total_x = ms.aggregate(name='total_x', measure=x, agg='sum', empty=ms.empty.{('null' if scenario in {'unavailable-breaks', 'empty-null-unavailable'} else 'zero')}())\n"
                + "total_y = ms.aggregate(name='total_y', measure=y, agg='sum', empty=ms.empty.zero())\n",
            }
        )
        session = mv.session.get_or_create("retained-risk", report_timezone=zone)
        members = session.members(ms.ref.entity("sales.facts"))
        grid = mv.time_grid(
            during=mv.time_scope(
                start=start.date().isoformat(),
                end=(start + timedelta(days=days)).date().isoformat(),
            ),
            grain=mv.grain("day"),
        )
        history = members.observe(ms.ref.metric("sales.total_x"), during=grid, by=(mv.member(),))
        converter = (
            int
            if profile == "int64-near-extremes"
            else float
            if profile == "finite-float64"
            else Decimal
        )
        batches: list[list[int]] = []
        iterate = SourceBatchStream._iterate

        def observed(stream: SourceBatchStream) -> Iterator[pa.RecordBatch]:
            sizes: list[int] = []
            batches.append(sizes)
            for batch in iterate(stream):
                sizes.append(batch.num_rows)
                yield batch

        with monkeypatch.context() as producer:
            if scenario == "cross-batch-long-run":
                producer.setattr(SourceBatchStream, "_iterate", observed)
            fixed_history = history.group_by(grid).rollup().execute()
        assert isinstance(fixed_history, mv.MaterializedGroupedNumericRelation)
        fixed_history_b = (
            members.observe(
                ms.ref.metric("sales.total_y"),
                during=grid,
                by=(mv.member(),),
            )
            .group_by(grid)
            .rollup()
            .execute()
            if scenario
            in {"complete-pair-lag-order", "unavailable-breaks", "empty-null-unavailable"}
            else fixed_history
        )
        assert isinstance(fixed_history_b, mv.MaterializedGroupedNumericRelation)
        entity_history = (
            history.execute() if scenario == "complete-identity-domain" else fixed_history
        )
        assert isinstance(entity_history, mv.MaterializedNumericRelation)
        yield RetainedPair(
            members.read(ms.ref.measure("sales.facts.x")).execute(),
            members.read(ms.ref.measure("sales.facts.y")).execute(),
            tuple(None if value == "NULL" else converter(value) for value in xs),
            tuple(converter(value) for value in ys),
            fixed_history,
            fixed_history_b,
            entity_history,
            tuple(tuple(sizes) for sizes in batches),
        )


def forbidden(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("Retained-input execution touched live source or Semantic")


@pytest.mark.runtime
@pytest.mark.parametrize("scenario", ["empty-null-unavailable"])
@pytest.mark.parametrize(
    "method",
    ["pearson", "spearman", "kendall", "zscore", "mad", "naive", "drift", "seasonal_naive", "runs"],
)
def test_public_retained_cell_boundaries_supporting(
    method: Literal[
        "pearson",
        "spearman",
        "kendall",
        "zscore",
        "mad",
        "naive",
        "drift",
        "seasonal_naive",
        "runs",
    ],
    scenario: str,
    retained_pair: RetainedPair,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a, b = retained_pair.a, retained_pair.b
    history, history_b = retained_pair.history, retained_pair.history_b
    assert a.to_pandas().cell_tag.tolist() == ["defined", "null", "defined", "defined"]
    with monkeypatch.context() as offline:
        offline.setattr(SourceSession, "__enter__", forbidden)
        offline.setattr(SemanticProject, "load", forbidden)
        offline.setattr(ibis.duckdb, "connect", forbidden)
        defined = a.value.is_defined()
        defined_a, defined_b = a.where(defined).execute(), b.where(defined).execute()
        ratio = defined_a.ratio(defined_b).execute()
        assert ratio.to_pandas().cell_tag.tolist() == ["defined", "undefined", "defined"]
        predicate = defined_a.value.gt(10)
        empty_a, empty_b = (
            defined_a.where(predicate).execute(),
            defined_b.where(predicate).execute(),
        )
        assert empty_a.to_pandas().empty and empty_b.to_pandas().empty
        oracle: dict[str, object] = {}
        if method in ("zscore", "mad"):
            fit = ratio.deviation(method=method).execute()
            assert fit._dataset is not None
            dataset, node = fit._dataset, fit._node.definition
            captured, state = decode_fit(dataset.verified().parts)
            partition = state.partitions[0]
            assert (partition.defined, partition.null, partition.undefined, partition.unknown) == (
                2,
                0,
                1,
                0,
            )
            center, scale, branch, scores = deviation_oracle((1.0, 1.25), method)
            assert partition.fit.center is not None and partition.fit.raw_scale is not None
            assert (
                partition.fit.center.value(),
                partition.fit.raw_scale.value(),
                partition.fit.branch,
            ) == (center, scale, branch)
            frame = fit.score.to_pandas()
            assert frame.cell_tag.tolist() == ["defined", "undefined", "defined"]
            assert frame[frame.cell_tag == "defined"].value.tolist() == list(scores)
            assert load(captured.primary).num_rows == 3
            null_fit = a.deviation(method=method).execute()
            assert null_fit._dataset is not None
            _, null_state = decode_fit(null_fit._dataset.verified().parts)
            assert null_state.partitions[0].null == 1 and null_state.partitions[0].fit.n == 3
            null_scores = null_fit.score.to_pandas()
            assert null_scores.cell_tag.tolist() == ["defined", "null", "defined", "defined"]
            assert null_scores[null_scores.cell_tag == "defined"].value.tolist() == list(
                deviation_oracle((1, 3, 5), method)[3]
            )
            empty = empty_a.deviation(method=method).execute()
            assert empty.score.to_pandas().empty and empty._dataset is not None
            _, empty_state = decode_fit(empty._dataset.verified().parts)
            assert empty_state.partitions[0].fit.n == 0
            family = "deviation"
            oracle = {
                "partition": [2, 0, 1, 0],
                "center": str(center),
                "scale": str(scale),
                "empty_rows": 0,
            }
        elif method in ("pearson", "spearman", "kendall"):
            association = a.correlate(b, method=method).execute()
            expected = coefficient((1, 3, 5), (1, 0, 4), method)
            assert association.coefficient.to_pandas().value.tolist() == [expected]
            assert association._dataset is not None
            dataset, node = association._dataset, association._node.definition
            _, state_a = decode_pairs(dataset.verified().parts)
            candidate = state_a.candidates[0]
            assert (candidate.input_count, candidate.complete_pairs, candidate.null_pairs) == (
                4,
                3,
                1,
            )
            with pytest.raises(StatisticalRelationError) as unavailable:
                ratio.correlate(defined_b, method=method).execute()
            assert unavailable.value.code == "r8.cell_policy"
            with pytest.raises(StatisticalRelationError) as no_pairs:
                empty_a.correlate(empty_b, method=method).execute()
            assert no_pairs.value.code == "r8.no_valid_candidate"
            family = "association"
            oracle = {
                "coefficient": expected,
                "complete_pairs": 3,
                "ordinary_null_pairs": 1,
                "undefined_rejected": True,
                "empty_rejected": True,
            }
        elif method == "runs":
            intervals = history.runs(where=history.value.gt(0)).execute()
            assert sorted(intervals.count.to_pandas().value.tolist()) == [1, 2]
            assert intervals._dataset is not None
            dataset, node = intervals._dataset, intervals._node.definition
            captured_runs, state_runs = decode_runs(dataset.verified().parts)
            assert state_runs.classifications.count("unavailable") == 1
            assert load(captured_runs.inputs[0]).num_rows == 4
            empty_runs = history.runs(where=history.value.gt(10)).execute()
            assert empty_runs.count.to_pandas().empty and empty_runs._dataset is not None
            _, empty_run_state = decode_runs(empty_runs._dataset.verified().parts)
            assert empty_run_state.classifications.count("false") == 3
            assert empty_run_state.classifications.count("unavailable") == 1
            ratio_history = history_b.ratio(history_b).execute()
            with pytest.raises(AnalysisError, match="original captured coverage fact"):
                ratio_history.runs(where=ratio_history.value.gt(0)).execute()
            family = "time"
            oracle = {
                "interval_counts": [1, 2],
                "null_breaks": 1,
                "empty_rows": 0,
                "missing_coverage_rejected": True,
            }
        else:
            model = (
                mv.naive()
                if method == "naive"
                else mv.drift()
                if method == "drift"
                else mv.seasonal_naive(periods=2)
            )
            forecast = history_b.forecast(horizon=mv.periods(1), model=model).execute()
            assert forecast.prediction.to_pandas().value.tolist() == [
                {"naive": 4.0, "drift": 5.0, "seasonal_naive": 0.0}[method]
            ]
            assert forecast._dataset is not None
            dataset, node = forecast._dataset, forecast._node.definition
            with pytest.raises(StatisticalRelationError) as null_history:
                history.forecast(horizon=mv.periods(1), model=model).execute()
            assert null_history.value.code == "r8.cell_policy"
            ratio_history = history_b.ratio(history_b).execute()
            with pytest.raises(StatisticalRelationError) as undefined_history:
                ratio_history.forecast(horizon=mv.periods(1), model=model).execute()
            assert undefined_history.value.code == "r8.cell_policy"
            empty_history = history_b.where(history_b.value.gt(10)).execute()
            assert empty_history.to_pandas().empty
            with pytest.raises(AnalysisError):
                empty_history.forecast(horizon=mv.periods(1), model=model)
            family = "forecast"
            oracle = {"null_rejected": True, "undefined_rejected": True, "empty_rejected": True}
        selected = next(
            item
            for item in descriptor_plan(dataset.artifact.descriptor, node).physical_requirements
            if item.key.method.name == family + "." + method
        )
        assert (
            isinstance(selected.key.shape, FixedShape) and selected.key.route == "artifact_python"
        )
        assert a._runtime.store.resources(a._runtime.session_ref) == ()
        evidence = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
        if evidence:
            Path(evidence, f"supporting-retained-{method}-{scenario}.json").write_text(
                json.dumps(
                    {
                        "family": family + "." + method + "@v1",
                        "scenario": scenario,
                        "oracle": oracle,
                        "physical_key": key_json(selected.key),
                        "retained_parts": [part.role for part in dataset.verified().parts],
                        "source_semantic_duckdb_forbidden": True,
                        "numeric_unknown_runtime_verified": False,
                        "boundary": "Public producer and retained consumer support only; no complete empty/null/unavailable qualification or numeric Unknown producer proof",
                    },
                    sort_keys=True,
                )
            )


@pytest.mark.runtime
@pytest.mark.parametrize("scenario", ["complete-identity-domain"])
@pytest.mark.parametrize(
    "method",
    ["pearson", "spearman", "kendall", "zscore", "mad", "naive", "drift", "seasonal_naive", "runs"],
)
def test_public_retained_identity(
    method: Literal[
        "pearson",
        "spearman",
        "kendall",
        "zscore",
        "mad",
        "naive",
        "drift",
        "seasonal_naive",
        "runs",
    ],
    scenario: str,
    retained_pair: RetainedPair,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {
        ("a", 9007199254740993, 0),
        ("a", 9007199254740993, 1),
        ("b", 9007199254740993, 2),
        ("b", 9007199254740993, 3),
    }

    def checked_keys(table: pa.Table, keys: tuple[str, ...], timed: bool) -> None:
        assert len(keys) == (4 if timed else 3)
        assert table.schema.field(keys[1]).type == pa.int64()
        identities = [tuple(row[key] for key in keys[:3]) for row in table.to_pylist()]
        assert set(identities) == expected
        assert len({tuple(row[key] for key in keys) for row in table.to_pylist()}) == table.num_rows
        assert table.num_rows == (16 if timed else 4)
        if timed:
            assert all(identities.count(identity) == 4 for identity in expected)

    with monkeypatch.context() as offline:
        offline.setattr(SourceSession, "__enter__", forbidden)
        offline.setattr(SemanticProject, "load", forbidden)
        offline.setattr(ibis.duckdb, "connect", forbidden)
        result: (
            mv.MaterializedAssociationResult
            | mv.MaterializedDeviationResult
            | mv.MaterializedForecastResult
            | mv.MaterializedTimeRunResult
        )
        if method in {"pearson", "spearman", "kendall"}:
            algorithm: Literal["pearson", "spearman", "kendall"] = (
                "pearson"
                if method == "pearson"
                else "spearman"
                if method == "spearman"
                else "kendall"
            )
            result = retained_pair.a.correlate(retained_pair.b, method=algorithm).execute()
            assert result._dataset is not None
            pairs, state = decode_pairs(result._dataset.verified().parts)
            for value in pairs.inputs:
                checked_keys(load(value.primary), value.keys, False)
            assert len(state.candidates) == 1 and state.candidates[0].complete_pairs == 4
            assert result.coefficient.to_pandas().value.tolist() == [
                coefficient(originals(retained_pair.a), originals(retained_pair.b), algorithm)
            ]
            family = "association"
        elif method in {"zscore", "mad"}:
            deviation: Literal["zscore", "mad"] = "zscore" if method == "zscore" else "mad"
            result = retained_pair.a.deviation(method=deviation).execute()
            assert result._dataset is not None
            inputs, fit = decode_fit(result._dataset.verified().parts)
            checked_keys(load(inputs.primary), inputs.keys, False)
            assert len(fit.partitions) == 1 and fit.partitions[0].defined == 4
            assert result.score.to_pandas().value.tolist() == list(
                deviation_oracle(originals(retained_pair.a), deviation)[3]
            )
            family = "deviation"
        elif method == "runs":
            history = retained_pair.entity_history
            result = history.runs(where=history.value.gte(-(2**63) + 1)).execute()
            assert result._dataset is not None
            captured, runs = decode_runs(result._dataset.verified().parts)
            checked_keys(load(captured.inputs[0]), captured.keys, True)
            assert runs.classifications == ("true",) * 16
            assert result.count.to_pandas().value.tolist() == [4] * 4
            assert result.duration.to_pandas().value.tolist() == [timedelta(days=4)] * 4
            subject = next(
                part.table for part in result._dataset.verified().parts if part.role == "subject"
            )
            assert {
                tuple(row[f"subject__key_{i}"] for i in range(3)) for row in subject.to_pylist()
            } == expected
            family = "time"
        else:
            model = (
                mv.naive()
                if method == "naive"
                else mv.drift()
                if method == "drift"
                else mv.seasonal_naive(periods=2)
            )
            result = retained_pair.entity_history.forecast(
                horizon=mv.periods(2), model=model
            ).execute()
            assert result._dataset is not None
            training, forecast = decode_forecast(result._dataset.verified().parts)
            checked_keys(load(training.input.primary), training.input.keys, True)
            assert len(forecast.series) == 4 and all(
                series.training.n == 4 for series in forecast.series
            )
            assert sorted(i for series in forecast.series for i in series.indices) == list(
                range(16)
            )
            assert result.prediction.to_pandas().shape[0] == 8
            family = "forecast"
        assert result._dataset is not None
        dataset = result._dataset
        assert (
            dataset.verified().contract.signature.domain.binding.session_id
            == retained_pair.a._node.root.signature.domain.binding.session_id
        )
        physical = next(
            item
            for item in descriptor_plan(
                dataset.artifact.descriptor, result._node.definition
            ).physical_requirements
            if item.key.method.name == family + "." + method
        )
        assert (
            isinstance(physical.key.shape, FixedShape) and physical.key.route == "artifact_python"
        )
        assert isinstance(physical.implementation.qualification, Qualified)
        evidence = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
        if evidence:
            Path(evidence, f"retained-{method}-{scenario}.json").write_text(
                json.dumps(
                    {
                        "id": f"R9:{family}.{method}@v1:none:retained-input:artifact_python:{scenario}",
                        "original_x": [str(value) for value in retained_pair.x],
                        "original_y": [str(value) for value in retained_pair.y],
                        "oracle": {
                            "compound_identities": sorted(expected),
                            "retained_domain_complete": True,
                        },
                        "physical_key": key_json(physical.key),
                        "implementation_id": physical.implementation.qualification.implementation_id,
                        "retained_parts": [part.role for part in dataset.verified().parts],
                        "source_semantic_duckdb_forbidden": True,
                    },
                    sort_keys=True,
                )
            )


@pytest.mark.runtime
@pytest.mark.parametrize(
    "scenario", ["full-grid-dst-adjacency-duration", "cross-batch-long-run", "unavailable-breaks"]
)
def test_public_retained_runs_algorithm(
    scenario: str,
    retained_pair: RetainedPair,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as offline:
        offline.setattr(SourceSession, "__enter__", forbidden)
        offline.setattr(SemanticProject, "load", forbidden)
        offline.setattr(ibis.duckdb, "connect", forbidden)
        history = retained_pair.history
        result = history.runs(where=history.value.gte(-(2**63) + 1)).execute()
        assert result._dataset is not None
        dataset = result._dataset
        captured, state = decode_runs(dataset.verified().parts)
        values = load(captured.inputs[0])
        grid = captured.signature.domain.time_grid
        assert grid is not None
        anchor = next(
            key
            for key, coordinate in zip(
                captured.keys, captured.signature.domain.instance_key, strict=True
            )
            if coordinate.role == "anchor"
        )
        lookup = {value: i for i, value in enumerate(values[anchor].to_pylist())}
        ordered = [lookup[cell.identity] for cell in grid.cells]
        expected_count = (
            1027
            if scenario == "cross-batch-long-run"
            else 6
            if scenario == "full-grid-dst-adjacency-duration"
            else 4
        )
        assert values.num_rows == expected_count
        expected_classes = (
            tuple("unavailable" if i == ordered[1] else "true" for i in range(4))
            if scenario == "unavailable-breaks"
            else ("true",) * expected_count
        )
        assert state.classifications == expected_classes
        views = load(state.views)
        assert views["input_rows"].to_pylist() == (
            [[ordered[0]], ordered[2:]] if scenario == "unavailable-breaks" else [ordered]
        )
        expected_days = [1, 2] if scenario == "unavailable-breaks" else [expected_count]
        starts = (
            [grid.cells[0].start, grid.cells[2].start]
            if scenario == "unavailable-breaks"
            else [grid.cells[0].start]
        )
        ends = (
            [grid.cells[0].end, grid.cells[-1].end]
            if scenario == "unavailable-breaks"
            else [grid.cells[-1].end]
        )
        assert views["start"].to_pylist() == starts and views["end"].to_pylist() == ends
        actual_starts = result.start.to_pandas().value.tolist()
        assert sorted(zip(actual_starts, result.count.to_pandas().value, strict=True)) == list(
            zip(starts, expected_days, strict=True)
        )
        expected_durations = [timedelta(days=count) for count in expected_days]
        if scenario == "full-grid-dst-adjacency-duration":
            expected_durations = [timedelta(hours=145)]
        assert sorted(zip(actual_starts, result.duration.to_pandas().value, strict=True)) == list(
            zip(starts, expected_durations, strict=True)
        )
        if scenario == "unavailable-breaks":
            assert values["cell_tag"].take(pa.array(ordered)).to_pylist() == [
                "defined",
                "null",
                "defined",
                "defined",
            ]
            assert views["left_kind"].to_pylist() == ["scope_boundary", "unavailable"]
            assert views["right_kind"].to_pylist() == ["unavailable", "scope_boundary"]
        if scenario == "cross-batch-long-run":
            assert any(
                len(sizes) > 1 and sum(sizes) >= 1027 for sizes in retained_pair.source_batches
            )
        selected = result.where(result.count.value.gte(1)).execute()
        assert selected._dataset is not None
        assert decode_runs(selected._dataset.verified().parts) == (captured, state)
        assert selected.count.to_pandas().equals(result.count.to_pandas())
        physical = next(
            item
            for item in descriptor_plan(
                dataset.artifact.descriptor, result._node.definition
            ).physical_requirements
            if item.key.method.name == "time.runs"
        )
        assert (
            isinstance(physical.key.shape, FixedShape) and physical.key.route == "artifact_python"
        )
        assert isinstance(physical.implementation.qualification, Qualified)
        evidence = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
        if evidence:
            Path(evidence, f"retained-runs-{scenario}.json").write_text(
                json.dumps(
                    {
                        "id": f"R9:time.runs@v1:none:retained-input:artifact_python:{scenario}",
                        "original_x": [str(value) for value in values["value"].to_pylist()],
                        "original_y": [],
                        "oracle": {
                            "classifications": expected_classes,
                            "counts": expected_days,
                            "duration_seconds": [
                                value.total_seconds() for value in expected_durations
                            ],
                            "producer_batches": retained_pair.source_batches,
                            "signature": repr(captured.signature),
                        },
                        "physical_key": key_json(physical.key),
                        "implementation_id": physical.implementation.qualification.implementation_id,
                        "retained_parts": [part.role for part in dataset.verified().parts],
                        "source_semantic_duckdb_forbidden": True,
                    },
                    sort_keys=True,
                )
            )


@pytest.mark.runtime
@pytest.mark.parametrize(
    "method,scenario",
    [
        (method, scenario)
        for method in ("pearson", "spearman", "kendall")
        for scenario in (
            "complete-pair-lag-order",
            "invalid-constant-pairs",
            "centered-numeric-extremes" if method == "pearson" else "ties-average-rank-tau-b",
        )
    ],
)
def test_public_retained_association_algorithm(
    method: Literal["pearson", "spearman", "kendall"],
    scenario: str,
    retained_pair: RetainedPair,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lagged = scenario == "complete-pair-lag-order"
    a, b = (
        (retained_pair.history, retained_pair.history_b)
        if lagged
        else (retained_pair.a, retained_pair.b)
    )
    x, y = originals(a), originals(b)
    with monkeypatch.context() as offline:
        offline.setattr(SourceSession, "__enter__", forbidden)
        offline.setattr(SemanticProject, "load", forbidden)
        offline.setattr(ibis.duckdb, "connect", forbidden)
        if scenario == "invalid-constant-pairs":
            predicate = a.value.eq(2**63 - 2)
            constant = a.where(predicate).execute()
            corresponding = b.where(predicate).execute()
            assert originals(constant) == (2**63 - 2, 2**63 - 2)
            with pytest.raises(StatisticalRelationError) as invalid:
                constant.correlate(corresponding, method=method).execute()
            assert invalid.value.code == "r8.no_valid_candidate"
            assert "(0, 'constant_a', 2)" in str(invalid.value)
        result = a.correlate(b, method=method, lag_range=range(-3, 4) if lagged else None).execute()
        assert result._dataset is not None
        dataset = result._dataset
        captured, state = decode_pairs(dataset.verified().parts)
        assert len(captured.inputs) == 2
        assert tuple(load(captured.inputs[0].primary)["value"].to_pylist()) == x
        assert tuple(load(captured.inputs[1].primary)["value"].to_pylist()) == y
        assert sorted(candidate.lag for candidate in state.candidates) == (
            list(range(-3, 4)) if lagged else [0]
        )
        oracle: list[dict[str, object]] = []
        for candidate in state.candidates:
            pairs = tuple((i, i + candidate.lag) for i in range(4) if 0 <= i + candidate.lag < 4)
            assert tuple(zip(candidate.left_indices, candidate.right_indices, strict=True)) == pairs
            assert (
                candidate.input_count,
                candidate.matched,
                candidate.boundary_drop,
                candidate.null_pairs,
                candidate.complete_pairs,
            ) == (4, len(pairs), 4 - len(pairs), 0, len(pairs))
            xx, yy = tuple(x[i] for i, _ in pairs), tuple(y[j] for _, j in pairs)
            expected_status = (
                "insufficient_pairs"
                if len(pairs) < 2
                else "constant_a"
                if len(set(xx)) == 1
                else "valid"
            )
            assert candidate.score.status == expected_status
            expected = coefficient(xx, yy, method) if expected_status == "valid" else None
            assert candidate.score.coefficient == expected
            if expected_status == "valid" and method == "spearman":
                assert tuple(value.value() for value in candidate.score.ranks_a) == ranks(
                    tuple(map(Fraction, xx))
                )
                assert tuple(value.value() for value in candidate.score.ranks_b) == ranks(
                    tuple(map(Fraction, yy))
                )
            if expected_status == "valid" and method == "kendall":
                comparisons = [
                    (Fraction(xx[i]) - Fraction(xx[j]), Fraction(yy[i]) - Fraction(yy[j]))
                    for i in range(len(xx))
                    for j in range(i)
                ]
                counts = (
                    sum(dx * dy > 0 for dx, dy in comparisons),
                    sum(dx * dy < 0 for dx, dy in comparisons),
                    sum(dx == 0 and dy != 0 for dx, dy in comparisons),
                    sum(dy == 0 and dx != 0 for dx, dy in comparisons),
                )
                assert (
                    candidate.score.concordant,
                    candidate.score.discordant,
                    candidate.score.ties_a,
                    candidate.score.ties_b,
                ) == counts
            oracle.append(
                {
                    "lag": candidate.lag,
                    "pairs": pairs,
                    "status": expected_status,
                    "coefficient": expected,
                }
            )
        valid = [
            candidate for candidate in state.candidates if candidate.score.coefficient is not None
        ]
        if valid:
            winner = min(
                (
                    (value, candidate.lag, candidate.key)
                    for candidate in valid
                    if (value := candidate.score.coefficient) is not None
                ),
                key=lambda item: (-abs(item[0]), abs(item[1]), item[1]),
            )
            assert [candidate.key for candidate in state.candidates if candidate.selected] == [
                winner[2]
            ]
        else:
            assert not any(candidate.selected for candidate in state.candidates)
            assert result.coefficient.to_pandas().value.isna().all()
        selected = result.where(result.selected.value.eq(True)).execute()
        assert selected._dataset is not None
        selected_inputs, selected_state = decode_pairs(selected._dataset.verified().parts)
        assert selected_inputs == captured and selected_state == state
        physical = next(
            item
            for item in descriptor_plan(
                dataset.artifact.descriptor, result._node.definition
            ).physical_requirements
            if item.key.method.name == "association." + method
        )
        assert (
            isinstance(physical.key.shape, FixedShape) and physical.key.route == "artifact_python"
        )
        assert isinstance(physical.implementation.qualification, Qualified)
        evidence = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
        if evidence:
            Path(evidence, f"retained-{method}-{scenario}.json").write_text(
                json.dumps(
                    {
                        "id": f"R9:association.{method}@v1:none:retained-input:artifact_python:{scenario}",
                        "original_x": [str(value) for value in x],
                        "original_y": [str(value) for value in y],
                        "oracle": {
                            "candidates": oracle,
                            "original_pairs_retained": True,
                            "constant_pair_rejected": scenario == "invalid-constant-pairs",
                        },
                        "physical_key": key_json(physical.key),
                        "implementation_id": physical.implementation.qualification.implementation_id,
                        "retained_parts": [part.role for part in dataset.verified().parts],
                        "source_semantic_duckdb_forbidden": True,
                    },
                    sort_keys=True,
                )
            )


@pytest.mark.runtime
@pytest.mark.parametrize(
    "scenario", ["complete-training", "model-innovation-variance", "future-grid-interval"]
)
@pytest.mark.parametrize("method", ["naive", "drift", "seasonal_naive"])
def test_public_retained_forecast_algorithm(
    method: Literal["naive", "drift", "seasonal_naive"],
    scenario: Literal["complete-training", "model-innovation-variance", "future-grid-interval"],
    retained_pair: RetainedPair,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = retained_pair.history
    x = originals(history)
    model = (
        mv.naive()
        if method == "naive"
        else mv.drift()
        if method == "drift"
        else mv.seasonal_naive(periods=2)
    )
    with monkeypatch.context() as offline:
        offline.setattr(SourceSession, "__enter__", forbidden)
        offline.setattr(SemanticProject, "load", forbidden)
        offline.setattr(ibis.duckdb, "connect", forbidden)
        if scenario == "complete-training":
            with pytest.raises(StatisticalRelationError) as incomplete:
                history.where(history.value.is_defined()).forecast(
                    horizon=mv.periods(4), model=model
                )
            assert incomplete.value.code == "r8.grid_incomplete"
        result = history.forecast(horizon=mv.periods(4), model=model, interval_level=0.8).execute()
        assert result._dataset is not None
        dataset = result._dataset
        captured, state = decode_forecast(dataset.verified().parts)
        assert len(state.series) == 1
        series = state.series[0]
        assert tuple(load(captured.input.primary)["value"][i].as_py() for i in series.indices) == x
        values = tuple(map(Fraction, x))
        slope = (values[-1] - values[0]) / 3 if method == "drift" else Fraction()
        distance = 2 if method == "seasonal_naive" else 1
        innovations = tuple(values[i] - values[i - distance] - slope for i in range(distance, 4))
        df = 2 if method in {"drift", "seasonal_naive"} else 3
        sigma2 = sum((value * value for value in innovations), Fraction()) / df
        assert (series.training.n, series.training.df) == (4, df)
        assert tuple(value.value() for value in series.training.innovations) == innovations
        assert series.training.slope.value() == slope and series.training.sigma2.value() == sigma2
        points = tuple(
            values[-2 + (h - 1) % 2] if method == "seasonal_naive" else values[-1] + h * slope
            for h in range(1, 5)
        )
        variances = tuple(
            sigma2
            * (
                (1 + (h - 1) // 2)
                if method == "seasonal_naive"
                else h * (1 + Fraction(h, 3))
                if method == "drift"
                else h
            )
            for h in range(1, 5)
        )
        assert tuple(point.h for point in series.points) == (1, 2, 3, 4)
        assert tuple(point.point.value() for point in series.points) == points
        assert tuple(point.variance.value() for point in series.points) == variances
        assert result.prediction.to_pandas().value.tolist() == [float(value) for value in points]
        with localcontext(Context(prec=240, rounding=ROUND_HALF_EVEN)):
            quantile = Decimal(str(NormalDist().inv_cdf(0.9)))
            for view, sign in ((result.lower, -1), (result.upper, 1)):
                for actual, point, variance in zip(
                    view.to_pandas().value, points, variances, strict=True
                ):
                    prediction = Decimal(point.numerator) / Decimal(point.denominator)
                    width = (
                        quantile
                        * (Decimal(variance.numerator) / Decimal(variance.denominator)).sqrt()
                    )
                    expected = prediction + sign * width
                    assert abs(Decimal.from_float(float(actual)) - expected) <= max(
                        abs(prediction), abs(width), abs(expected)
                    ) * Decimal("1e-14")
        cells = captured.future.grid.cells
        assert len(cells) == 4 and all(not cell.partial for cell in cells)
        assert all(left.end == right.start for left, right in pairwise(cells))
        if scenario == "future-grid-interval":
            starts = [
                datetime(2026, 11, 1 + i, tzinfo=ZoneInfo("America/New_York")).astimezone(
                    timezone.utc
                )
                for i in range(5)
            ]
            assert [(cell.start, cell.end) for cell in cells] == list(pairwise(starts))
            assert [(cell.end - cell.start).total_seconds() for cell in cells] == [
                90000,
                86400,
                86400,
                86400,
            ]
        threshold = float(result.lower.to_pandas().value.iloc[0])
        selected = result.where(result.lower.value.gt(threshold)).execute()
        assert selected._dataset is not None
        selected_inputs, selected_state = decode_forecast(selected._dataset.verified().parts)
        assert selected_inputs == captured and selected_state == state
        assert selected.prediction.to_pandas().value.tolist() == [
            prediction
            for prediction, lower in zip(
                result.prediction.to_pandas().value, result.lower.to_pandas().value, strict=True
            )
            if lower > threshold
        ]
        physical = next(
            item
            for item in descriptor_plan(
                dataset.artifact.descriptor, result._node.definition
            ).physical_requirements
            if item.key.method.name == "forecast." + method
        )
        assert (
            isinstance(physical.key.shape, FixedShape) and physical.key.route == "artifact_python"
        )
        assert isinstance(physical.implementation.qualification, Qualified)
        evidence = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
        if evidence:
            Path(evidence, f"retained-{method}-{scenario}.json").write_text(
                json.dumps(
                    {
                        "id": f"R9:forecast.{method}@v1:none:retained-input:artifact_python:{scenario}",
                        "original_x": [str(value) for value in x],
                        "original_y": [],
                        "oracle": {
                            "innovations": [str(value) for value in innovations],
                            "df": df,
                            "sigma2": str(sigma2),
                            "points": [str(value) for value in points],
                            "variances": [str(value) for value in variances],
                            "interval_level": 0.8,
                            "future_cells": [
                                [cell.start.isoformat(), cell.end.isoformat()] for cell in cells
                            ],
                            "original_training_retained": True,
                        },
                        "physical_key": key_json(physical.key),
                        "implementation_id": physical.implementation.qualification.implementation_id,
                        "retained_parts": [part.role for part in dataset.verified().parts],
                        "source_semantic_duckdb_forbidden": True,
                    },
                    sort_keys=True,
                )
            )


@pytest.mark.runtime
@pytest.mark.parametrize(
    "scenario", ["original-fit-domain", "zero-scale", "decimal-center-scale-extremes"]
)
@pytest.mark.parametrize("method", ["zscore", "mad"])
def test_public_retained_deviation_algorithm(
    method: Literal["zscore", "mad"],
    scenario: Literal["original-fit-domain", "zero-scale", "decimal-center-scale-extremes"],
    retained_pair: RetainedPair,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as offline:
        offline.setattr(SourceSession, "__enter__", forbidden)
        offline.setattr(SemanticProject, "load", forbidden)
        offline.setattr(ibis.duckdb, "connect", forbidden)
        source: mv.MaterializedNumericRelation | mv.MaterializedSelectedNumericRelation = (
            retained_pair.a
        )
        if scenario == "zero-scale":
            source = source.where(source.value.eq(2**63 - 2)).execute()
        x = originals(source)
        center, scale, branch, scores = deviation_oracle(x, method)
        fitted = source.deviation(method=method).execute()
        assert fitted._dataset is not None
        dataset = fitted._dataset
        captured, state = decode_fit(dataset.verified().parts)
        assert tuple(load(captured.primary)["value"].to_pylist()) == x
        if scenario == "decimal-center-scale-extremes":
            assert all(isinstance(value, Decimal) for value in x)
            assert load(captured.primary).schema.field("value").type == pa.decimal128(38, 6)
            assert center > 10**32 - 1001 and scale < 10
        assert len(state.partitions) == 1
        partition = state.partitions[0]
        assert (partition.defined, partition.null, partition.undefined, partition.unknown) == (
            len(x),
            0,
            0,
            0,
        )
        assert sorted(partition.indices) == list(range(len(x)))
        assert partition.fit.center is not None and partition.fit.raw_scale is not None
        assert (
            partition.fit.center.value(),
            partition.fit.raw_scale.value(),
            partition.fit.branch,
        ) == (center, scale, branch)
        if scenario == "zero-scale":
            assert x == (2**63 - 2, 2**63 - 2) and scale == 0 and scores == ()
            actual = fitted.score.to_pandas()
            assert actual.cell_reason.tolist() == ["zero_scale", "zero_scale"]
            assert actual.value.isna().all()
        else:
            assert fitted.score.to_pandas().value.tolist() == list(scores)
            selected = fitted.where(fitted.score.value.gt(0)).execute()
            assert selected.score.to_pandas().value.tolist() == [
                value for value in scores if value > 0
            ]
            assert selected._dataset is not None
            selected_inputs, selected_state = decode_fit(selected._dataset.verified().parts)
            assert selected_inputs == captured and selected_state == state
            assert selected.observed.to_pandas().value.tolist() == [
                value for value, score in zip(x, scores, strict=True) if score > 0
            ]
        physical = next(
            item
            for item in descriptor_plan(
                dataset.artifact.descriptor, fitted._node.definition
            ).physical_requirements
            if item.key.method.name == "deviation." + method
        )
        assert (
            isinstance(physical.key.shape, FixedShape) and physical.key.route == "artifact_python"
        )
        assert isinstance(physical.implementation.qualification, Qualified)
        evidence = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
        if evidence:
            Path(evidence, f"retained-{method}-{scenario}.json").write_text(
                json.dumps(
                    {
                        "id": f"R9:deviation.{method}@v1:none:retained-input:artifact_python:{scenario}",
                        "original_x": [str(value) for value in x],
                        "original_y": [],
                        "oracle": {
                            "center": str(center),
                            "scale": str(scale),
                            "branch": branch,
                            "scores": scores,
                            "original_fit_retained": scenario == "original-fit-domain",
                        },
                        "physical_key": key_json(physical.key),
                        "implementation_id": physical.implementation.qualification.implementation_id,
                        "retained_parts": [part.role for part in dataset.verified().parts],
                        "source_semantic_duckdb_forbidden": True,
                    },
                    sort_keys=True,
                )
            )


@pytest.mark.runtime
@pytest.mark.parametrize("profile", ["int64-near-extremes", "finite-float64", "decimal-exact"])
@pytest.mark.parametrize("method", ["pearson", "spearman", "kendall", "zscore", "mad"])
def test_public_retained_numeric_risk(
    profile: Profile,
    method: Method,
    retained_pair: RetainedPair,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a, b = retained_pair.a, retained_pair.b
    x, y = originals(a), originals(b)
    assert len(x) == len(y) == 4
    assert x == retained_pair.x and y == retained_pair.y
    physical_type = (
        pa.int64()
        if profile == "int64-near-extremes"
        else pa.float64()
        if profile == "finite-float64"
        else pa.decimal128(38, 6)
    )
    assert a._dataset is not None and b._dataset is not None
    assert a._dataset.verified().primary.schema.field("value").type == physical_type
    assert b._dataset.verified().primary.schema.field("value").type == physical_type
    with monkeypatch.context() as offline:
        offline.setattr(SourceSession, "__enter__", forbidden)
        offline.setattr(SemanticProject, "load", forbidden)
        offline.setattr(ibis.duckdb, "connect", forbidden)
        if method in ("zscore", "mad"):
            result = a.deviation(method=method).execute()
            center, scale, branch, scores = deviation_oracle(x, method)
            assert result.score.to_pandas().value.tolist() == list(scores)
            assert result._dataset is not None
            inputs, state = decode_fit(result._dataset.verified().parts)
            assert inputs.fit_id == state.fit_id and len(state.partitions) == 1
            partition = state.partitions[0]
            assert (partition.defined, partition.null, partition.undefined, partition.unknown) == (
                4,
                0,
                0,
                0,
            )
            assert sorted(partition.indices) == [0, 1, 2, 3]
            fit = state.partitions[0].fit
            assert fit.center is not None and fit.raw_scale is not None
            assert (fit.center.value(), fit.raw_scale.value(), fit.branch) == (
                center,
                scale,
                branch,
            )
            family = "deviation"
            dataset, node = result._dataset, result._node.definition
            oracle: dict[str, object] = {
                "center": str(center),
                "scale": str(scale),
                "scores": scores,
            }
        else:
            association_method: Literal["pearson", "spearman", "kendall"] = (
                "pearson"
                if method == "pearson"
                else "spearman"
                if method == "spearman"
                else "kendall"
            )
            expected = coefficient(x, y, method)
            association = a.correlate(b, method=association_method).execute()
            assert association.coefficient.to_pandas().value.tolist() == [expected]
            assert association._dataset is not None
            captured, state_a = decode_pairs(association._dataset.verified().parts)
            assert len(captured.inputs) == 2 and len(state_a.candidates) == 1
            assert state_a.candidates[0].complete_pairs == 4
            family = "association"
            dataset, node = association._dataset, association._node.definition
            oracle = {"coefficient": expected}
        selected = next(
            requirement
            for requirement in descriptor_plan(
                dataset.artifact.descriptor, node
            ).physical_requirements
            if requirement.key.method.name == family + "." + method
        )
        assert (
            isinstance(selected.key.shape, FixedShape) and selected.key.route == "artifact_python"
        )
        assert isinstance(selected.implementation.qualification, Qualified)
        evidence = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
        if evidence:
            Path(evidence, f"retained-{method}-{profile}.json").write_text(
                json.dumps(
                    {
                        "id": f"R9:{family}.{method}@v1:none:retained-input:artifact_python:{profile}",
                        "original_x": [str(value) for value in x],
                        "original_y": [str(value) for value in y],
                        "oracle": oracle,
                        "physical_key": key_json(selected.key),
                        "implementation_id": selected.implementation.qualification.implementation_id,
                        "retained_parts": [part.role for part in dataset.verified().parts],
                        "source_semantic_duckdb_forbidden": True,
                        "boundary": "Public numeric retained-input risk; no cold recovery or other scenario qualification",
                    },
                    sort_keys=True,
                )
            )


@pytest.mark.runtime
@pytest.mark.parametrize("profile", ["int64-near-extremes", "finite-float64", "decimal-exact"])
@pytest.mark.parametrize("method", ["naive", "drift", "seasonal_naive", "runs"])
def test_public_retained_time_numeric_risk(
    profile: Profile,
    method: Literal["naive", "drift", "seasonal_naive", "runs"],
    retained_pair: RetainedPair,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = retained_pair.history
    x = originals(history)
    assert x == retained_pair.x
    with monkeypatch.context() as offline:
        offline.setattr(SourceSession, "__enter__", forbidden)
        offline.setattr(SemanticProject, "load", forbidden)
        offline.setattr(ibis.duckdb, "connect", forbidden)
        if method == "runs":
            zero = Decimal("0.000000") if profile == "decimal-exact" else 0
            result_runs = history.runs(where=history.value.gt(zero)).execute()
            positive = [i for i, value in enumerate(x) if value > 0]
            assert positive == ([1, 2] if profile == "int64-near-extremes" else [0, 1, 2, 3])
            assert result_runs.count.to_pandas().value.tolist() == [len(positive)]
            assert result_runs.duration.to_pandas().value.tolist() == [
                timedelta(days=len(positive))
            ]
            assert result_runs._dataset is not None
            dataset, node = result_runs._dataset, result_runs._node.definition
            captured_runs, state_runs = decode_runs(dataset.verified().parts)
            assert tuple(load(captured_runs.inputs[0])["value"].to_pylist()) == x
            assert state_runs.classifications == tuple(
                "true" if value > 0 else "false" for value in x
            )
            views = load(state_runs.views)
            assert views["input_rows"].to_pylist() == [positive]
            family = "time"
            oracle: dict[str, object] = {
                "positive_ordinals": positive,
                "duration_days": len(positive),
            }
        else:
            model = (
                mv.naive()
                if method == "naive"
                else mv.drift()
                if method == "drift"
                else mv.seasonal_naive(periods=2)
            )
            result = history.forecast(horizon=mv.periods(2), model=model).execute()
            assert result._dataset is not None
            dataset, node = result._dataset, result._node.definition
            captured, state = decode_forecast(dataset.verified().parts)
            assert len(state.series) == 1
            series = state.series[0]
            original = load(captured.input.primary)
            assert tuple(original["value"][i].as_py() for i in series.indices) == x
            values = tuple(map(Fraction, x))
            slope = (values[-1] - values[0]) / 3 if method == "drift" else Fraction()
            distance = 2 if method == "seasonal_naive" else 1
            innovations = tuple(
                values[i] - values[i - distance] - slope for i in range(distance, 4)
            )
            df = 2 if method in {"drift", "seasonal_naive"} else 3
            variance = sum((value * value for value in innovations), Fraction()) / df
            assert series.training.n == 4 and series.training.df == df
            assert tuple(value.value() for value in series.training.innovations) == innovations
            assert series.training.slope.value() == slope
            assert series.training.sigma2.value() == variance
            points = tuple(
                values[-2 + h - 1] if method == "seasonal_naive" else values[-1] + h * slope
                for h in (1, 2)
            )
            variances = tuple(
                variance
                * (
                    1
                    if method == "seasonal_naive"
                    else h * (1 + Fraction(h, 3))
                    if method == "drift"
                    else h
                )
                for h in (1, 2)
            )
            assert tuple(point.point.value() for point in series.points) == points
            assert tuple(point.variance.value() for point in series.points) == variances
            with localcontext(Context(prec=240, rounding=ROUND_HALF_EVEN)):
                point_decimals = tuple(
                    Decimal(point.numerator) / Decimal(point.denominator) for point in points
                )
                expected = [
                    point.quantize(Decimal(".000001"))
                    if profile == "decimal-exact"
                    else float(point)
                    for point in point_decimals
                ]
                assert result.prediction.to_pandas().value.tolist() == expected
                q = Decimal(str(NormalDist().inv_cdf(0.975)))
                widths = tuple(
                    q * (Decimal(v.numerator) / Decimal(v.denominator)).sqrt() for v in variances
                )
                for view, sign in ((result.lower, -1), (result.upper, 1)):
                    for actual, point, width in zip(
                        view.to_pandas().value, point_decimals, widths, strict=True
                    ):
                        expected_endpoint = point + sign * width
                        actual_decimal = (
                            Decimal(actual)
                            if isinstance(actual, Decimal)
                            else Decimal.from_float(float(actual))
                        )
                        tolerance = (
                            Decimal(".000001")
                            if profile == "decimal-exact"
                            else max(abs(point), abs(width), abs(expected_endpoint))
                            * Decimal("1e-14")
                        )
                        assert abs(actual_decimal - expected_endpoint) <= tolerance
            family = "forecast"
            oracle = {
                "training": [str(value) for value in x],
                "points": [str(value) for value in points],
                "variances": [str(value) for value in variances],
            }
        selected = next(
            item
            for item in descriptor_plan(dataset.artifact.descriptor, node).physical_requirements
            if item.key.method.name == family + "." + method
        )
        assert (
            isinstance(selected.key.shape, FixedShape) and selected.key.route == "artifact_python"
        )
        assert isinstance(selected.implementation.qualification, Qualified)
        parts = [part.role for part in dataset.verified().parts]
        assert (
            {"training_inputs", "forecast_state", "future_cells"}
            if family == "forecast"
            else {"condition_cells", "run_cells", "grid_cells"}
        ) <= set(parts)
        evidence = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
        if evidence:
            Path(evidence, f"retained-{method}-{profile}.json").write_text(
                json.dumps(
                    {
                        "id": f"R9:{family}.{method}@v1:none:retained-input:artifact_python:{profile}",
                        "original_x": [str(value) for value in x],
                        "original_y": [],
                        "oracle": oracle,
                        "physical_key": key_json(selected.key),
                        "implementation_id": selected.implementation.qualification.implementation_id,
                        "retained_parts": parts,
                        "source_semantic_duckdb_forbidden": True,
                    },
                    sort_keys=True,
                )
            )
