"""Independent numerical witnesses for the accepted contribution examples."""

from collections.abc import Callable
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
from marivo._grouping import Grouping
from marivo.analysis.core.attribute import AttributeMethodPayload, AttributeMethodProduction
from marivo.analysis.core.data_schema import DataSchema
from marivo.analysis.core.data_types import DataColumn, DecimalDataType, ScalarDataType
from marivo.analysis.core.frame_method_facts import FrameRatioMethod
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.dsl.population import Population
from marivo.analysis.materialization import frame_evaluation
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject


def _contribution(
    current: AnalysisFrame,
    baseline: AnalysisFrame,
    *,
    by: tuple[Deferred | Grouping, ...] | Population,
) -> AnalysisFrame:
    compared = current.compare({"base": baseline})
    selected = compared.select(
        {name: compared[f"base.absolute.{name}"] for name in current.output_names}
    )
    return selected.attribute(by=by)


@pytest.fixture
def session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    database = tmp_path / "oracles.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE facts(id BIGINT, phase VARCHAR, region VARCHAR, channel VARCHAR, "
            "amount BIGINT, weight BIGINT, gross DECIMAL(18,2), cost DECIMAL(18,2), counted BIGINT)"
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + f"md.duckdb(name='warehouse', path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\n"
            "ms.domain(name='sales', owner='Analytics', default=True)\n",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
facts = ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table('facts'), primary_key=['id'])
phase = ms.dimension_column(name='phase', entity=facts, column='phase')
region = ms.dimension_column(name='region', entity=facts, column='region')
channel = ms.dimension_column(name='channel', entity=facts, column='channel')
amount = ms.measure_column(name='amount', entity=facts, column='amount', unit='USD', additivity=ms.additive_all())
weight = ms.measure_column(name='weight', entity=facts, column='weight', unit='1', additivity=ms.additive_all())
gross = ms.measure_column(name='gross', entity=facts, column='gross', unit='USD', additivity=ms.additive_all())
cost = ms.measure_column(name='cost', entity=facts, column='cost', unit='USD', additivity=ms.additive_all())
counted = ms.measure_column(name='counted', entity=facts, column='counted', unit='1', additivity=ms.additive_all())
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.create("oracles", timezone="UTC")


def _columns(frame: AnalysisFrame) -> dict[str, DataColumn]:
    assert isinstance(frame._schema, DataSchema)
    return {column.name: column for column in frame._schema.values}


def _present(rows: pd.DataFrame) -> None:
    assert rows[("presence", "detail.current.present.q")].tolist() == [True] * len(rows)
    assert rows[("presence", "detail.baseline.present.q")].tolist() == [True] * len(rows)


@pytest.fixture
def productions(monkeypatch: pytest.MonkeyPatch) -> list[AttributeMethodProduction]:
    captured: list[AttributeMethodProduction] = []
    original = frame_evaluation._attribute_productions

    def capture(
        associations: tuple[tuple[str, str, int, bool], ...],
        reachable: frozenset[str],
        observed: tuple[tuple[str, AttributeMethodPayload], ...],
    ) -> tuple[AttributeMethodProduction, ...]:
        values = original(associations, reachable, observed)
        captured.extend(values)
        return values

    monkeypatch.setattr(frame_evaluation, "_attribute_productions", capture)
    return captured


