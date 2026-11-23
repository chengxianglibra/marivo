"""Verify public Shapley laws and compare qualified state and native replacement games."""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from itertools import permutations
from pathlib import Path
from typing import Literal, TypeAlias

import ibis
import pytest

import marivo.analysis as mv
from marivo.analysis.core.frame_method_facts import FrameShapleyMethod
from marivo.analysis.core.frame_shapley_facts import (
    FrameShapleyCapture,
    FrameShapleyGame,
    FrameShapleyQuery,
    FrameShapleyQueryGame,
)
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject
from tests.formal._shapley_axioms import (
    SamplingObservation,
    force_native_admission,
    observe_exhaustive_sampling,
    permutation_marginals,
    standard_error,
    subset_shapley,
)
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import capture_source

_Metric: TypeAlias = Literal["square_x", "square_y", "sum_squares", "ratio", "distinct"]
_Row: TypeAlias = tuple[int, int, int]
_Endpoint: TypeAlias = tuple[_Row, _Row]


@dataclass(frozen=True)
class Group:
    label: str
    alias: str
    baseline: _Endpoint
    current: _Endpoint


def _groups(n: int) -> tuple[Group, ...]:
    baseline = ((1, 1, 1), (2, 2, 1))
    current = ((2, 2, 2), (4, 3, 3))
    return tuple(
        Group(
            chr(65 + index),
            chr(65 + n - index - 1),
            baseline if index < 2 else ((index, 2, index + 5), (3, index, index + 2)),
            current if index < 2 else ((index + 1, 3, 3), (3, index + 2, index + 3)),
        )
        for index in range(n)
    )


def _fixture_groups(n: int) -> tuple[Group, ...]:
    groups = _groups(n)
    if n < 3:
        return groups
    last = groups[-1]
    return (*groups[:-1], Group(last.label, last.alias, last.baseline, last.baseline))


def _project(
    fixture: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    groups: tuple[Group, ...],
) -> Session:
    database = tmp_path / "axioms.duckdb"
    connection = ibis.duckdb.connect(str(database))
    connection.raw_sql(
        "CREATE TABLE facts (id BIGINT, batch BIGINT, player VARCHAR, relabelled VARCHAR, "
        "x BIGINT, y BIGINT, member BIGINT)"
    )
    rows = [
        (batch, group.label, group.alias, x, y, member)
        for group in groups
        for batch, endpoint in enumerate((group.baseline, group.current))
        for x, y, member in endpoint
    ]
    values = ", ".join(repr((index, *row)) for index, row in enumerate(rows))
    connection.raw_sql("INSERT INTO facts VALUES " + values)
    connection.disconnect()
    fixture(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                "md.duckdb(name='warehouse', wall_clock_timezone='UTC', "
                f"path={str(database)!r})\n"
            ),
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Analytics', default=True)\n"
            ),
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
facts = ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table('facts'), primary_key=['id'])
batch = ms.dimension_column(name='batch', entity=facts, column='batch')
player = ms.dimension_column(name='player', entity=facts, column='player')
relabelled = ms.dimension_column(name='relabelled', entity=facts, column='relabelled')
x = ms.measure_column(name='x', entity=facts, column='x', unit='1', additivity=ms.additive_all())
y = ms.measure_column(name='y', entity=facts, column='y', unit='1', additivity=ms.additive_all())
member = ms.measure_column(name='member', entity=facts, column='member', unit='1', additivity=ms.additive_all())
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("axioms", timezone="UTC")


def _game_value(rows: Sequence[_Row], metric: _Metric) -> Fraction:
    x = sum(row[0] for row in rows)
    y = sum(row[1] for row in rows)
    values = {
        "square_x": Fraction(x**2),
        "square_y": Fraction(y**2),
        "sum_squares": Fraction(x**2 + y**2),
        "ratio": Fraction(x**2, y),
        "distinct": Fraction(len({row[2] for row in rows})),
    }
    return values[metric]


