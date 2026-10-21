"""Explicit default Attribute requests use the ordinary checked Frame schedule."""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

import duckdb
import pandas as pd
import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
from marivo.analysis.dsl.attribute import attribute_endpoint
from marivo.analysis.compiler.frame_plan import CompiledFramePlan
from marivo.analysis.compiler.graph_lowering import LoweredRead
from marivo.analysis.compiler.graph_plan import FrameCompute, FrameInput, LocalCompute, SourceRead
from marivo.analysis.core.data_schema import DataSchema
from marivo.analysis.core.frame_data import CompletedFrameData, MemoryFrameBacking
from marivo.analysis.dsl.attribute import AttributeEndpoint
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.dsl.frame_axis import FrameAxis
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.graph_source_execution import PreparedSources, run_frame_plan
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import CompiledRead
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import terminal_runs


def _flat(rows: pd.DataFrame) -> pd.DataFrame:
    result = rows.copy()
    if isinstance(result.columns, pd.MultiIndex):
        result.columns = [
            column[1] if column[0] == "value" else ".".join(column) for column in result.columns
        ]
    return result


@pytest.fixture
def session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    database = tmp_path / "attribute.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE facts(id BIGINT, region VARCHAR, channel VARCHAR, amount BIGINT, denominator BIGINT, day DATE, tenant VARCHAR DEFAULT 't')"
        )
        connection.execute(
            "INSERT INTO facts(id,region,channel,amount,denominator,day) VALUES (1,'east','C',100,1,'2026-08-01'),(2,'east','A',2,-1,'2026-08-02'),(3,'west','B',2,1,'2026-08-01'),(4,NULL,'N',NULL,1,'2026-07-01')"
        )
        connection.execute("CREATE TABLE tags(id BIGINT, label VARCHAR, kind VARCHAR)")
        connection.execute("INSERT INTO tags VALUES (1,'x','online'),(2,'y','store')")
        connection.execute("CREATE TABLE links(id BIGINT, fact_id BIGINT, tag_id BIGINT)")
        connection.execute("INSERT INTO links VALUES (1,1,1),(2,1,2),(3,2,1)")
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + f"md.duckdb(name='warehouse', path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Analytics', default=True)\n",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
facts = ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table('facts'), primary_key=['tenant','id'])
region = ms.dimension_column(name='region', entity=facts, column='region')
channel = ms.dimension_column(name='channel', entity=facts, column='channel')
day = ms.time_dimension_column(name='day', entity=facts, column='day', granularity='day')
amount = ms.measure_column(name='amount', entity=facts, column='amount', unit='USD', additivity=ms.additive_all())
denominator = ms.measure_column(name='denominator', entity=facts, column='denominator', unit='1', additivity=ms.additive_all())
fact_id = ms.dimension_column(name='id', entity=facts, column='id')
tags = ms.entity(name='tags', datasource=ms.ref.datasource('warehouse'), source=md.table('tags'), primary_key=['id'])
tag_id = ms.dimension_column(name='id', entity=tags, column='id')
label = ms.dimension_column(name='label', entity=tags, column='label')
kind = ms.dimension_column(name='kind', entity=tags, column='kind')
links = ms.entity(name='links', datasource=ms.ref.datasource('warehouse'), source=md.table('links'), primary_key=['id'])
link_fact = ms.dimension_column(name='fact_id', entity=links, column='fact_id')
link_tag = ms.dimension_column(name='tag_id', entity=links, column='tag_id')
fact_links = ms.relationship(name='fact_links', from_entity=facts, to_entity=links, keys=[ms.join_on(fact_id, link_fact)])
link_tags = ms.relationship(name='link_tags', from_entity=links, to_entity=tags, keys=[ms.join_on(link_tag, tag_id)])
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.create("attribute", timezone="UTC")


def test_default_q_major_outer_presence_canonical_and_continuation(session: Session) -> None:
    amount = session.catalog.fields.get("sales.facts.amount").sum()
    channel = session.catalog.fields.get("sales.facts.channel")
    current = mv.attribute_endpoint({"q": amount}, where=channel != "B")
    baseline = mv.attribute_endpoint({"q": amount}, where=channel != "C")
    result = session.attribute(current, baseline, by=(channel,))
    assert result.output_names == ("left.q", "right.q", "absolute.q")
    assert result.presence_names == ("left_present", "right_present")
    result.show(n=0)
    fixed = result.compute()
    rows = _flat(fixed.to_pandas())
    assert rows[rows.columns[0]].tolist() == ["A", "B", "C", "N"]
    assert rows["presence.left_present"].tolist() == [True, False, True, True]
    assert rows["presence.right_present"].tolist() == [True, True, False, True]
    expected = pd.DataFrame(
        {
            "left.q": pd.Series([2, None, 100, None], dtype="int64[pyarrow]").array,
            "right.q": pd.Series([2, 2, None, None], dtype="int64[pyarrow]").array,
            "absolute.q": pd.Series([0, None, None, None], dtype="int64[pyarrow]").array,
        }
    )
    pd.testing.assert_frame_equal(rows.loc[:, list(expected)], expected)
    assert fixed.top(0, by=fixed["absolute.q"]).compute().to_pandas().empty
    assert terminal_runs(session) == ()


