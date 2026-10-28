"""Whole-Metric replacement sampling reaches the real source/compiler path."""

import math
import sys
from collections.abc import Callable
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import duckdb
import ibis
import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import capture_source


@pytest.fixture
def game_session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    database = tmp_path / "game.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE facts(id BIGINT, player VARCHAR, x BIGINT, y BIGINT)")
        connection.execute(
            "INSERT INTO facts VALUES (1,'A',1,4),(2,'B',2,3),(3,'A',3,4),(4,'B',2,5)"
        )
        connection.execute("ALTER TABLE facts ADD COLUMN day DATE DEFAULT '2026-07-01'")
        connection.execute("UPDATE facts SET day='2026-08-01' WHERE id>=3")
        connection.execute("ALTER TABLE facts ADD COLUMN region VARCHAR DEFAULT 'east'")
    semantic_project_factory(
        {
            "datasources/warehouse.py": f"import marivo.datasource as md\nmd.duckdb(name='warehouse',path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Analytics',default=True)\n",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
facts=ms.entity(name='facts',datasource=ms.ref.datasource('warehouse'),source=md.table('facts'),primary_key=['id'])
id=ms.dimension_column(name='id',entity=facts,column='id')
player=ms.dimension_column(name='player',entity=facts,column='player')
day=ms.time_dimension_column(name='day',entity=facts,column='day')
region=ms.dimension_column(name='region',entity=facts,column='region')
x=ms.measure_column(name='x',entity=facts,column='x',unit='1',additivity=ms.additive_all())
y=ms.measure_column(name='y',entity=facts,column='y',unit='1',additivity=ms.additive_all())
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("game", timezone="UTC")


def _target(session: Session, kind: str = "product"):
    x = catalog_value(session.catalog, "sales.facts.x", kind="field")
    y = catalog_value(session.catalog, "sales.facts.y", kind="field")
    identifier = catalog_value(session.catalog, "sales.facts.id", kind="field")
    metric = x.sum() * y.sum() if kind == "product" else x.min() * y.max()
    current = session.observe({"q": metric}, where=identifier >= 3)
    baseline = session.observe({"q": metric}, where=identifier < 3)
    compared = current.compare({"b": mv.baseline(baseline, on=())})
    return compared.select({"change": compared["b.absolute.q"]})


def test_product_replacement_sampler_closes_reproduces_and_retains_costs(
    game_session: Session,
) -> None:
    target = _target(game_session)
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    report = target.attribute(by=(player,), permutations=64, seed=42).compute()
    rows = report.to_pandas()
    assert rows["total.change"].tolist() == [24, 24]
    assert sum(rows["contribution.change"]) == pytest.approx(24)
    assert rows["contribution.change"].tolist() == pytest.approx([16, 8], abs=2)
    method = report.method_facts[0].parents[0].metrics[0].method
    assert method.kind == "shapley"
    assert method.permutations == 64 and method.player_count == 2
    assert method.unique_coalitions <= 4 and method.sampled_transitions == 128
    assert rows["contribution_std_error.change"].min() >= 0
    again = target.attribute(by=(player,), permutations=64, seed=42).compute().to_pandas()
    assert again["contribution.change"].tolist() == rows["contribution.change"].tolist()


def test_nonadditive_leaf_states_reuse_original_full_formula(game_session: Session) -> None:
    target = _target(game_session, "extrema")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    rows = target.attribute(by=(player,), permutations=64, seed=9).compute().to_pandas()
    assert sum(rows["contribution.change"]) == pytest.approx(rows["total.change"].iloc[0])


def test_sampling_configuration_and_level_are_rejected_before_execution(
    game_session: Session,
) -> None:
    target = _target(game_session)
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    for config in ({"permutations": True}, {"permutations": 1}, {"seed": False}, {"seed": -1}):
        with pytest.raises(AnalysisError):
            target.attribute(by=(player,), **config)
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    with pytest.raises(AnalysisError):
        game_session.observe({"q": x.min()}).attribute(by=(player,))


def _comparison(session, metrics, *, relative=False, reverse=False):
    identifier = catalog_value(session.catalog, "sales.facts.id", kind="field")
    current = session.observe(metrics, where=identifier >= 3)
    baseline = session.observe(metrics, where=identifier < 3)
    if reverse:
        current, baseline = baseline, current
    compared = current.compare({"b": mv.baseline(baseline, on=())})
    mode = "relative" if relative else "absolute"
    return compared.select({name: compared[f"b.{mode}.{name}"] for name in metrics})


def test_native_quantile_and_numeric_distinct_keep_their_qualified_routes(
    game_session: Session, tmp_path: Path
) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute("ALTER TABLE facts ALTER COLUMN x SET DATA TYPE DECIMAL(20,2)")
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    target = _comparison(game_session, {"q": x.quantile(0.5), "d": x.count_distinct()})
    report = target.attribute(by=(player,), permutations=16, seed=4).compute()
    rows = report.to_pandas()
    assert rows["total.q"].tolist() == [1, 1]
    assert sum(rows["contribution.q"]) == pytest.approx(1)
    assert rows["total.d"].tolist() == [0, 0]
    assert sum(rows["contribution.d"]) == pytest.approx(0)
    methods = tuple(metric for facts in report.method_facts for metric in facts.parents[0].metrics)
    assert all(metric.method.kind == "shapley" for metric in methods)
    assert {m.method.state_route for m in methods} == {
        "native_queries/v1",
        "typed_sufficient_frequency/v1",
    }


def test_compatible_targets_share_capture_and_permutations(game_session: Session) -> None:
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    y = catalog_value(game_session.catalog, "sales.facts.y", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    target = _comparison(game_session, {"first": x.sum() * y.sum(), "second": x.sum() * y.sum()})
    report = target.attribute(by=(player,), permutations=32, seed=12).compute()
    rows = report.to_pandas()
    assert rows["contribution.first"].tolist() == rows["contribution.second"].tolist()
    assert (
        rows["contribution_std_error.first"].tolist()
        == rows["contribution_std_error.second"].tolist()
    )
    a, b = tuple(metric for facts in report.method_facts for metric in facts.parents[0].metrics)
    assert a.method.captures == b.method.captures
    again = target.attribute(by=(player,), permutations=2, seed=12).compute()
    assert (
        a.method.captured_source_reads
        == again.method_facts[0].parents[0].metrics[0].method.captured_source_reads
    )


def test_repeated_occurrences_switch_together_and_reverse_sign(game_session: Session) -> None:
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    metric = x.sum() * x.sum()
    rows = (
        _comparison(game_session, {"q": metric})
        .attribute(by=(player,), permutations=32, seed=8)
        .compute()
        .to_pandas()
    )
    reverse = (
        _comparison(game_session, {"q": metric}, reverse=True)
        .attribute(by=(player,), permutations=32, seed=8)
        .compute()
        .to_pandas()
    )
    assert rows["contribution.q"].tolist() == pytest.approx([16, 0])
    assert reverse["contribution.q"].tolist() == pytest.approx([-16, 0])


@pytest.mark.parametrize("fill", [False, True])
def test_domain_proof_covers_unsampled_empty_mixed_minimum(
    game_session: Session, tmp_path: Path, fill: bool
) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute(
            "UPDATE facts SET x=CASE WHEN id=1 THEN 1 WHEN id=4 THEN 2 ELSE NULL END"
        )
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    y = catalog_value(game_session.catalog, "sales.facts.y", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    minimum = ibis.coalesce(x.min(), 0) if fill else x.min()
    report = _comparison(game_session, {"q": minimum * y.sum()}).attribute(
        by=(player,), permutations=2, seed=3
    )
    if fill:
        rows = report.compute().to_pandas()
        assert sum(rows["contribution.q"]) == pytest.approx(rows["total.q"].iloc[0])
    else:
        with pytest.raises(AnalysisError, match=r"mixed state|replacement|undefined"):
            report.compute()


def test_conditional_demand_proves_only_selected_log_branch(
    game_session: Session, tmp_path: Path
) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute(
            "UPDATE facts SET x=CASE WHEN id=1 THEN -2 WHEN id=2 THEN 1 WHEN id=3 THEN 1 ELSE 2 END"
        )
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    total = x.sum()
    metric = (total > 0).ifelse(total.log(), 0)
    rows = (
        _comparison(game_session, {"q": metric})
        .attribute(by=(player,), permutations=16, seed=1)
        .compute()
        .to_pandas()
    )
    assert sum(rows["contribution.q"]) == pytest.approx(__import__("math").log(3))


def test_weighted_nested_state_uses_covalid_pairs(game_session: Session, tmp_path: Path) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute("UPDATE facts SET x=NULL WHERE id=2")
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    y = catalog_value(game_session.catalog, "sales.facts.y", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    metric = x.weighted_mean(weight=y) * y.sum()
    rows = (
        _comparison(game_session, {"q": metric})
        .attribute(by=(player,), permutations=16, seed=2)
        .compute()
        .to_pandas()
    )
    assert rows["total.q"].tolist() == pytest.approx([15, 15])
    assert sum(rows["contribution.q"]) == pytest.approx(15)


def test_relative_game_normalizes_after_absolute_sampling(game_session: Session) -> None:
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    y = catalog_value(game_session.catalog, "sales.facts.y", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    metric = x.sum() * y.sum()
    absolute = (
        _comparison(game_session, {"q": metric})
        .attribute(by=(player,), permutations=16, seed=2)
        .compute()
        .to_pandas()
    )
    relative = (
        _comparison(game_session, {"q": metric}, relative=True)
        .attribute(by=(player,), permutations=16, seed=2)
        .compute()
        .to_pandas()
    )
    assert relative["contribution.q"].tolist() == pytest.approx(
        [value / 21 for value in absolute["contribution.q"]]
    )
    assert relative["contribution_std_error.q"].tolist() == pytest.approx(
        [value / 21 for value in absolute["contribution_std_error.q"]]
    )


def test_level_and_statically_partitioned_distinct_keep_exact_route(game_session: Session) -> None:
    identifier = catalog_value(game_session.catalog, "sales.facts.id", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    level = (
        game_session.observe({"q": identifier.count_distinct()}).attribute(by=(player,)).compute()
    )
    assert level.method_facts[0].parents[0].metrics[0].method.kind == "level_additive"
    paired = (
        _comparison(game_session, {"q": identifier.count_distinct()})
        .attribute(by=(identifier,))
        .compute()
    )
    assert paired.method_facts[0].parents[0].metrics[0].method.kind == "additive"


def test_strict_null_source_checks_preserve_ordinary_owner_policy(
    game_session: Session, tmp_path: Path
) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute("UPDATE facts SET x=NULL")
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    y = catalog_value(game_session.catalog, "sales.facts.y", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    strict = x.min(nulls="error")
    inactive = ibis.ifelse(y.sum() < 0, strict, 0)
    with pytest.raises(AnalysisError):
        game_session.observe({"q": inactive}).compute()
    with pytest.raises(AnalysisError):
        _comparison(game_session, {"q": inactive}).attribute(by=(player,), permutations=2).compute()
    active = ibis.ifelse(y.sum() > 0, strict, 0)
    with pytest.raises(AnalysisError):
        _comparison(game_session, {"q": active}).attribute(by=(player,), permutations=2).compute()


def test_large_integer_marginal_is_subtracted_before_float64(
    game_session: Session, tmp_path: Path
) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute("UPDATE facts SET x=9007199254740992,y=1 WHERE id=1")
        connection.execute("UPDATE facts SET x=0,y=1 WHERE id IN (2,4)")
        connection.execute("UPDATE facts SET x=9007199254740993,y=1 WHERE id=3")
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    y = catalog_value(game_session.catalog, "sales.facts.y", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    rows = (
        _comparison(game_session, {"q": x.sum() * y.sum()})
        .attribute(by=(player,), permutations=8)
        .compute()
        .to_pandas()
    )
    assert rows["contribution.q"].tolist() == [2, 0]
    assert rows["total.q"].tolist() == [2, 2]


def test_empty_sum_and_present_all_null_are_distinct_in_coalition_states(
    game_session: Session, tmp_path: Path
) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute("UPDATE facts SET x=NULL WHERE id=3")
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    y = catalog_value(game_session.catalog, "sales.facts.y", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    metric = ibis.coalesce(x.sum(), 99) * y.sum()
    rows = (
        _comparison(game_session, {"q": metric})
        .attribute(by=(player,), permutations=8)
        .compute()
        .to_pandas()
    )
    assert sum(rows["contribution.q"]) == pytest.approx(-3)


def test_player_and_null_key_order_is_stable_across_physical_source_order(
    game_session: Session, tmp_path: Path
) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute("UPDATE facts SET player=NULL WHERE player='B'")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    target = _target(game_session)
    first = target.attribute(by=(player,), permutations=16, seed=5).compute().to_pandas()
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute("CREATE TABLE shuffled AS SELECT * FROM facts ORDER BY id DESC")
        connection.execute("DELETE FROM facts")
        connection.execute("INSERT INTO facts SELECT * FROM shuffled")
    second = target.attribute(by=(player,), permutations=16, seed=5).compute().to_pandas()
    assert first["contribution.change"].tolist() == second["contribution.change"].tolist()


def test_mixed_exact_and_game_targets_preserve_bound_rules(game_session: Session) -> None:
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    y = catalog_value(game_session.catalog, "sales.facts.y", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    report = (
        _comparison(game_session, {"sum": x.sum(), "nonlinear": x.sum() * y.sum()})
        .attribute(by=(player,), permutations=8)
        .compute()
    )
    methods = {
        metric.name: metric.method.kind
        for facts in report.method_facts
        for metric in facts.parents[0].metrics
    }
    assert methods == {"sum": "additive", "nonlinear": "shapley"}


def test_sampling_standard_error_matches_independent_two_permutation_game(
    game_session: Session,
) -> None:
    import math

    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    rows = (
        _target(game_session)
        .attribute(by=(player,), permutations=64, seed=42)
        .compute()
        .to_pandas()
    )
    mean = float(rows["contribution.change"].iloc[0])
    second_count = round((mean - 14) * 64 / 4)
    assert 0 <= second_count <= 64
    assert mean == pytest.approx((second_count * 18 + (64 - second_count) * 14) / 64)
    expected = math.sqrt(
        (second_count * (18 - mean) ** 2 + (64 - second_count) * (14 - mean) ** 2) / (64 * 63)
    )
    assert rows["contribution_std_error.change"].tolist() == pytest.approx([expected, expected])


def test_positive_dummy_player_has_zero_mean_and_zero_sampling_error(
    game_session: Session, tmp_path: Path
) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute("UPDATE facts SET x=3,y=4 WHERE player='A'")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    rows = (
        _target(game_session).attribute(by=(player,), permutations=16, seed=9).compute().to_pandas()
    )
    assert rows["contribution.change"].iloc[0] == 0
    assert rows["contribution_std_error.change"].iloc[0] == 0


def test_quantile_nested_in_sum_product_uses_whole_game_and_mixed_capture_facts(
    game_session: Session,
    tmp_path: Path,
) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute("ALTER TABLE facts ALTER COLUMN y SET DATA TYPE DECIMAL(20,2)")
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    y = catalog_value(game_session.catalog, "sales.facts.y", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    report = (
        _comparison(game_session, {"q": x.sum() * y.quantile(0.5)})
        .attribute(by=(player,), permutations=8)
        .compute()
    )
    rows = report.to_pandas()
    assert sum(rows["contribution.q"]) == pytest.approx(12)
    method = report.method_facts[0].parents[0].metrics[0].method
    assert method.state_route == "native_queries/v1"
    assert method.captured_source_reads == len(method.captures)
    assert all(leaf.current_capture_ids and leaf.baseline_capture_ids for leaf in method.leaves)


def test_conditional_nonzero_guard_is_proved_for_dynamic_denominator(
    game_session: Session, tmp_path: Path
) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute(
            "UPDATE facts SET x=CASE WHEN id=1 THEN -2 WHEN id=2 THEN 1 WHEN id=3 THEN 1 ELSE 2 END"
        )
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    value = x.sum()
    metric = ibis.ifelse(value != 0, 1 / value, 0)
    rows = (
        _comparison(game_session, {"q": metric})
        .attribute(by=(player,), permutations=8)
        .compute()
        .to_pandas()
    )
    assert sum(rows["contribution.q"]) == pytest.approx(4 / 3)


@pytest.mark.parametrize("dtype", ["BIGINT", "DECIMAL(20,2)", "DOUBLE", "UBIGINT"])
def test_mean_games_reconstruct_typed_sum_count_and_disclose_bias(
    game_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dtype: str
) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute(f"ALTER TABLE facts ALTER COLUMN x SET DATA TYPE {dtype}")
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    ordinary = game_session.observe({"mean": x.mean()}).compute().to_pandas()
    assert ordinary["mean"].tolist() == [2]
    target = _comparison(game_session, {"q": x.mean() * x.count()}).compute()
    trace = capture_source(monkeypatch)
    report = target.attribute(by=(player,), permutations=8).compute()
    rows = report.to_pandas()
    assert rows["total.q"].tolist() == [2, 2]
    assert sum(rows["contribution.q"]) == pytest.approx(2)
    method = report.method_facts[0].parents[0].metrics[0].method
    assert method.state_route == "typed_sufficient_state/v1"
    assert method.game.domain_policy == "finite_interval_demand/v1"
    assert method.game.standard_error == "permutation_mean/v1"
    assert method.game.metric_approximation == "summary_reconstruction/v1"
    assert trace.native_sql


@pytest.mark.parametrize(
    "value",
    [
        sys.float_info.max,
        math.nextafter(math.nextafter(sys.float_info.max, 0), 0),
        -sys.float_info.max,
    ],
)
def test_mean_summary_bound_overflow_rejects_with_structured_domain_error(
    game_session: Session, tmp_path: Path, value: float
) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute("ALTER TABLE facts ALTER COLUMN x SET DATA TYPE DOUBLE")
        connection.execute("DELETE FROM facts")
        connection.execute(
            "INSERT INTO facts(id,player,x,y) VALUES (1,'A',?,1),(3,'A',?,1)",
            [value, value],
        )
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    target = _comparison(game_session, {"q": (x.mean() > 0).ifelse(1, 0)}).compute()
    assert target.to_pandas()["q"].tolist() == [0]
    with pytest.raises(AnalysisError) as error:
        target.attribute(by=(player,), permutations=2).compute()
    assert error.value.reason == "incomplete_domain"


@pytest.mark.parametrize("calendar", [False, True])
@pytest.mark.parametrize("native", [False, True])
def test_interval_and_calendar_games_reuse_frozen_endpoint_correspondence(
    game_session: Session, calendar: bool, native: bool
) -> None:
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    y = catalog_value(game_session.catalog, "sales.facts.y", kind="field")
    day = catalog_value(game_session.catalog, "sales.facts.day", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    current_scope = game_session.time_scope(start="2026-08-01", end="2026-08-02")
    baseline_scope = game_session.time_scope(start="2026-07-01", end="2026-07-02")
    metric = (x.mean(time=day) if native else x.sum(time=day)) * y.sum(time=day)
    current = game_session.observe(
        {"q": metric}, by=(current_scope.by(mv.grain("day")) if calendar else current_scope,)
    )
    baseline = game_session.observe(
        {"q": metric}, by=(baseline_scope.by(mv.grain("day")) if calendar else baseline_scope,)
    )
    compared = current.compare(
        {
            "b": mv.baseline(
                baseline,
                on=tuple(zip(current.axes, baseline.axes, strict=True)),
                match=mv.ordinal(),
            )
        }
    ).compute()
    target = compared.select({"q": compared["b.absolute.q"]}).compute()
    rows = target.attribute(by=(player,), permutations=8).compute().to_pandas()
    expected = 12 if native else 24
    assert rows["total.q"].tolist() == [expected, expected]
    assert sum(rows["contribution.q"]) == pytest.approx(expected)


def test_frozen_parent_selection_keeps_old_observation_and_fresh_state_total(
    game_session: Session, tmp_path: Path
) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute(
            "INSERT INTO facts VALUES (11,'A',10,2,'2026-07-01','west'),(12,'B',20,3,'2026-07-01','west'),(21,'A',10,2,'2026-08-01','west'),(22,'B',20,3,'2026-08-01','west')"
        )
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    y = catalog_value(game_session.catalog, "sales.facts.y", kind="field")
    day = catalog_value(game_session.catalog, "sales.facts.day", kind="field")
    region = catalog_value(game_session.catalog, "sales.facts.region", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    metric = x.sum() * y.sum()
    current = game_session.observe({"q": metric}, by=(region,), where=day >= "2026-08-01")
    baseline = game_session.observe({"q": metric}, by=(region,), where=day < "2026-08-01")
    compared = current.compare(
        {"b": mv.baseline(baseline, on=tuple(zip(current.axes, baseline.axes, strict=True)))}
    ).compute()
    selected = compared.where(compared["b.absolute.q"] > 0)
    target = selected.select({"q": selected["b.absolute.q"]}).compute()
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute("UPDATE facts SET x=4 WHERE id=3")
        connection.execute("UPDATE facts SET x=1000 WHERE id=21")
    rows = target.attribute(by=(player,), permutations=8).compute().to_pandas()
    assert rows.iloc[:, 0].tolist() == ["east", "east"]
    assert rows["observed_total.q"].tolist() == [24, 24]
    assert rows["total.q"].tolist() == [33, 33]


def test_native_duckdb_interpolating_quantile_uses_original_native_queries(
    game_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    target = _comparison(game_session, {"q": x.quantile(0.5)}).compute()
    trace = capture_source(monkeypatch)
    report = target.attribute(by=(player,), permutations=8).compute()
    rows = report.to_pandas()
    assert rows["total.q"].tolist() == [1, 1]
    assert sum(rows["contribution.q"]) == pytest.approx(1)
    method = report.method_facts[0].parents[0].metrics[0].method
    assert method.state_route == "native_queries/v1"
    assert method.leaves[0].reduction.kind == "native_quantile"
    assert method.leaves[0].reduction.reducer == "quantile"
    assert trace.native_sql


@pytest.mark.parametrize("keys", [(("A",),), (("A",), ("B",))])
def test_population_game_requires_qualified_coverage_before_business_reads(
    game_session: Session, monkeypatch: pytest.MonkeyPatch, keys
) -> None:
    import marivo.analysis.materialization.graph_source_execution as execution

    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    target = _target(game_session).compute()
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("business reads for an unqualified Population game")

    monkeypatch.setattr(execution, "read_stage", forbidden)
    domain = mv.coordinates(by=(player,), keys=keys)
    with pytest.raises(AnalysisError, match="no qualified replacement-game coverage receipt"):
        target.attribute(by=domain, permutations=2)
    assert calls == []


def test_unused_and_coalesced_zero_weight_state_respect_metric_demand(
    game_session: Session, tmp_path: Path
) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute("UPDATE facts SET y=0")
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    y = catalog_value(game_session.catalog, "sales.facts.y", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    weighted = x.weighted_mean(weight=y)
    metrics = {
        "unused": ibis.ifelse(y.sum() > 0, weighted, 0),
        "filled": ibis.coalesce(weighted, 0) * x.sum(),
    }
    report = _comparison(game_session, metrics).attribute(by=(player,), permutations=4).compute()
    rows = report.to_pandas()
    assert rows["contribution.unused"].tolist() == [0, 0]
    assert rows["contribution.filled"].tolist() == [0, 0]


@pytest.mark.parametrize("seed", [0, 1, 7])
def test_all_coalition_proof_rejects_unsampled_exponential_overflow(
    game_session: Session, tmp_path: Path, seed: int
) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute("UPDATE facts SET x=CASE WHEN id IN (1,4) THEN 600 ELSE -600 END")
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    target = _comparison(game_session, {"q": x.sum().exp()}).compute()
    with pytest.raises(AnalysisError) as error:
        target.attribute(by=(player,), permutations=2, seed=seed).compute()
    assert error.value.reason == "incomplete_domain"


def test_float_sum_game_discloses_reconstruction_changing_original_condition(
    game_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute("ALTER TABLE facts ALTER COLUMN x SET DATA TYPE DOUBLE")
        connection.execute("DELETE FROM facts")
        connection.execute(
            "INSERT INTO facts(id,player,x,y) VALUES "
            "(1,'A',1e16,1),(2,'B',1,1),(0,'A',-1e16,1),"
            "(3,'A',1e16,1),(4,'B',2,1),(5,'A',-1e16,1)"
        )
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    metric = (x.sum() > 0).ifelse(1, 0)
    # Native accumulation gives baseline zero and current two. Regrouping by
    # player instead gives baseline one, changing the original branch.
    target = _comparison(game_session, {"q": metric}).compute()
    assert target.to_pandas()["q"].tolist() == [1]
    trace = capture_source(monkeypatch)
    report = target.attribute(by=(player,), permutations=8, seed=0).compute()
    rows = report.to_pandas()
    assert rows["total.q"].tolist() == [0, 0]
    assert sum(rows["contribution.q"]) == pytest.approx(0)
    assert (
        report.method_facts[0].parents[0].metrics[0].method.state_route
        == "typed_sufficient_state/v1"
    )
    assert rows["observed_total.q"].tolist() == [1, 1]
    assert (
        report.method_facts[0].parents[0].metrics[0].method.game.metric_approximation
        == "summary_reconstruction/v1"
    )
    assert trace.native_sql


@pytest.mark.parametrize("floating_operand", ["x", "y"])
def test_float_weighted_game_uses_summary_components(
    game_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    floating_operand: str,
) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute(
            f"ALTER TABLE facts ALTER COLUMN {floating_operand} SET DATA TYPE DOUBLE"
        )
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    y = catalog_value(game_session.catalog, "sales.facts.y", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    target = _comparison(game_session, {"q": x.weighted_mean(weight=y) * x.count()})
    assert target.compute().to_pandas()["q"].iloc[0] > 0
    trace = capture_source(monkeypatch)
    report = target.attribute(by=(player,), permutations=8).compute()
    rows = report.to_pandas()
    assert rows["total.q"].tolist() == pytest.approx([128 / 63, 128 / 63])
    assert sum(rows["contribution.q"]) == pytest.approx(128 / 63)
    assert (
        report.method_facts[0].parents[0].metrics[0].method.state_route
        == "typed_sufficient_state/v1"
    )
    assert (
        report.method_facts[0].parents[0].metrics[0].method.game.metric_approximation
        == "summary_reconstruction/v1"
    )
    assert trace.native_sql


def test_float_extrema_and_counts_keep_their_qualified_game_routes(
    game_session: Session, tmp_path: Path
) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute("ALTER TABLE facts ALTER COLUMN x SET DATA TYPE DOUBLE")
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    y = catalog_value(game_session.catalog, "sales.facts.y", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    rows = (
        _comparison(game_session, {"extrema": x.min() * x.max(), "count": x.count() * y.sum()})
        .attribute(by=(player,), permutations=8)
        .compute()
        .to_pandas()
    )
    assert rows["total.extrema"].tolist() == [4, 4]
    assert rows["total.count"].tolist() == [4, 4]


def test_weighted_game_casts_each_exact_sum_before_original_condition(
    game_session: Session, tmp_path: Path
) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute("ALTER TABLE facts ALTER COLUMN x SET DATA TYPE DECIMAL(20,2)")
        connection.execute("DELETE FROM facts")
        connection.execute(
            "INSERT INTO facts(id,player,x,y) VALUES "
            "(1,'A',900719925474099.31,1),(2,'B',0,1),(0,'C',0,1),"
            "(3,'A',900719925474100.31,1),(4,'B',0,1),(5,'C',0,1)"
        )
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    y = catalog_value(game_session.catalog, "sales.facts.y", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    # Float64(numerator) / Float64(3) equals the threshold at baseline and
    # exceeds it at current. Decimal division followed by Float64 does not.
    metric = (x.weighted_mean(weight=y) > 300239975158033.06).ifelse(1, 0)
    target = _comparison(game_session, {"q": metric}).compute()
    assert target.to_pandas()["q"].tolist() == [1]
    rows = target.attribute(by=(player,), permutations=8, seed=0).compute().to_pandas()
    assert rows["total.q"].tolist() == [1, 1, 1]
    assert rows["contribution.q"].tolist() == [1, 0, 0]


def test_decimal_weight_cancellation_preserves_every_native_carrier_digit(
    game_session: Session, tmp_path: Path
) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute("ALTER TABLE facts ALTER COLUMN y SET DATA TYPE DECIMAL(38,0)")
        connection.execute("DELETE FROM facts")
        connection.execute(
            "INSERT INTO facts(id,player,x,y) VALUES "
            "(1,'A',1,10000000000000000000000000000),(2,'B',1,1),"
            "(0,'C',1,-10000000000000000000000000000),"
            "(3,'A',2,10000000000000000000000000000),(4,'B',2,1),"
            "(5,'C',2,-10000000000000000000000000000)"
        )
        source = connection.execute(
            "SELECT id>=3, sum(x*y), sum(y) FROM facts GROUP BY 1 ORDER BY 1"
        ).fetchall()
    assert source == [(False, Decimal(1), Decimal(1)), (True, Decimal(2), Decimal(1))]
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    y = catalog_value(game_session.catalog, "sales.facts.y", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    metric = x.weighted_mean(weight=y) * x.count()
    target = _comparison(game_session, {"q": metric}).compute()
    assert target.to_pandas()["q"].tolist() == [3]
    rows = target.attribute(by=(player,), permutations=8, seed=0).compute().to_pandas()
    assert rows["total.q"].tolist() == [3, 3, 3]
    assert all(math.isfinite(value) for value in rows["contribution.q"])


def test_finite_exponential_marginals_have_finite_scaled_standard_errors(
    game_session: Session, tmp_path: Path
) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute("UPDATE facts SET x=CASE WHEN id<3 THEN 0 ELSE 200 END")
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    target = _comparison(game_session, {"q": x.sum().exp()}).compute()
    rows = target.attribute(by=(player,), permutations=8, seed=0).compute().to_pandas()
    scale = math.exp(400)
    # The two possible marginal values, normalized before squaring, avoid
    # overflowing the independent sample-variance oracle.
    low = (math.exp(200) - 1) / scale
    high = (scale - math.exp(200)) / scale
    for mean, error in zip(rows["contribution.q"], rows["contribution_std_error.q"], strict=True):
        assert math.isfinite(mean)
        normalized = mean / scale
        high_count = round((normalized - low) * 8 / (high - low))
        assert 0 <= high_count <= 8
        assert normalized == pytest.approx((high_count * high + (8 - high_count) * low) / 8)
        expected = scale * math.sqrt(
            (high_count * (high - normalized) ** 2 + (8 - high_count) * (low - normalized) ** 2)
            / (8 * 7)
        )
        assert math.isfinite(error)
        assert error == pytest.approx(expected)
    assert sum(rows["contribution.q"]) == pytest.approx(scale - 1)


def test_float_sum_qualification_uses_bound_row_recipe_type(
    game_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    y = catalog_value(game_session.catalog, "sales.facts.y", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    # Both physical measures are Int64, while the consumed row recipe is Float64.
    target = _comparison(game_session, {"q": (x * 0.5).sum() * y.sum()}).compute()
    assert target.to_pandas()["q"].tolist() == [12]
    trace = capture_source(monkeypatch)
    report = target.attribute(by=(player,), permutations=8).compute()
    rows = report.to_pandas()
    assert rows["total.q"].tolist() == [12, 12]
    assert sum(rows["contribution.q"]) == pytest.approx(12)
    assert (
        report.method_facts[0].parents[0].metrics[0].method.state_route
        == "typed_sufficient_state/v1"
    )
    assert trace.native_sql


def test_native_and_state_targets_share_inputs_without_changing_qualified_state_route(
    game_session: Session,
) -> None:
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    y = catalog_value(game_session.catalog, "sales.facts.y", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    report = (
        _comparison(game_session, {"native": x.quantile(0.5) * y.sum(), "state": x.min() * y.max()})
        .attribute(by=(player,), permutations=16, seed=7)
        .compute()
    )
    rows = report.to_pandas()
    assert rows["total.native"].tolist() == [12, 12]
    assert rows["total.state"].tolist() == [6, 6]
    methods = {
        metric.target: metric.method
        for fact in report.method_facts
        for parent in fact.parents
        for metric in parent.metrics
    }
    assert methods[("native",)].state_route == "native_queries/v1"
    assert methods[("state",)].state_route == "typed_sufficient_state/v1"
    assert methods[("native",)].game.standard_error == "observed_marginal_mean/v1"
    assert methods[("state",)].game.standard_error == "permutation_mean/v1"


def test_native_coalition_queries_are_cached_and_costs_follow_unique_masks(
    game_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute("INSERT INTO facts(id,player,x,y) VALUES (0,'C',1,1),(5,'C',4,2)")
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    target = _comparison(game_session, {"q": x.quantile(0.5) * x.count()}).compute()
    trace = capture_source(monkeypatch)
    reports = [
        target.attribute(by=(player,), permutations=count, seed=7).compute() for count in (2, 64)
    ]
    methods = [report.method_facts[0].parents[0].metrics[0].method for report in reports]
    assert methods[0].unique_coalitions <= 6
    assert methods[1].unique_coalitions == 8
    for method in methods:
        assert method.coalition_evaluations == method.unique_coalitions
        assert method.state_entries == 0
        coalition_reads = sum(
            item.source_reads for item in method.captures if item.purpose == "coalition"
        )
        assert coalition_reads == 2 * method.unique_coalitions
        assert method.captured_source_reads == sum(item.source_reads for item in method.captures)
    assert methods[0].captured_source_reads < methods[1].captured_source_reads
    assert len(trace.native_sql) == sum(method.captured_source_reads for method in methods)


def test_native_game_preserves_null_parent_and_player_keys(
    game_session: Session, tmp_path: Path
) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute(
            "UPDATE facts SET region=NULL, player=CASE WHEN id IN (2,4) THEN NULL ELSE player END"
        )
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    region = catalog_value(game_session.catalog, "sales.facts.region", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    identifier = catalog_value(game_session.catalog, "sales.facts.id", kind="field")
    metric = x.quantile(0.5) * x.count()
    current = game_session.observe({"q": metric}, by=(region,), where=identifier >= 3)
    baseline = game_session.observe({"q": metric}, by=(region,), where=identifier < 3)
    compared = current.compare(
        {"b": mv.baseline(baseline, on=tuple(zip(current.axes, baseline.axes, strict=True)))}
    )
    target = compared.select({"q": compared["b.absolute.q"]}).compute()
    report = target.attribute(by=(player,), permutations=16, seed=7).compute()
    rows = report.to_pandas()
    assert rows.iloc[:, 0].isna().all()
    assert rows.iloc[:, 1].isna().sum() == 1
    assert rows["total.q"].tolist() == [2, 2]
    assert sum(rows["contribution.q"]) == pytest.approx(2)


def test_native_approximate_quantile_keeps_original_backend_algorithm(
    game_session: Session, tmp_path: Path
) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        current = connection.execute(
            "SELECT approx_quantile(CAST(x AS DOUBLE),0.5) FROM facts WHERE id>=3"
        ).fetchone()[0]
        baseline = connection.execute(
            "SELECT approx_quantile(CAST(x AS DOUBLE),0.5) FROM facts WHERE id<3"
        ).fetchone()[0]
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    target = _comparison(game_session, {"q": x.approx_quantile(0.5)}).compute()
    report = target.attribute(by=(player,), permutations=16, seed=7).compute()
    rows = report.to_pandas()
    assert rows["total.q"].tolist() == [current - baseline, current - baseline]
    assert sum(rows["contribution.q"]) == pytest.approx(current - baseline)
    method = report.method_facts[0].parents[0].metrics[0].method
    assert method.game.metric_approximation == "native_approximate_metric/v1"
    assert method.leaves[0].reduction.reducer == "approx_quantile"
    assert method.leaves[0].reduction.algorithm_version == "original_native_reducer/v1"


def test_native_demanded_empty_coalition_rejects_complete_call(
    game_session: Session, tmp_path: Path
) -> None:
    with duckdb.connect(str(tmp_path / "game.duckdb")) as connection:
        connection.execute("DELETE FROM facts WHERE id IN (2,3)")
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    target = _comparison(game_session, {"q": x.quantile(0.5) * x.count()}).compute()
    with pytest.raises(AnalysisError, match="undefined") as error:
        target.attribute(by=(player,), permutations=64, seed=7).compute()
    assert error.value.reason == "invalid_value"


def test_native_reads_can_change_between_queries_while_players_and_endpoints_stay_cached(
    game_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.analysis.materialization.graph_local_execution as local_execution
    from marivo.datasource.adapters import SourceSession
    from marivo.datasource.runtime import DatasourceConnectionService

    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    target = _comparison(game_session, {"q": x.quantile(0.5) * x.count()}).compute()
    open_backend = DatasourceConnectionService.use_backend

    @contextmanager
    def writable_fixture(service, name, **kwargs):
        # This test permits an independent fixture writer while real native
        # reducers run. Production still uses its ordinary read-only connections.
        kwargs["read_only"] = False
        with open_backend(service, name, **kwargs) as backend:
            yield backend

    monkeypatch.setattr(DatasourceConnectionService, "use_backend", writable_fixture)
    execute, run_local = SourceSession.execute, local_execution.run_local
    local_active = False
    native_queries: list[str] = []

    def checked_local(*args, **kwargs):
        nonlocal local_active
        local_active = True
        try:
            return run_local(*args, **kwargs)
        finally:
            local_active = False

    def changing_source(owner, read):
        assert not local_active, "local Frame/Query kernels must never initiate source I/O"
        result = execute(owner, read)
        if ":native:" in read.purpose:
            native_queries.append(read.purpose)
            if len(native_queries) == 1:
                with duckdb.connect(
                    str(tmp_path / "game.duckdb"), config={"threads": 1, "TimeZone": "UTC"}
                ) as connection:
                    connection.execute("UPDATE facts SET x=x+10 WHERE id>=3")
                    connection.execute("INSERT INTO facts(id,player,x,y) VALUES (6,'new',1000,1)")
        return result

    monkeypatch.setattr(local_execution, "run_local", checked_local)
    monkeypatch.setattr(SourceSession, "execute", changing_source)
    report = target.attribute(by=(player,), permutations=64, seed=7).compute()
    rows = report.to_pandas()
    assert rows.iloc[:, 0].tolist() == ["A", "B"]
    assert rows["observed_total.q"].tolist() == [2, 2]
    assert rows["total.q"].tolist() == [22, 22]
    assert sum(rows["contribution.q"]) == pytest.approx(22)
    method = report.method_facts[0].parents[0].metrics[0].method
    assert method.left.value == 25 and method.right.value == 3
    assert method.game.input_consistency == "independent_reads/v1"
    assert len(native_queries) == len(set(native_queries)) == 8


def test_compatible_native_targets_reuse_leaf_queries_and_permutation_stream(
    game_session: Session,
) -> None:
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    metric = x.quantile(0.5) * x.count()
    target = _comparison(game_session, {"a": metric, "b": metric}).compute()
    report = target.attribute(by=(player,), permutations=64, seed=7).compute()
    rows = report.to_pandas()
    assert rows["contribution.a"].tolist() == rows["contribution.b"].tolist()
    assert rows["contribution_std_error.a"].tolist() == rows["contribution_std_error.b"].tolist()
    methods = [
        metric.method
        for fact in report.method_facts
        for parent in fact.parents
        for metric in parent.metrics
    ]
    coalition_ids = [
        {item.capture_id for item in method.captures if item.purpose == "coalition"}
        for method in methods
    ]
    assert len(coalition_ids[0]) == 8
    assert coalition_ids[0] == coalition_ids[1]