def _coalitions(
    players: tuple[str, ...], groups: tuple[Group, ...], metric: _Metric, *, relabel: bool
) -> dict[int, Fraction]:
    directory = {group.alias if relabel else group.label: group for group in groups}
    return {
        mask: _game_value(
            [
                row
                for index, label in enumerate(players)
                for row in (
                    directory[label].current if mask & 2**index else directory[label].baseline
                )
            ],
            metric,
        )
        for mask in range(2 ** len(players))
    }


def _run(
    session: Session,
    metric: _Metric,
    n: int,
    *,
    relabel: bool = False,
) -> mv.AnalysisFrame:
    x = catalog_value(session.catalog, "sales.facts.x", kind="measure").sum()
    y = catalog_value(session.catalog, "sales.facts.y", kind="measure").sum()
    member = catalog_value(session.catalog, "sales.facts.member", kind="measure")
    batch = catalog_value(session.catalog, "sales.facts.batch", kind="dimension")
    by = catalog_value(
        session.catalog, f"sales.facts.{'relabelled' if relabel else 'player'}", kind="dimension"
    )
    formulas = {
        "square_x": x * x,
        "square_y": y * y,
        "sum_squares": x * x + y * y,
        "ratio": (x * x) / y,
        "distinct": member.count_distinct(),
    }
    target = {"value": formulas[metric]}
    current = session.observe(target, where=batch == 1)
    baseline = session.observe(target, where=batch == 0)
    paired = current.compare({"b": mv.baseline(baseline, on=())})
    selected = paired.select({"change": paired["b.absolute.value"]})
    return selected.attribute(by=(by,), permutations=max(2, math.factorial(n)), seed=73).compute()


def _method(report: mv.AnalysisFrame) -> FrameShapleyMethod:
    method = report.method_facts[0].parents[0].metrics[0].method
    assert isinstance(method, FrameShapleyMethod)
    return method


def _close_float(actual: float, expected: Fraction, *, tolerance: float) -> None:
    assert abs(Fraction(actual) - expected) <= Fraction(tolerance), (actual, expected)


def _number(value: object) -> float:
    assert isinstance(value, (int, float)), value
    return float(value)


def _verify_coalition_values(
    observed: SamplingObservation, values: dict[int, Fraction], metric: _Metric
) -> float:
    if metric != "ratio":
        assert observed.coalitions == values
        return 0
    assert observed.coalitions.keys() == values.keys()
    # This bounded ratio performs one Float64 division on exact small integers.
    for mask, expected in values.items():
        assert observed.coalitions[mask] == Fraction(float(expected)), (mask, expected)
    return 3 * math.ulp(float(max(values.values())))


def _verify_paths(
    observed: SamplingObservation, values: dict[int, Fraction], tolerance: float
) -> tuple[tuple[Fraction, ...], ...]:
    n = len(observed.players)
    expected_orders = [(0,), (0,)] if n == 1 else list(permutations(range(n)))
    assert observed.orders == expected_orders
    expected = permutation_marginals(values, observed.orders, n)
    for actual, draws in zip(observed.marginals, expected, strict=True):
        for marginal, reference in zip(actual, draws, strict=True):
            assert abs(Fraction(marginal) - reference) <= Fraction(tolerance), (
                "production permutation marginal",
                observed.players,
                marginal,
                reference,
            )
    change = values[2**n - 1] - values[0]
    for draw in zip(*observed.marginals, strict=True):
        _close_float(math.fsum(draw), change, tolerance=n * tolerance + math.ulp(float(change)))
    return expected