def test_fraction_b_subgroups_use_global_or_fresh_local_population(
    session: Session, tmp_path: Path, productions: list[AttributeMethodProduction]
) -> None:
    with duckdb.connect(str(tmp_path / "oracles.duckdb")) as connection:
        connection.execute(
            "INSERT INTO facts(id,phase,region,channel,amount,weight) VALUES "
            "(1,'base','A','A',1,10),(2,'base','B','B1',4,40),"
            "(3,'base','B','B2',5,50),(4,'current','A','A',2,10),"
            "(5,'current','B','B1',3,90),(6,'current','B','B2',5,100)"
        )
        totals = connection.execute(
            "SELECT phase,SUM(amount),SUM(weight) FROM facts GROUP BY phase ORDER BY phase"
        ).fetchall()
    assert totals == [("base", 10, 100), ("current", 10, 200)]
    assert Fraction(totals[0][1], totals[0][2]) == Fraction(1, 10)
    assert Fraction(totals[1][1], totals[1][2]) == Fraction(1, 20)
    amount = session.catalog.fields.get("sales.facts.amount")
    weight = session.catalog.fields.get("sales.facts.weight")
    phase = session.catalog.fields.get("sales.facts.phase")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    metric = amount.sum() / weight.sum()
    global_result = _contribution(
        session.observe({"q": metric}, where=phase == "current"),
        session.observe({"q": metric}, where=phase == "base"),
        by=(region, channel),
    ).compute()
    local_result = _contribution(
        session.observe({"q": metric}, where=(phase == "current") & (region == "B")),
        session.observe({"q": metric}, where=(phase == "base") & (region == "B")),
        by=(channel,),
    ).compute()
    global_rows = global_result.to_pandas()
    local_rows = local_result.to_pandas()
    assert list(global_rows.iloc[:, :2].itertuples(index=False, name=None)) == [
        ("A", "A"),
        ("B", "B1"),
        ("B", "B2"),
    ]
    assert local_rows.iloc[:, 0].tolist() == ["B1", "B2"]
    assert global_rows[("value", "contribution.q")].tolist() == pytest.approx(
        [0, float(Fraction(-1, 40)), float(Fraction(-1, 40))]
    )
    assert global_rows[("value", "contribution_share.q")].tolist() == [0, 0.5, 0.5]
    assert local_rows[("value", "contribution.q")].tolist() == pytest.approx(
        [float(Fraction(-49, 1710)), float(Fraction(-5, 171))]
    )
    assert local_rows[("value", "contribution_share.q")].tolist() == pytest.approx(
        [float(Fraction(49, 99)), float(Fraction(50, 99))]
    )
    original_left = [Fraction(2, 10), Fraction(3, 90), Fraction(5, 100)]
    original_right = [Fraction(1, 10), Fraction(4, 40), Fraction(5, 50)]
    for frame, rows, start in (
        (global_result, global_rows, 0),
        (local_result, local_rows, 1),
    ):
        assert rows[("value", "detail.current.q")].tolist() == pytest.approx(
            [float(value) for value in original_left[start:]]
        )
        assert rows[("value", "detail.baseline.q")].tolist() == pytest.approx(
            [float(value) for value in original_right[start:]]
        )
        assert rows[("value", "detail.absolute.q")].tolist() == pytest.approx(
            [
                float(left - right)
                for left, right in zip(original_left[start:], original_right[start:], strict=True)
            ]
        )
        columns = _columns(frame)
        assert frame.output_names == (
            "contribution.q",
            "total.q",
            "observed_total.q",
            "contribution_share.q",
            "detail.current.q",
            "detail.baseline.q",
            "detail.absolute.q",
            "component.current.numerator.q",
            "component.current.denominator.q",
            "component.baseline.numerator.q",
            "component.baseline.denominator.q",
        )
        for path, expected, unit in (
            ("component.current.numerator.q", [2, 3, 5], "USD"),
            ("component.baseline.numerator.q", [1, 4, 5], "USD"),
            ("component.current.denominator.q", [10, 90, 100], "1"),
            ("component.baseline.denominator.q", [10, 40, 50], "1"),
        ):
            assert rows[("value", path)].tolist() == expected[start:]
            assert columns[path].dtype == DecimalDataType(38, 0)
            assert columns[path].unit == unit
        assert columns["contribution.q"].unit == "USD"
        assert columns["contribution_share.q"].unit == "1"
        _present(rows)
    # The local root is 8/190, compared with 9/90; it differs from the global root.
    assert Fraction(8, 190) - Fraction(9, 90) == Fraction(-11, 190)
    assert sum(local_rows[("value", "contribution.q")]) == pytest.approx(float(Fraction(-11, 190)))
    assert sum(global_rows[("value", "contribution.q")]) == pytest.approx(float(Fraction(-1, 20)))
    assert len(productions) == 2
    for production, left, right, denominator_left, denominator_right, change in (
        (productions[0], Fraction(1, 20), Fraction(1, 10), 200, 100, Fraction(-1, 20)),
        (productions[1], Fraction(4, 95), Fraction(1, 10), 190, 90, Fraction(-11, 190)),
    ):
        assert len(production.payload.parents) == 1
        parent = production.payload.parents[0]
        assert parent.key == ()
        method = parent.metrics[0].method
        assert isinstance(method, FrameRatioMethod)
        assert method.left.value == pytest.approx(float(left))
        assert method.right.value == pytest.approx(float(right))
        assert method.change.value == pytest.approx(float(change))
        assert method.denominator_left.value == denominator_left
        assert method.denominator_right.value == denominator_right