def test_request_copy_and_strict_bool(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from marivo.datasource.adapters import SourceSession

    reads: list[object] = []
    execute = SourceSession.execute

    def read(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        reads.append(issued)
        return execute(self, issued)

    monkeypatch.setattr(SourceSession, "execute", read)
    amount = session.catalog.fields.get("sales.facts.amount").sum()
    channel = session.catalog.fields.get("sales.facts.channel")
    original = {"q": amount}
    endpoint = mv.attribute_endpoint(original)
    original["later"] = amount
    assert tuple(endpoint.metrics) == ("q",)
    # These runtime values intentionally violate the public strict-bool type contract.
    for value in (0, 1, None):
        with pytest.raises(AnalysisError, match="strict boolean"):
            session.attribute(endpoint, endpoint, by=(channel,), contribution=cast("bool", value))
    for metrics in ({}, {"": amount}):
        with pytest.raises(AnalysisError) as caught:
            invalid = mv.attribute_endpoint(metrics)
            session.attribute(invalid, invalid, by=(channel,))
        assert caught.value.expected
        assert caught.value.received
        assert caught.value.repair is not None
        assert "nonempty" in caught.value.expected or "non-empty" in caught.value.expected
    assert reads == []
    assert terminal_runs(session) == ()


def test_ordinary_compare_population_bridge_uses_fixed_keys_and_fresh_values(
    session: Session, tmp_path: Path
) -> None:
    from fractions import Fraction

    from marivo.analysis.core.data_schema import DataSchema
    from marivo.analysis.core.data_types import ScalarDataType

    database = tmp_path / "attribute.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("DELETE FROM facts")
        connection.execute(
            "INSERT INTO facts(id,region,channel,amount,denominator,day) VALUES "
            "(1,'east','A',100,1,'2026-07-01'),(2,'east','B',100,1,'2026-07-01'),"
            "(3,'west','A',10,1,'2026-07-01'),(4,'west','B',10,1,'2026-07-01'),"
            "(5,'east','A',160,1,'2026-08-01'),(6,'east','B',80,1,'2026-08-01'),"
            "(7,'west','A',15,1,'2026-08-01'),(8,'west','B',15,1,'2026-08-01')"
        )
    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    metric = amount.sum(time=day)
    current = session.time_scope(start="2026-08-01", end="2026-08-02")
    baseline = session.time_scope(start="2026-07-01", end="2026-07-02")
    left = session.observe(
        {"q": metric},
        by=(
            current,
            region,
        ),
    )
    right = session.observe(
        {"q": metric},
        by=(
            baseline,
            region,
        ),
    )
    left = left.reduce({"q": left["q"].sum()}, by=(left.axes[1],))
    right = right.reduce({"q": right["q"].sum()}, by=(right.axes[1],))
    compared = left.compare(
        {"reference": right}, on={"reference": ((left.axes[0], right.axes[0]),)}, how="outer"
    ).compute()
    old = _flat(compared.to_pandas())
    assert list(old.iloc[:, 0]) == ["east", "west"]
    assert old["reference.absolute.q"].tolist() == [40, 10]
    selected = compared.where(compared["reference.absolute.q"] >= 20).persist()
    frozen = _flat(selected.to_pandas())
    assert frozen.iloc[:, 0].tolist() == ["east"]
    assert frozen["reference.absolute.q"].tolist() == [40]
    parents = session.population_from(selected, on=((selected.axes[0], region),))
    with duckdb.connect(str(database)) as connection:
        connection.execute("UPDATE facts SET amount=171 WHERE id=5")
        connection.execute("UPDATE facts SET amount=1000 WHERE id IN (7,8)")
        groups = connection.execute(
            "SELECT region,channel,"
            "SUM(amount) FILTER (WHERE day >= DATE '2026-08-01') AS left_q,"
            "SUM(amount) FILTER (WHERE day < DATE '2026-08-01') AS right_q "
            "FROM facts WHERE region='east' GROUP BY region,channel ORDER BY region,channel"
        ).fetchall()
        totals = connection.execute(
            "SELECT region,"
            "SUM(amount) FILTER (WHERE day >= DATE '2026-08-01') AS left_q,"
            "SUM(amount) FILTER (WHERE day < DATE '2026-08-01') AS right_q "
            "FROM facts GROUP BY region ORDER BY region"
        ).fetchall()
    assert groups == [("east", "A", 171, 100), ("east", "B", 80, 100)]
    assert totals == [("east", 251, 200), ("west", 2000, 20)]
    parent_change = totals[0][1] - totals[0][2]
    assert parent_change == 51
    assert totals[1][1] - totals[1][2] == 1980
    fixed = session.attribute(
        mv.attribute_endpoint({"q": metric}, during=current),
        mv.attribute_endpoint({"q": metric}, during=baseline),
        parents=parents,
        by=(channel,),
        contribution=True,
    ).compute()
    rows = _flat(fixed.to_pandas())
    assert list(rows.iloc[:, :2].itertuples(index=False, name=None)) == [
        (row[0], row[1]) for row in groups
    ]
    assert rows["left.q"].tolist() == [row[2] for row in groups]
    assert rows["right.q"].tolist() == [row[3] for row in groups]
    changes = [row[2] - row[3] for row in groups]
    assert changes == [71, -20]
    assert rows["absolute.q"].tolist() == changes
    assert rows["contribution.q"].tolist() == changes
    assert rows["contribution_share.q"].tolist() == pytest.approx(
        [float(Fraction(change, parent_change)) for change in changes]
    )
    assert rows["contribution.q"].sum() == parent_change
    assert not rows.isna().any().any()
    assert rows["presence.left_present"].tolist() == [True, True]
    assert rows["presence.right_present"].tolist() == [True, True]
    assert isinstance(fixed._schema, DataSchema)
    for column in fixed._schema.values:
        share = column.name == "contribution_share.q"
        assert column.dtype == ScalarDataType("float64" if share else "int64")
        assert column.unit == ("1" if share else "USD")
        assert column.nullable
    pd.testing.assert_frame_equal(_flat(selected.to_pandas()), frozen)


def test_joint_product_and_correlated_population_preserve_complete_keys(
    session: Session, tmp_path: Path
) -> None:
    from marivo.analysis.core.data_schema import DataSchema
    from marivo.analysis.core.data_types import ScalarDataType

    amount = session.catalog.fields.get("sales.facts.amount").sum()
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    endpoint = mv.attribute_endpoint({"q": amount}, where=(channel == "A") | (channel == "B"))
    regions = mv.coordinates(by=(region,), keys=(("east",), ("west",)))
    channels = mv.coordinates(by=(channel,), keys=(("A",), ("B",)))
    product = mv.product(regions, channels)
    correlated = mv.coordinates(by=(region, channel), keys=(("east", "A"), ("west", "B")))
    reversed_domain = mv.coordinates(by=(channel, region), keys=(("A", "east"), ("B", "west")))
    with duckdb.connect(str(tmp_path / "attribute.duckdb")) as connection:
        product_sql = connection.execute(
            "WITH domain(region,channel) AS "
            "(VALUES ('east','A'),('east','B'),('west','A'),('west','B')),"
            "actual AS (SELECT region,channel,SUM(amount) AS q FROM facts "
            "WHERE channel IN ('A','B') GROUP BY region,channel) "
            "SELECT d.region,d.channel,a.q FROM domain d LEFT JOIN actual a "
            "USING(region,channel) ORDER BY d.region,d.channel"
        ).fetchall()
        correlated_sql = connection.execute(
            "WITH domain(region,channel) AS (VALUES ('east','A'),('west','B')),"
            "actual AS (SELECT region,channel,SUM(amount) AS q FROM facts "
            "WHERE channel IN ('A','B') GROUP BY region,channel) "
            "SELECT d.region,d.channel,a.q FROM domain d LEFT JOIN actual a "
            "USING(region,channel) ORDER BY d.region,d.channel"
        ).fetchall()
    assert product_sql == [
        ("east", "A", 2),
        ("east", "B", None),
        ("west", "A", None),
        ("west", "B", 2),
    ]
    assert correlated_sql == [("east", "A", 2), ("west", "B", 2)]
    for domain, oracle, reverse in (
        (product, product_sql, False),
        (correlated, correlated_sql, False),
        (reversed_domain, correlated_sql, True),
    ):
        fixed = session.attribute(endpoint, endpoint, by=domain).compute()
        rows = _flat(fixed.to_pandas())
        expected_keys = [(row[1], row[0]) if reverse else row[:2] for row in oracle]
        assert list(rows.iloc[:, :2].itertuples(index=False, name=None)) == expected_keys
        expected_values = pd.Series([row[2] for row in oracle], dtype="int64[pyarrow]")
        for name in ("left.q", "right.q"):
            pd.testing.assert_series_equal(rows[name], expected_values, check_names=False)
        expected_change = pd.Series(
            [None if row[2] is None else 0 for row in oracle], dtype="int64[pyarrow]"
        )
        pd.testing.assert_series_equal(rows["absolute.q"], expected_change, check_names=False)
        assert rows["presence.left_present"].tolist() == [True] * len(oracle)
        assert rows["presence.right_present"].tolist() == [True] * len(oracle)
        assert isinstance(fixed._schema, DataSchema)
        assert all(
            column.dtype == ScalarDataType("int64") and column.nullable and column.unit == "USD"
            for column in fixed._schema.values
        )
    with pytest.raises(AnalysisError) as mixed:
        # This Population intentionally violates the declared per-axis input type.
        session.attribute(endpoint, endpoint, by=(cast("Deferred", correlated), channel))
    assert mixed.value.expected == "a semantic field or Grouping"
    assert mixed.value.received == "Population"
    assert mixed.value.repair is not None
    assert mixed.value.repair.action == (
        "Use a resolved field, mv.identity(entity), or population.axes."
    )
    with pytest.raises(AnalysisError):
        session.attribute(endpoint, endpoint, parents=regions, by=(region,))


def test_default_max_and_cancelling_sum_match_native_sql(session: Session, tmp_path: Path) -> None:
    from marivo.analysis.core.data_schema import DataSchema
    from marivo.analysis.core.data_types import ScalarDataType

    with duckdb.connect(str(tmp_path / "attribute.duckdb")) as connection:
        connection.execute("DELETE FROM facts")
        connection.execute(
            "INSERT INTO facts(id,region,channel,amount,denominator,day) VALUES "
            "(1,'east','A',100,300,'2026-08-01'),(2,'east','B',0,207,'2026-08-01'),"
            "(3,'east','C',10,152,'2026-08-01'),(4,'east','A',0,200,'2026-07-01'),"
            "(5,'east','B',100,205,'2026-07-01'),(6,'east','C',0,150,'2026-07-01')"
        )
        groups = connection.execute(
            "SELECT channel,"
            "MAX(denominator) FILTER (WHERE day >= DATE '2026-08-01'),"
            "MAX(denominator) FILTER (WHERE day < DATE '2026-08-01'),"
            "SUM(amount) FILTER (WHERE day >= DATE '2026-08-01'),"
            "SUM(amount) FILTER (WHERE day < DATE '2026-08-01') "
            "FROM facts GROUP BY channel ORDER BY channel"
        ).fetchall()
        overall = connection.execute(
            "SELECT MAX(denominator) FILTER (WHERE day >= DATE '2026-08-01'),"
            "MAX(denominator) FILTER (WHERE day < DATE '2026-08-01') FROM facts"
        ).fetchone()
    assert groups == [("A", 300, 200, 100, 0), ("B", 207, 205, 0, 100), ("C", 152, 150, 10, 0)]
    assert overall == (300, 205)
    assert overall[0] - overall[1] == 95
    amount = session.catalog.fields.get("sales.facts.amount")
    denominator = session.catalog.fields.get("sales.facts.denominator")
    day = session.catalog.fields.get("sales.facts.day")
    channel = session.catalog.fields.get("sales.facts.channel")
    metrics = {"peak": denominator.max(time=day), "net": amount.sum(time=day)}
    fixed = session.attribute(
        mv.attribute_endpoint(
            metrics, during=session.time_scope(start="2026-08-01", end="2026-08-02")
        ),
        mv.attribute_endpoint(
            metrics, during=session.time_scope(start="2026-07-01", end="2026-07-02")
        ),
        by=(channel,),
    ).compute()
    assert fixed.output_names == (
        "left.peak",
        "right.peak",
        "absolute.peak",
        "left.net",
        "right.net",
        "absolute.net",
    )
    rows = _flat(fixed.to_pandas())
    assert rows.iloc[:, 0].tolist() == [row[0] for row in groups] == ["A", "B", "C"]
    expected = pd.DataFrame(
        {
            "left.peak": pd.Series([row[1] for row in groups], dtype="int64[pyarrow]").array,
            "right.peak": pd.Series([row[2] for row in groups], dtype="int64[pyarrow]").array,
            "absolute.peak": pd.Series(
                [row[1] - row[2] for row in groups], dtype="int64[pyarrow]"
            ).array,
            "left.net": pd.Series([row[3] for row in groups], dtype="int64[pyarrow]").array,
            "right.net": pd.Series([row[4] for row in groups], dtype="int64[pyarrow]").array,
            "absolute.net": pd.Series(
                [row[3] - row[4] for row in groups], dtype="int64[pyarrow]"
            ).array,
        }
    )
    pd.testing.assert_frame_equal(rows.loc[:, list(expected)], expected)
    assert rows["absolute.peak"].tolist() == [100, 2, 2]
    assert rows["absolute.peak"].sum() == 104
    assert rows["absolute.net"].tolist() == [100, -100, 10]
    assert rows["absolute.net"].sum() == 10
    assert not rows.isna().any().any()
    assert rows["presence.left_present"].tolist() == [True, True, True]
    assert rows["presence.right_present"].tolist() == [True, True, True]
    assert isinstance(fixed._schema, DataSchema)
    for column in fixed._schema.values:
        assert column.dtype == ScalarDataType("int64")
        assert column.nullable
        assert column.unit == ("1" if column.name.endswith(".peak") else "USD")
    assert all(
        column.dtype == ScalarDataType("boolean") and not column.nullable
        for column in fixed._schema.presence
    )


def test_parent_frame_filter_is_and_and_fine_keys_are_outer(session: Session) -> None:
    amount = session.catalog.fields.get("sales.facts.amount").sum()
    channel = session.catalog.fields.get("sales.facts.channel")
    region = session.catalog.fields.get("sales.facts.region")
    parent = session.observe({"q": amount}, by=(region,))
    parent = parent.top(1, by=parent["q"])
    endpoint = attribute_endpoint({"q": amount}, where=channel != "C")
    result = session.attribute(
        endpoint,
        endpoint,
        by=(channel,),
        parents=parent,
        parent_on=((parent.axes[0], region, region),),
    ).compute()
    rows = result.to_pandas()
    assert rows.iloc[:, 0].tolist() == ["east"]
    assert rows.iloc[:, 1].tolist() == ["A"]


def test_default_uses_original_group_formula_without_parent_ratio_total(session: Session) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    denominator = session.catalog.fields.get("sales.facts.denominator")
    channel = session.catalog.fields.get("sales.facts.channel")
    metric = amount.sum().divide(denominator.sum(), zero_denominator="error")
    endpoint = attribute_endpoint({"q": metric}, where=(channel == "A") | (channel == "C"))
    result = _flat(session.attribute(endpoint, endpoint, by=(channel,)).compute().to_pandas())
    assert result["left.q"].tolist() == [-2.0, 100.0]
    assert result["absolute.q"].tolist() == [0.0, 0.0]


def test_plain_calendar_parent_and_paired_mixed_parent(session: Session, tmp_path: Path) -> None:
    from marivo._definition.correspondence import CalendarCorrespondence

    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    metric = amount.sum(time=day)
    august = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    july = session.time_scope(start="2026-07-01", end="2026-07-03").by(mv.grain("day"))
    selected = session.observe({"q": metric}, by=(august,)).compute()
    endpoint = attribute_endpoint({"q": metric}, during=august)
    plain = session.attribute(endpoint, endpoint, by=(channel,), parents=selected).compute()
    assert _flat(plain.to_pandas())["left.q"].tolist() == [2, 100, 2]
    left = session.observe(
        {"q": metric},
        by=(
            august,
            region,
        ),
    ).compute()
    right = session.observe(
        {"q": metric},
        by=(
            july,
            region,
        ),
    ).compute()
    paired = left.align(
        right,
        on=tuple(zip(left.axes, right.axes, strict=True)),
        how="outer",
        match=CalendarCorrespondence("ordinal"),
    ).compute()
    assert not paired.to_pandas()[("presence", "right_present")].all()
    with duckdb.connect(str(tmp_path / "attribute.duckdb")) as connection:
        connection.execute(
            "INSERT INTO facts(id,region,channel,amount,denominator,day) VALUES (5,'east','A',50,1,'2026-07-01')"
        )
    result = session.attribute(
        attribute_endpoint({"q": metric}, during=august),
        attribute_endpoint({"q": metric}, during=july),
        by=(channel,),
        parents=paired,
        parent_on=((paired.axes[1], region, region),),
        parent_how="outer",
    ).compute()
    assert result.presence_names == ("left_present", "right_present")
    assert len(result.to_pandas()) == 5
    assert 50 in result.to_pandas()[("value", "right.q")].dropna().tolist()


def test_complete_population_null_keys_and_empty_parent(session: Session) -> None:
    amount = session.catalog.fields.get("sales.facts.amount").sum()
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    endpoint = attribute_endpoint({"q": amount})
    parent = mv.coordinates(by=(region,), keys=((None,), ("missing",)))
    result = session.attribute(endpoint, endpoint, by=(channel,), parents=parent).compute()
    assert result.to_pandas().iloc[:, 1].tolist() == ["N"]
    fine = mv.coordinates(by=(channel,), keys=(("A",), ("B",), ("C",), ("N",), ("Z",)))
    complete = session.attribute(endpoint, endpoint, by=fine).compute()
    assert len(complete.to_pandas()) == 5
    empty = mv.coordinates(by=(region,), keys=())
    result = session.attribute(endpoint, endpoint, by=(channel,), parents=empty).compute()
    assert result.to_pandas().empty


def test_parent_exact_is_complete_parent_only_and_survives_top_zero(session: Session) -> None:
    from marivo.analysis.errors import AnalysisError

    amount = session.catalog.fields.get("sales.facts.amount").sum()
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    parents = session.observe({"q": amount}, by=(region,))
    parents = parents.where(parents.coord(parents.axes[0]) == "east").compute()
    left = attribute_endpoint({"q": amount}, where=channel == "A")
    right = attribute_endpoint({"q": amount}, where=channel == "C")
    parent_on = ((parents.axes[0], region, region),)
    rows = _flat(
        session.attribute(left, right, parents=parents, parent_on=parent_on, by=(channel,))
        .compute()
        .to_pandas()
    )
    assert rows.iloc[:, 1].tolist() == ["A", "C"]
    missing = attribute_endpoint({"q": amount}, where=channel == "B")
    invalid = session.attribute(left, missing, parents=parents, parent_on=parent_on, by=(channel,))
    with pytest.raises(AnalysisError, match="equal fresh complete parent domains"):
        invalid.top(0, by=invalid["absolute.q"]).compute()
    assert (
        len(
            session.attribute(
                left,
                missing,
                parent_how="outer",
                parents=parents,
                parent_on=parent_on,
                by=(channel,),
            )
            .compute()
            .to_pandas()
        )
        == 1
    )


def test_fixed_and_pending_parent_selection_use_one_checked_schedule(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import marivo.analysis.materialization.frame_evaluation as evaluator

    amount = session.catalog.fields.get("sales.facts.amount").sum()
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    selection = session.observe({"q": amount}, by=(region,))
    selection = selection.top(1, by=selection["q"])
    fixed = selection.compute()
    with duckdb.connect(str(tmp_path / "attribute.duckdb")) as connection:
        connection.execute("UPDATE facts SET amount=1000 WHERE region='west'")
    calls = []
    original = run_frame_plan

    def track(
        compiled: CompiledFramePlan,
        *,
        source_prepared: Mapping[str, PreparedSources],
        read_fixed: Callable[[FrameInput], CompletedFrameData],
        adapt_source: Callable[[str, StageOutput], StageOutput],
        observe_read: Callable[[SourceRead, LoweredRead, StageOutput], None] | None = None,
        observe_local: Callable[[LocalCompute | FrameCompute, StageOutput], None] | None = None,
    ) -> StageOutput:
        calls.append(compiled)
        return original(
            compiled,
            source_prepared=source_prepared,
            read_fixed=read_fixed,
            adapt_source=adapt_source,
            observe_read=observe_read,
            observe_local=observe_local,
        )

    monkeypatch.setattr(evaluator, "run_frame_plan", track)
    endpoint = attribute_endpoint({"q": amount})
    for parent, expected in ((fixed, "east"), (selection, "west")):
        result = session.attribute(
            endpoint,
            endpoint,
            by=(channel,),
            parents=parent,
            parent_on=((parent.axes[0], region, region),),
        ).compute()
        assert set(result.to_pandas().iloc[:, 0]) == {expected}
    assert len(calls) == 2
    from marivo.analysis.core.attribute import AttributeProjectProgram

    for call in calls:
        stages = call.lowered.plan.stages
        projections = [
            i
            for i, stage in enumerate(stages)
            if isinstance(stage, FrameCompute)
            and isinstance(stage.program, AttributeProjectProgram)
        ]
        endpoint_reads = [
            i
            for i, stage in enumerate(stages)
            if isinstance(stage, SourceRead) and stage.parameters
        ]
        assert len(projections) == 2 and max(projections) < min(endpoint_reads)


def test_calendar_tuple_actual_domain_preserves_ordinary_observe_completion(
    session: Session,
) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    channel = session.catalog.fields.get("sales.facts.channel")
    metric = amount.sum(time=day)
    calendar = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    ordinary = session.observe(
        {"q": metric},
        by=(
            calendar,
            channel,
        ),
    ).compute()
    assert len(ordinary.to_pandas()) == 6
    parent = session.observe({"q": metric}, by=(calendar,)).compute()
    endpoint = attribute_endpoint({"q": metric}, during=calendar)
    actual = session.attribute(endpoint, endpoint, by=(channel,), parents=parent).compute()
    assert len(actual.to_pandas()) == 3
    candidates = mv.coordinates(by=(channel,), keys=(("A",), ("B",), ("C",), ("Z",)))
    complete = session.attribute(endpoint, endpoint, by=candidates, parents=parent).compute()
    assert len(complete.to_pandas()) == 8


def test_compound_identity_parent_and_member_candidate_policy(
    session: Session, tmp_path: Path
) -> None:
    amount = session.catalog.fields.get("sales.facts.amount").sum()
    channel = session.catalog.fields.get("sales.facts.channel")
    entity = session.catalog.entities.get("sales.facts")
    with duckdb.connect(str(tmp_path / "attribute.duckdb")) as connection:
        connection.execute(
            "INSERT INTO facts(id,tenant,region,channel,amount,denominator,day) VALUES (1,'other','east','C',99,1,'2026-08-01')"
        )
    keys = mv.coordinates(
        by=(mv.identity(entity),),
        keys=(
            (("t", 1),),
            (("t", 3),),
            (("t", 999),),
        ),
    )
    parent = session.frame(keys).compute()
    endpoint = attribute_endpoint({"q": amount})
    result = session.attribute(
        endpoint,
        endpoint,
        by=(channel,),
        parents=parent,
        parent_on=((parent.axes[0], entity, entity),),
    ).compute()
    assert result.to_pandas().iloc[:, 0].tolist() == [("t", 1), ("t", 3)]
    endpoint = attribute_endpoint({"q": amount}, where=mv.in_population(keys))
    members = session.attribute(endpoint, endpoint, by=keys).compute()
    assert members.to_pandas().iloc[:, 0].tolist() == [("t", 1), ("t", 3), ("t", 999)]
    assert members.to_pandas()[("value", "left.q")].tolist()[:2] == [100, 2]
    assert pd.isna(members.to_pandas()[("value", "left.q")].iloc[2])


def test_original_checks_survive_select_top_and_explicit_empty_candidates(session: Session) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    channel = session.catalog.fields.get("sales.facts.channel")
    invalid = amount.sum().divide(amount.sum() * 0, zero_denominator="error")
    endpoint = attribute_endpoint({"q": invalid})
    result = session.attribute(endpoint, endpoint, by=(channel,))
    with pytest.raises(AnalysisError, match="denominator"):
        selected = result.select({"ignored": 1})
        selected.top(0, by=selected["ignored"]).compute()
    empty = mv.coordinates(by=(channel,), keys=())
    result = session.attribute(
        attribute_endpoint({"q": amount.sum()}), attribute_endpoint({"q": amount.sum()}), by=empty
    )
    assert result.compute().to_pandas().empty


def test_default_overlapping_correlated_membership_and_calendar_policy(session: Session) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    channel = session.catalog.fields.get("sales.facts.channel")
    label = session.catalog.fields.get("sales.tags.label")
    kind = session.catalog.fields.get("sales.tags.kind")
    path = (
        session.catalog.relationships.get("sales.fact_links"),
        session.catalog.relationships.get("sales.link_tags"),
    )
    buckets = mv.membership((label, kind), via=(path,), unmatched="exclude")
    endpoint = attribute_endpoint({"q": amount.sum()}, memberships=(buckets,))
    result = session.attribute(endpoint, endpoint, by=(label, kind)).compute()
    assert result.to_pandas()[("value", "left.q")].tolist() == [102, 100]
    assert len(result.axes) == 2
    calendar = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    metric = amount.sum(time=day)
    parent = session.observe({"q": metric}, by=(calendar,)).compute()
    endpoint = attribute_endpoint({"q": metric}, during=calendar, memberships=(buckets,))
    result = session.attribute(
        endpoint, endpoint, by=(channel, label, kind), parents=parent
    ).compute()
    assert len(result.to_pandas()) == 3
    assert result.to_pandas()[("value", "left.q")].tolist() == [100, 100, 2]
    domain = mv.coordinates(
        by=(label, kind), keys=(("x", "online"), ("y", "store"), ("z", "absent"))
    )
    result = session.attribute(endpoint, endpoint, by=domain, parents=parent).compute()
    assert len(result.to_pandas()) == 6


def test_request_rejects_incompatible_names_units_policies_and_parent_handles(
    session: Session,
) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    denominator = session.catalog.fields.get("sales.facts.denominator")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    endpoint = attribute_endpoint({"q": amount.sum()})
    for other in (
        attribute_endpoint({"other": amount.sum()}),
        attribute_endpoint({"q": denominator.sum()}),
        attribute_endpoint({"q": amount.max()}),
    ):
        with pytest.raises(AnalysisError):
            session.attribute(endpoint, other, by=(channel,))
    for by in ((), (channel, channel)):
        with pytest.raises(AnalysisError):
            session.attribute(endpoint, endpoint, by=by)
    parent = session.observe({"q": amount.sum()}, by=(region,)).compute()
    foreign = parent.select({"q": parent["q"]})
    for mappings in (
        (),
        ((parent.axes[0], region, region), (parent.axes[0], region, region)),
        ((foreign.axes[0], region, region),),
    ):
        with pytest.raises(AnalysisError):
            session.attribute(endpoint, endpoint, by=(channel,), parents=parent, parent_on=mappings)
    other_session = mv.session.create("other", timezone="UTC")
    with pytest.raises(AnalysisError, match="receiving Session"):
        other_session.attribute(
            endpoint,
            endpoint,
            by=(channel,),
            parents=parent,
            parent_on=((parent.axes[0], region, region),),
        )


def test_actual_parent_key_types_checked_before_top_zero_and_multiple_names_q_major(
    session: Session,
) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    denominator = session.catalog.fields.get("sales.facts.denominator")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    endpoint = attribute_endpoint({"second": amount.sum(), "first": amount.max()})
    right = attribute_endpoint({"first": amount.max(), "second": amount.sum()})
    frame = session.attribute(endpoint, right, by=(channel,))
    assert frame.output_names == (
        "left.second",
        "right.second",
        "absolute.second",
        "left.first",
        "right.first",
        "absolute.first",
    )
    assert frame.compute().to_pandas()[("value", "absolute.second")].dropna().eq(0).all()
    parent = session.frame(mv.coordinates(by=(denominator,), keys=((1,),))).compute()
    invalid = session.attribute(
        endpoint,
        right,
        by=(channel,),
        parents=parent,
        parent_on=((parent.axes[0], region, region),),
    )
    with pytest.raises(AnalysisError, match="physical key types"):
        invalid.top(0, by=invalid["absolute.first"]).compute()


def test_window_parent_calendar_keeps_original_history_scope(session: Session) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    channel = session.catalog.fields.get("sales.facts.channel")
    metric = amount.sum(time=day).window(mv.all_history())
    calendar = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    parent = session.observe({"q": metric}, by=(calendar,)).compute()
    endpoint = attribute_endpoint({"q": metric}, during=calendar)
    result = session.attribute(endpoint, endpoint, by=(channel,), parents=parent).compute()
    rows = result.to_pandas()
    assert rows[("value", "left.q")].dropna().tolist() == [2, 100, 2, 2, 100]


def test_complete_saved_calendar_directory_is_required_after_parent_top(session: Session) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    channel = session.catalog.fields.get("sales.facts.channel")
    metric = amount.sum(time=day)
    calendar = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    parent = session.observe({"q": metric}, by=(calendar,))
    parent = parent.top(1, by=parent["q"]).compute()
    shortened = session.time_scope(start="2026-08-01", end="2026-08-02").by(mv.grain("day"))
    endpoint = attribute_endpoint({"q": metric}, during=shortened)
    invalid = session.attribute(endpoint, endpoint, by=(channel,), parents=parent)
    with pytest.raises(AnalysisError, match="complete saved endpoint calendar directory"):
        invalid.compute()


def test_paired_structural_missing_side_exact_rejects_before_source_and_outer_keeps_it(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.analysis.materialization.frame_evaluation as evaluator
    from marivo._definition.correspondence import CalendarCorrespondence

    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    channel = session.catalog.fields.get("sales.facts.channel")
    metric = amount.sum(time=day)
    august = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    july = session.time_scope(start="2026-07-01", end="2026-07-02").by(mv.grain("day"))
    parent = (
        session.observe({"q": metric}, by=(august,))
        .align(
            session.observe({"q": metric}, by=(july,)),
            match=CalendarCorrespondence("ordinal"),
            how="outer",
        )
        .compute()
    )
    left, right = (
        attribute_endpoint({"q": metric}, during=august),
        attribute_endpoint({"q": metric}, during=july),
    )
    read_calls = []
    original = run_frame_plan

    def track(
        compiled: CompiledFramePlan,
        *,
        source_prepared: Mapping[str, PreparedSources],
        read_fixed: Callable[[FrameInput], CompletedFrameData],
        adapt_source: Callable[[str, StageOutput], StageOutput],
        observe_read: Callable[[SourceRead, LoweredRead, StageOutput], None] | None = None,
        observe_local: Callable[[LocalCompute | FrameCompute, StageOutput], None] | None = None,
    ) -> StageOutput:
        assert observe_read is not None

        def observed(stage: SourceRead, request: LoweredRead, output: StageOutput) -> None:
            read_calls.append(request)
            observe_read(stage, request, output)

        return original(
            compiled,
            source_prepared=source_prepared,
            read_fixed=read_fixed,
            adapt_source=adapt_source,
            observe_read=observed,
            observe_local=observe_local,
        )

    monkeypatch.setattr(evaluator, "run_frame_plan", track)
    invalid = session.attribute(left, right, by=(channel,), parents=parent)
    with pytest.raises(AnalysisError, match="structurally projected endpoints"):
        invalid.compute()
    assert read_calls == []
    result = session.attribute(
        left, right, by=(channel,), parents=parent, parent_how="outer"
    ).compute()
    rows = result.to_pandas()
    assert len(rows) == 4
    assert rows[("presence", "right_present")].tolist() == [False, False, True, False]


def test_reduced_attribute_result_is_an_ordinary_parent_without_slot_collisions(
    session: Session,
) -> None:
    amount = session.catalog.fields.get("sales.facts.amount").sum()
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    endpoint = attribute_endpoint({"q": amount})
    first = session.attribute(endpoint, endpoint, by=(region, channel)).compute()
    reduced = first.reduce({"delta": first["absolute.q"].sum()}, by=(first.axes[1],)).compute()
    second = session.attribute(
        endpoint,
        endpoint,
        by=(region,),
        parents=reduced,
        parent_on=((reduced.axes[0], channel, channel),),
    ).compute()
    assert len({axis.token for axis in second.axes}) == 2
    assert len(second.to_pandas()) == 4


def test_calendar_population_parent_and_member_count_keep_complete_member_domain(
    session: Session,
) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    calendar = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    regions = mv.coordinates(by=(region,), keys=(("east",), ("absent",)))
    parents = mv.product(calendar, regions)
    endpoint = attribute_endpoint({"q": amount.sum(time=day)}, during=calendar)
    result = session.attribute(endpoint, endpoint, by=(channel,), parents=parents).compute()
    assert len(result.to_pandas()) == 2
    entity = session.catalog.entities.get("sales.facts")
    members = mv.coordinates(
        by=(mv.identity(entity),),
        keys=(
            (("t", 1),),
            (("t", 2),),
            (("t", 3),),
        ),
    )
    members = members.classify(by=(members.axes[0], channel))
    # Population.count owns the explicitly classified identity domain.
    original = session.observe(
        {"q": members.count()},
        by=(
            calendar,
            channel,
        ),
    ).compute()
    parent = session.observe({"q": amount.sum(time=day)}, by=(calendar,)).compute()
    invalid = attribute_endpoint({"q": members.count()}, during=calendar, where=channel == "A")
    with pytest.raises(AnalysisError, match="count-only outputs"):
        session.attribute(invalid, invalid, by=(channel,), parents=parent)
    counted = attribute_endpoint({"q": members.count()}, during=calendar)
    result = session.attribute(counted, counted, by=(channel,), parents=parent).compute()
    assert (
        result.to_pandas()[("value", "left.q")].tolist()
        == original.to_pandas()[("value", "q")].tolist()
    )


@pytest.mark.parametrize("scaled", (False, True))
def test_member_count_effective_root_rejects_unrelated_integer_identities(
    session: Session, scaled: bool
) -> None:
    tag_members = mv.coordinates(
        by=(mv.identity(session.catalog.entities.get("sales.tags")),),
        keys=(
            ((1,),),
            ((2,),),
        ),
    )
    link_members = mv.coordinates(
        by=(mv.identity(session.catalog.entities.get("sales.links")),),
        keys=(
            ((1,),),
            ((2,),),
            ((3,),),
        ),
    )

    def metric(domain: mv.Population) -> Deferred:
        return 2 * domain.count() if scaled else domain.count()

    with pytest.raises(AnalysisError, match="compatible root roles"):
        session.attribute(
            attribute_endpoint({"q": metric(tag_members)}),
            attribute_endpoint({"q": metric(link_members)}),
            by=(mv.identity(session.catalog.entities.get("sales.tags")),),
        )
    same_root_other = mv.coordinates(
        by=(mv.identity(session.catalog.entities.get("sales.tags")),), keys=(((2,),),)
    )
    result = session.attribute(
        attribute_endpoint({"q": metric(tag_members)}),
        attribute_endpoint({"q": metric(same_root_other)}),
        by=(mv.identity(session.catalog.entities.get("sales.tags")),),
    ).compute()
    rows = result.to_pandas()
    assert rows.iloc[:, 0].tolist() == [1, 2]
    assert rows[("presence", "right_present")].tolist() == [False, True]
    assert rows[("value", "absolute.q")].dropna().tolist() == [0]


@pytest.mark.parametrize("frame_parent", (False, True))
@pytest.mark.parametrize("complete_fine", (False, True))
def test_selected_parent_scopes_member_count_before_candidate_selection(
    session: Session, frame_parent: bool, complete_fine: bool
) -> None:
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    members = mv.coordinates(
        by=(mv.identity(session.catalog.entities.get("sales.facts")),),
        keys=(
            (("t", 1),),
            (("t", 2),),
            (("t", 3),),
        ),
    )
    members = members.classify(by=(members.axes[0], region, channel))
    selected = mv.coordinates(by=(region,), keys=(("east",),))
    parent: AnalysisFrame | mv.Population
    mapping: tuple[tuple[FrameAxis, Deferred, Deferred], ...]
    if frame_parent:
        parent = session.frame(selected).compute()
        mapping = ((parent.axes[0], region, region),)
    else:
        parent = selected
        mapping = ()
    fine = mv.coordinates(by=(channel,), keys=(("A",), ("C",))) if complete_fine else (channel,)
    # The classified Population owns counted membership before parent selection.
    endpoint = attribute_endpoint({"q": 2 * members.count()})
    result = session.attribute(
        endpoint, endpoint, parents=parent, parent_on=mapping, by=fine
    ).compute()
    rows = result.to_pandas()
    assert rows.iloc[:, 0].tolist() == ["east", "east"]
    assert rows.iloc[:, 1].tolist() == ["A", "C"]
    assert rows[("value", "left.q")].tolist() == [2, 2]
    selected_fine = mv.coordinates(by=(channel,), keys=(("A",),))
    selected_rows = (
        session.attribute(endpoint, endpoint, parents=parent, parent_on=mapping, by=selected_fine)
        .compute()
        .to_pandas()
    )
    assert selected_rows.iloc[:, 0].tolist() == ["east"]
    assert selected_rows.iloc[:, 1].tolist() == ["A"]
    assert selected_rows[("value", "left.q")].tolist() == [2]


@pytest.mark.parametrize("both_parents", (False, True))
def test_population_parent_retains_correlated_overlapping_membership_authority(
    session: Session, both_parents: bool
) -> None:
    amount = session.catalog.fields.get("sales.facts.amount").sum()
    channel = session.catalog.fields.get("sales.facts.channel")
    label = session.catalog.fields.get("sales.tags.label")
    kind = session.catalog.fields.get("sales.tags.kind")
    path = (
        session.catalog.relationships.get("sales.fact_links"),
        session.catalog.relationships.get("sales.link_tags"),
    )
    membership = mv.membership((label, kind), via=(path,), unmatched="exclude")
    keys = (("x", "online"), ("y", "store")) if both_parents else (("x", "online"),)
    parents = mv.coordinates(by=(label, kind), keys=keys)
    oracle = (
        session.observe({"q": amount}, by=(label, kind, channel), memberships=(membership,))
        .compute()
        .to_pandas()
    )
    expected = oracle.loc[oracle.iloc[:, 0].isin([key[0] for key in keys])]
    endpoint = attribute_endpoint({"q": amount}, memberships=(membership,))
    actual = (
        session.attribute(endpoint, endpoint, parents=parents, by=(channel,)).compute().to_pandas()
    )
    assert list(actual.iloc[:, :3].itertuples(index=False, name=None)) == list(
        expected.iloc[:, :3].itertuples(index=False, name=None)
    )
    assert actual[("value", "left.q")].tolist() == expected[("value", "q")].tolist()
    assert actual[("value", "right.q")].tolist() == expected[("value", "q")].tolist()
    assert actual[("value", "absolute.q")].eq(0).all()


def test_member_count_compound_identity_parent_scope_and_calendar_axis_order(
    session: Session,
) -> None:
    entity = session.catalog.entities.get("sales.facts")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    members = mv.coordinates(
        by=(mv.identity(entity),),
        keys=(
            (("t", 1),),
            (("t", 2),),
            (("t", 3),),
        ),
    )
    endpoint = attribute_endpoint({"q": members.count()})
    complete = (
        session.attribute(endpoint, endpoint, by=(mv.identity(entity),)).compute().to_pandas()
    )
    assert complete.iloc[:, 0].tolist() == [("t", 1), ("t", 2), ("t", 3)]
    members = members.classify(by=(members.axes[0], region, channel))
    endpoint = attribute_endpoint({"q": members.count()})
    parent = mv.coordinates(by=(mv.identity(entity),), keys=((("t", 1),),))
    fine = mv.coordinates(by=(channel,), keys=(("C",),))
    scoped = session.attribute(endpoint, endpoint, parents=parent, by=fine).compute().to_pandas()
    assert scoped.iloc[:, 0].tolist() == [("t", 1)]
    assert scoped[("value", "left.q")].tolist() == [1]
    calendar = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    # Preserve the parent order region,calendar; the calendar is not key.0.
    parents = mv.product(mv.coordinates(by=(region,), keys=(("east",),)), calendar)
    counted = attribute_endpoint({"q": members.count()}, during=calendar)
    rows = (
        session.attribute(
            counted,
            counted,
            parents=parents,
            by=mv.coordinates(by=(channel,), keys=(("A",), ("C",))),
        )
        .compute()
        .to_pandas()
    )
    assert rows.iloc[:, 0].tolist() == ["east"] * 4
    assert rows.iloc[:, 1].tolist() == [0, 0, 1, 1]
    assert rows[("value", "left.q")].tolist() == [1, 1, 1, 1]


def test_default_native_decimal_formula_preserves_actual_carrier_and_parent_scope(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from decimal import Decimal

    from marivo.analysis.compiler.graph_lowering import LoweredQuery
    from marivo.analysis.compiler.graph_plan import SourceRead
    from marivo.analysis.core.data_types import DecimalDataType
    from marivo.analysis.materialization import graph_source_execution, query_key_fusion
    from marivo.analysis.materialization.graph_source_execution import PreparedSources
    from tests.support.source_trace import capture_source

    with duckdb.connect(str(tmp_path / "attribute.duckdb")) as connection:
        connection.execute("ALTER TABLE facts ALTER amount TYPE DECIMAL(18,2)")
        connection.execute("DELETE FROM facts")
        connection.execute(
            "INSERT INTO facts(id,region,channel,amount,denominator,day) VALUES "
            "(1,'east','A',100,1,'2026-08-01'),(2,'east','B',100,1,'2026-08-01'),"
            "(3,'east','A',160.25,1,'2026-08-02'),(4,'east','B',80.25,1,'2026-08-02'),"
            "(5,'east',NULL,NULL,1,'2026-08-02')"
        )
    amount = session.catalog.fields.get("sales.facts.amount")
    identifier = session.catalog.fields.get("sales.facts.id")
    channel = session.catalog.fields.get("sales.facts.channel")
    region = session.catalog.fields.get("sales.facts.region")
    metric = (amount.sum() + amount.sum()) * Decimal("0.5")
    current, baseline = identifier >= 3, identifier <= 2
    left = attribute_endpoint({"q": metric}, where=current)
    right = attribute_endpoint({"q": metric}, where=baseline)

    def no_proof(*args: object, **kwargs: object) -> None:
        raise AssertionError("default Attribute requested contribution proof")

    monkeypatch.setattr(graph_source_execution, "prepare_attribute_sources", no_proof)
    fused: list[SourceRead] = []
    fuse = query_key_fusion.fuse_query_terminal

    def terminal(prepared: PreparedSources, lowered: LoweredQuery) -> LoweredQuery:
        result = fuse(prepared, lowered)
        if result is not lowered:
            final = next(
                stage for stage in result.plan.stages if stage.stage_id == result.plan.publish
            )
            if isinstance(final, SourceRead) and any(
                column.slot_id == "value.0" for column in final.columns
            ):
                fused.append(final)
        return result

    monkeypatch.setattr(query_key_fusion, "fuse_query_terminal", terminal)
    trace = capture_source(monkeypatch)
    pending = session.attribute(left, right, by=(channel,))
    pending.show(n=0)
    assert trace.native_sql == [] and terminal_runs(session) == ()
    originals = tuple(
        session.observe({"q": metric}, where=condition, by=axes).compute()
        for condition in (current, baseline)
        for axes in ((), (channel,))
    )
    parent_left, fine_left, parent_right, fine_right = originals
    assert parent_left.to_pandas()["value", "q"].tolist() == [Decimal("240.500000000")]
    assert parent_right.to_pandas()["value", "q"].tolist() == [Decimal("200.000000000")]
    for original in originals:
        assert isinstance(original._schema, DataSchema)
        assert original._schema.values[0].dtype == DecimalDataType(38, 9)
        assert original._schema.values[0].unit == "USD"
    before = len(fused)
    fixed = pending.compute()
    native_endpoints = fused[before:]
    assert len(native_endpoints) == 4
    assert sorted(len(stage.keys) for stage in native_endpoints) == [0, 0, 1, 1]
    for stage in native_endpoints:
        assert stage.parameters
        assert stage.inputs == tuple(
            dict.fromkeys(parameter.stage_id for parameter in stage.parameters)
        )
        assert stage.native_reads
    assert fixed.method_facts == () and terminal_runs(session) == ()
    assert isinstance(fixed._schema, DataSchema)
    assert all(
        column.dtype == DecimalDataType(38, 9) and column.unit == "USD" and column.nullable
        for column in fixed._schema.values
    )
    value_rows = fixed.to_pandas()["value"]
    assert isinstance(value_rows, pd.DataFrame)
    assert all(str(dtype) == "decimal128(38, 9)[pyarrow]" for dtype in value_rows.dtypes)

    def keyed(frame: pd.DataFrame) -> dict[str | None, pd.Series]:
        rows = _flat(frame)
        return {None if pd.isna(row.iloc[0]) else row.iloc[0]: row for _, row in rows.iterrows()}

    expected_left, expected_right = keyed(fine_left.to_pandas()), keyed(fine_right.to_pandas())
    rows = keyed(fixed.to_pandas())
    assert set(rows) == set(expected_left) | set(expected_right) == {None, "A", "B"}
    for key, row in rows.items():
        assert row["presence.left_present"] == (key in expected_left)
        assert row["presence.right_present"] == (key in expected_right)
        for name, expected in (("left.q", expected_left), ("right.q", expected_right)):
            value = expected[key]["q"] if key in expected else None
            if pd.isna(value):
                assert pd.isna(row[name])
            else:
                assert row[name] == value
    assert rows["A"]["absolute.q"] == Decimal("60.250000000")
    assert rows["B"]["absolute.q"] == Decimal("-19.750000000")
    assert pd.isna(rows[None]["absolute.q"])

    with duckdb.connect(str(tmp_path / "attribute.duckdb")) as connection:
        connection.execute(
            "INSERT INTO facts(id,region,channel,amount,denominator,day) VALUES (6,'west','outside',999,1,'2026-08-02')"
        )
    selected = mv.coordinates(by=(region,), keys=(("east",),))
    selected_rows = _flat(
        session.attribute(left, right, by=(channel,), parents=selected).compute().to_pandas()
    )
    assert selected_rows.iloc[:, 0].tolist() == ["east", "east", "east"]
    assert "outside" not in selected_rows.iloc[:, 1].dropna().tolist()
    assert selected_rows["absolute.q"].dropna().tolist() == [Decimal("60.25"), Decimal("-19.75")]
    unit = session.values(by=(), columns={"parent": "1"}, rows=(((), {"parent": 1}),)).compute()
    empty = unit.where(unit["parent"] < 0).compute()
    assert session.attribute(left, right, by=(channel,), parents=empty).compute().to_pandas().empty
    assert terminal_runs(session) == ()


def test_fixed_attribute_method_facts_survive_named_multi_baseline_compare(
    session: Session,
) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    channel = session.catalog.fields.get("sales.facts.channel")
    endpoint = attribute_endpoint({"q": amount.sum()}, where=channel != "N")
    fixed = session.attribute(endpoint, endpoint, by=(channel,), contribution=True).compute()
    on = tuple(zip(fixed.axes, fixed.axes, strict=True))
    compared = fixed.compare({"one": fixed, "two": fixed}, on={"one": on, "two": on}).compute()
    assert len(fixed.method_facts) == 1 and compared.method_facts == fixed.method_facts
    assert compared.presence_names[:3] == ("current.present", "one.present", "two.present")
    rows = compared.to_pandas()
    for alias in ("one", "two"):
        assert rows[("value", alias + ".absolute.left.q")].dropna().eq(0).all()
        assert rows[("presence", alias + ".presence.left_present")].all()
    assert isinstance(compared._backing, MemoryFrameBacking)
    assert len(compared._backing.metadata.audit.evaluation.inputs) == 1