def _verify_report(
    report: mv.AnalysisFrame,
    observed: SamplingObservation,
    groups: tuple[Group, ...],
    metric: _Metric,
    *,
    relabel: bool = False,
) -> dict[str, float]:
    n = len(groups)
    values = _coalitions(observed.players, groups, metric, relabel=relabel)
    tolerance = _verify_coalition_values(observed, values, metric)
    draws = _verify_paths(observed, values, tolerance)
    means = subset_shapley(values, n)
    assert tuple(sum(path) / len(path) for path in draws) == means
    assert sum(means) == values[2**n - 1] - values[0]
    method = _method(report)
    assert (method.permutations, method.seed, method.algorithm_version) == (
        len(observed.orders),
        73,
        "permutation/v1",
    )
    assert (method.player_count, method.unique_coalitions, method.sampled_transitions) == (
        n,
        2**n,
        n * len(observed.orders),
    )
    _close_float(_number(method.left.value), values[2**n - 1], tolerance=tolerance)
    _close_float(_number(method.right.value), values[0], tolerance=tolerance)
    expected_change = values[2**n - 1] - values[0]
    _close_float(_number(method.change.value), expected_change, tolerance=tolerance)
    result = report.to_pandas()
    rows = result.set_index(result.columns[0])
    assert set(rows.index) == set(observed.players)
    contributions = dict(zip(rows.index, map(float, rows["contribution.change"]), strict=True))
    for label, mean, path in zip(observed.players, means, draws, strict=True):
        _close_float(contributions[label], mean, tolerance=tolerance + math.ulp(float(mean)))
        observed_error = _number(rows.loc[label, "contribution_std_error.change"])
        expected_error = standard_error(path, mean)
        assert abs(observed_error - expected_error) <= tolerance + 2 * math.ulp(expected_error)
    for total in rows["total.change"]:
        _close_float(float(total), expected_change, tolerance=tolerance)
    _close_float(
        math.fsum(contributions.values()),
        expected_change,
        tolerance=n * tolerance + sum(math.ulp(float(value)) for value in means),
    )
    return contributions


def _path_by_group(observed: SamplingObservation) -> dict[tuple[str, ...], dict[str, float]]:
    return {
        tuple(observed.players[position] for position in order): dict(
            zip(observed.players, marginals, strict=True)
        )
        for order, marginals in zip(
            observed.orders, zip(*observed.marginals, strict=True), strict=True
        )
    }


def _assert_path_laws(
    samples: dict[_Metric, SamplingObservation], groups: tuple[Group, ...]
) -> None:
    paths = {metric: _path_by_group(sample) for metric, sample in samples.items()}
    for order, summed in paths["sum_squares"].items():
        for group, marginal in summed.items():
            assert marginal == paths["square_x"][order][group] + paths["square_y"][order][group]
    if len(groups) >= 2:
        swap = {"A": "B", "B": "A"}
        for table in paths.values():
            for order, marginals in table.items():
                exchanged = tuple(swap.get(label, label) for label in order)
                assert marginals["A"] == table[exchanged]["B"]
                assert marginals["B"] == table[exchanged]["A"]
        # Both nonlinear fixtures have a nonzero interaction between the two groups.
        for sample in (samples["ratio"], samples["distinct"]):
            values = sample.coalitions
            assert values[3] - values[2] != values[1] - values[0]
    if len(groups) >= 3:
        for table in paths.values():
            assert all(marginals[groups[-1].label] == 0 for marginals in table.values())


def _assert_relabelled_paths(
    original: SamplingObservation, relabelled: SamplingObservation, groups: tuple[Group, ...]
) -> None:
    paths = _path_by_group(relabelled)
    aliases = {group.label: group.alias for group in groups}
    for order, marginals in _path_by_group(original).items():
        relabelled_order = tuple(aliases[group] for group in order)
        assert paths[relabelled_order] == {
            aliases[group]: marginal for group, marginal in marginals.items()
        }