def test_linear_distinct_decimal_children_have_contributions_eight_and_ten(
    session: Session, tmp_path: Path
) -> None:
    with duckdb.connect(str(tmp_path / "oracles.duckdb")) as connection:
        connection.execute(
            "INSERT INTO facts(id,phase,region,gross,cost) VALUES "
            "(1,'base','G1',10,5),(2,'base','G2',20,10),"
            "(3,'current','G1',16,4),(4,'current','G2',24,7)"
        )
        native = connection.execute(
            "SELECT region,SUM(gross)-2*SUM(cost) FROM facts WHERE phase='current' "
            "GROUP BY region ORDER BY region"
        ).fetchall()
        root_change = connection.execute(
            "SELECT SUM(CASE WHEN phase='current' THEN gross-2*cost ELSE -(gross-2*cost) END) FROM facts"
        ).fetchone()
    assert native == [("G1", Decimal("8.00")), ("G2", Decimal("10.00"))]
    assert root_change == (Decimal("18.00"),)
    gross = session.catalog.fields.get("sales.facts.gross")
    cost = session.catalog.fields.get("sales.facts.cost")
    phase = session.catalog.fields.get("sales.facts.phase")
    region = session.catalog.fields.get("sales.facts.region")
    metric = gross.sum() - 2 * cost.sum()
    result = _contribution(
        session.observe({"q": metric}, where=phase == "current"),
        session.observe({"q": metric}, where=phase == "base"),
        by=(region,),
    ).compute()
    rows = result.to_pandas()
    assert rows.iloc[:, 0].tolist() == ["G1", "G2"]
    assert rows[("value", "detail.current.q")].tolist() == [value for _, value in native]
    assert rows[("value", "detail.baseline.q")].tolist() == [Decimal("0.00"), Decimal("0.00")]
    for path in ("detail.current.q", "detail.baseline.q", "detail.absolute.q", "contribution.q"):
        assert all(type(value) is Decimal for value in rows[("value", path)])
        assert isinstance(_columns(result)[path].dtype, DecimalDataType)
        assert _columns(result)[path].unit == "USD"
    assert rows[("value", "detail.absolute.q")].tolist() == [Decimal("8.00"), Decimal("10.00")]
    assert rows[("value", "contribution.q")].tolist() == [Decimal("8.00"), Decimal("10.00")]
    assert sum(rows[("value", "contribution.q")]) == root_change[0]
    assert rows[("value", "contribution_share.q")].tolist() == pytest.approx(
        [float(Fraction(4, 9)), float(Fraction(5, 9))]
    )
    assert _columns(result)["contribution_share.q"].unit == "1"
    _present(rows)


@pytest.mark.parametrize("distinct", (False, True), ids=("count", "exact_distinct"))
def test_disjoint_counted_identities_allow_exact_contributions(
    session: Session, tmp_path: Path, distinct: bool
) -> None:
    with duckdb.connect(str(tmp_path / "oracles.duckdb")) as connection:
        connection.execute(
            "INSERT INTO facts(id,phase,region,counted) VALUES "
            "(1,'base','A',101),(2,'base','A',101),(3,'base','A',NULL),"
            "(4,'base','B',201),(5,'base','B',202),"
            "(6,'current','A',101),(7,'current','A',102),(8,'current','A',103),"
            "(9,'current','A',NULL),(10,'current','B',201),(11,'current','B',201),"
            "(12,'current','B',201),(13,'current','B',NULL)"
        )
        overlaps = connection.execute(
            "SELECT phase,counted FROM facts WHERE counted IS NOT NULL "
            "GROUP BY phase,counted HAVING COUNT(DISTINCT region)>1"
        ).fetchall()
        native = connection.execute(
            "SELECT region,phase,COUNT(counted),COUNT(DISTINCT counted) "
            "FROM facts GROUP BY region,phase ORDER BY region,phase"
        ).fetchall()
    assert overlaps == []
    assert native == [
        ("A", "base", 2, 1),
        ("A", "current", 3, 3),
        ("B", "base", 2, 2),
        ("B", "current", 3, 1),
    ]
    value = session.catalog.fields.get("sales.facts.counted")
    phase = session.catalog.fields.get("sales.facts.phase")
    region = session.catalog.fields.get("sales.facts.region")
    metric = value.count_distinct() if distinct else value.count()
    result = _contribution(
        session.observe({"q": metric}, where=phase == "current"),
        session.observe({"q": metric}, where=phase == "base"),
        by=(region,),
    ).compute()
    rows = result.to_pandas()
    assert rows.iloc[:, 0].tolist() == ["A", "B"]
    assert rows[("value", "detail.current.q")].tolist() == ([3, 1] if distinct else [3, 3])
    assert rows[("value", "detail.baseline.q")].tolist() == ([1, 2] if distinct else [2, 2])
    expected = [2, -1] if distinct else [1, 1]
    assert rows[("value", "detail.absolute.q")].tolist() == expected
    assert rows[("value", "contribution.q")].tolist() == expected
    assert rows[("value", "contribution_share.q")].tolist() == ([2, -1] if distinct else [0.5, 0.5])
    for path in ("detail.current.q", "detail.baseline.q", "detail.absolute.q", "contribution.q"):
        assert _columns(result)[path].dtype == ScalarDataType("int64")
        assert _columns(result)[path].unit == "1"
    assert _columns(result)["contribution_share.q"].unit == "1"
    _present(rows)