@pytest.mark.parametrize("n", range(1, 6), ids=lambda n: f"n{n}")
def test_public_exhaustive_shapley_axioms(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    n: int,
) -> None:
    groups = _fixture_groups(n)
    session = _project(semantic_project_factory, tmp_path, monkeypatch, groups)
    samples = observe_exhaustive_sampling(monkeypatch)
    formulas: tuple[_Metric, ...] = ("square_x", "square_y", "sum_squares", "ratio", "distinct")
    results = {
        metric: _verify_report(_run(session, metric, n), samples[-1], groups, metric)
        for metric in formulas
    }
    assert len(samples) == len(formulas)
    observed = dict(zip(formulas, samples, strict=True))
    _assert_path_laws(observed, groups)
    for label in results["sum_squares"]:
        assert results["sum_squares"][label] == pytest.approx(
            results["square_x"][label] + results["square_y"][label], rel=0, abs=1e-12
        )
    for contributions in results.values():
        if n >= 2:
            assert contributions["A"] == contributions["B"]
        if n >= 3:
            assert contributions[groups[-1].label] == 0
    # A real relabelled grouping reverses the lexicographic player directory.
    relabelled = _verify_report(
        _run(session, "ratio", n, relabel=True), samples[-1], groups, "ratio", relabel=True
    )
    _assert_relabelled_paths(observed["ratio"], samples[-1], groups)
    for group in groups:
        assert relabelled[group.alias] == results["ratio"][group.label]
    print(f"players={n} coalitions={2**n} orders={len(samples[-1].orders)} formulas={formulas}")


@pytest.mark.parametrize("metric", ("square_x", "distinct"))
def test_qualified_state_and_native_coalitions_are_exactly_equal(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    metric: _Metric,
) -> None:
    n = 3
    groups = _fixture_groups(n)
    session = _project(semantic_project_factory, tmp_path, monkeypatch, groups)
    samples = observe_exhaustive_sampling(monkeypatch)
    trace = capture_source(monkeypatch)
    local_report = _run(session, metric, n)
    local = _method(local_report)
    local_values = _verify_report(local_report, samples[-1], groups, metric)
    local_reads = len(trace.native_sql)
    assert trace.native_sql
    force_native_admission(monkeypatch)
    native_report = _run(session, metric, n)
    native = _method(native_report)
    native_values = _verify_report(native_report, samples[-1], groups, metric)
    assert len(samples) == 2
    assert samples[0] == samples[1]
    assert local_values == native_values
    _assert_route_facts(local, native, metric, trace.native_sql[local_reads:])
    print(
        f"metric={metric} masks={sorted(samples[0].coalitions.items())} "
        f"local={local.state_route} native={native.state_route} source_calls={local_reads}/"
        f"{len(trace.native_sql) - local_reads}"
    )


def _assert_route_facts(
    local: FrameShapleyMethod,
    native: FrameShapleyMethod,
    metric: _Metric,
    sql: list[str],
) -> None:
    assert isinstance(local.game, FrameShapleyGame)
    assert local.game.domain_policy == "finite_interval_demand/v1"
    assert local.game.standard_error == "permutation_mean/v1"
    assert local.game.domain_proof
    assert local.state_route == (
        "typed_value_frequency/v1" if metric == "distinct" else "typed_sufficient_state/v1"
    )
    assert local.state_entries > 0
    assert local.captured_source_reads > 0
    assert all(isinstance(capture, FrameShapleyCapture) for capture in local.captures)
    assert isinstance(native.game, FrameShapleyQueryGame)
    assert native.state_route == "native_queries/v1"
    assert native.game.domain_policy == "evaluated_coalitions/v1"
    assert native.game.standard_error == "observed_marginal_mean/v1"
    assert native.game.input_consistency == "independent_reads/v1"
    assert native.state_entries == 0
    receipts = [
        capture
        for capture in native.captures
        if isinstance(capture, FrameShapleyQuery) and capture.purpose == "coalition"
    ]
    assert len(receipts) == native.unique_coalitions == 8
    assert all(receipt.backend == "duckdb" for receipt in receipts)
    assert sum("UNION ALL" in statement.upper() for statement in sql) == len(receipts)
    assert local.game.metric_approximation == native.game.metric_approximation == "exact_metric/v1"
    assert local.game.current_formula_id == native.game.current_formula_id
    assert local.game.baseline_formula_id == native.game.baseline_formula_id