def test_native_weighted_mean_uses_one_common_valid_pair_mask(
    session: Session, tmp_path: Path, productions: list[AttributeMethodProduction]
) -> None:
    with duckdb.connect(str(tmp_path / "oracles.duckdb")) as connection:
        connection.execute(
            "INSERT INTO facts(id,phase,region,amount,weight) VALUES "
            "(1,'base','G',5,1),(2,'base','G',NULL,9),"
            "(3,'current','G',10,1),(4,'current','G',NULL,9)"
        )
        native = connection.execute(
            "SELECT phase,SUM(amount*weight)/SUM(weight) FILTER (WHERE amount IS NOT NULL AND weight IS NOT NULL),"
            "SUM(amount*weight),SUM(weight) FILTER (WHERE amount IS NOT NULL AND weight IS NOT NULL),"
            "SUM(amount*weight)/SUM(weight) FROM facts GROUP BY phase ORDER BY phase"
        ).fetchall()
    assert native == [("base", 5.0, 5, 1, 0.5), ("current", 10.0, 10, 1, 1.0)]
    value = session.catalog.fields.get("sales.facts.amount")
    weight = session.catalog.fields.get("sales.facts.weight")
    phase = session.catalog.fields.get("sales.facts.phase")
    region = session.catalog.fields.get("sales.facts.region")
    metric = value.weighted_mean(weight=weight)
    result = _contribution(
        session.observe({"q": metric}, where=phase == "current"),
        session.observe({"q": metric}, where=phase == "base"),
        by=(region,),
    ).compute()
    rows = result.to_pandas()
    assert rows.iloc[:, 0].tolist() == ["G"]
    assert rows[("value", "detail.current.q")].tolist() == [10.0]
    assert rows[("value", "detail.baseline.q")].tolist() == [5.0]
    assert rows[("value", "detail.absolute.q")].tolist() == [5.0]
    assert rows[("value", "contribution.q")].tolist() == [5.0]
    assert rows[("value", "contribution_share.q")].tolist() == [1.0]
    columns = _columns(result)
    for path, expected, unit in (
        ("component.current.numerator.q", 10, "USD"),
        ("component.baseline.numerator.q", 5, "USD"),
        ("component.current.denominator.q", 1, "1"),
        ("component.baseline.denominator.q", 1, "1"),
    ):
        assert rows[("value", path)].tolist() == [expected]
        assert columns[path].dtype == DecimalDataType(38, 2)
        assert columns[path].unit == unit
    assert columns["detail.current.q"].dtype == ScalarDataType("float64")
    assert columns["contribution.q"].unit == "USD"
    assert columns["contribution_share.q"].unit == "1"
    _present(rows)
    assert len(productions) == 1
    method = productions[0].payload.parents[0].metrics[0].method
    assert isinstance(method, FrameRatioMethod)
    assert method.left.value == 10.0
    assert method.right.value == 5.0
    assert method.numerator_left.value == 10
    assert method.numerator_right.value == 5
    assert method.denominator_left.value == 1
    assert method.denominator_right.value == 1
    assert len(method.support) == 1
    support = method.support[0]
    assert support.policy == "co_valid_value_weight"
    assert (support.left_input_count, support.right_input_count) == (2, 2)
    assert (support.left_effective_count, support.right_effective_count) == (1, 1)
