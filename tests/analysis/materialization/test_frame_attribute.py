"""Explicit default Attribute requests use the ordinary checked Frame schedule."""

from collections.abc import Callable
from pathlib import Path
from typing import cast

import duckdb
import pandas as pd
import pyarrow as pa
import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
from marivo.analysis.core.data_schema import DataSchema
from marivo.analysis.core.data_types import DecimalDataType
from marivo.analysis.core.frame_data import MemoryFrameBacking
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.errors import AnalysisError
from marivo.analysis.session.core import Session
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
day = ms.time_dimension_column(name='day', entity=facts, column='day')
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


def _compare(
    current: AnalysisFrame, baseline: AnalysisFrame, *, how: str = "outer"
) -> AnalysisFrame:
    assert how in ("outer", "exact")
    return current.compare(
        {"reference": baseline},
        on={"reference": tuple(zip(current.axes, baseline.axes, strict=True))},
        how="outer" if how == "outer" else "exact",
    )


def test_explicit_investigation_outer_presence_and_original_nullable_values(
    session: Session,
) -> None:
    amount = session.catalog.fields.get("sales.facts.amount").sum()
    channel = session.catalog.fields.get("sales.facts.channel")
    current = session.observe({"q": amount}, by=(channel,), where=channel != "B")
    baseline = session.observe({"q": amount}, by=(channel,), where=channel != "C")
    pending = _compare(current, baseline)
    assert pending.output_names == (
        "q",
        "reference.baseline.q",
        "reference.absolute.q",
        "reference.relative.q",
    )
    pending.show(n=0)
    fixed = pending.compute()
    rows = _flat(fixed.to_pandas())
    assert rows.iloc[:, 0].tolist() == ["A", "C", "N", "B"]
    rows = rows.sort_values(rows.columns[0], kind="stable").reset_index(drop=True)
    assert rows["presence.current.present"].tolist() == [True, False, True, True]
    assert rows["presence.reference.present"].tolist() == [True, True, False, True]
    assert pa.array(rows["q"]).to_pylist() == [2, None, 100, None]
    assert pa.array(rows["reference.baseline.q"]).to_pylist() == [2, 2, None, None]
    assert pa.array(rows["reference.absolute.q"]).to_pylist() == [0, None, None, None]
    assert fixed.top(0, by=fixed["reference.absolute.q"]).compute().to_pandas().empty
    assert fixed.method_facts == () and terminal_runs(session) == ()


def test_attribute_has_only_receiver_and_keyword_classifications(session: Session) -> None:
    import inspect

    assert tuple(inspect.signature(AnalysisFrame.attribute).parameters) == ("self", "by")
    amount = session.catalog.fields.get("sales.facts.amount").sum()
    channel = session.catalog.fields.get("sales.facts.channel")
    original = {"q": amount}
    source = session.observe(original)
    original["later"] = amount
    assert source.output_names == ("q",)
    for by in ((), (channel, channel)):
        with pytest.raises(AnalysisError):
            source.attribute(by=by)
    arithmetic = source.select({"q": source["q"] + 1})
    with pytest.raises(AnalysisError):
        arithmetic.attribute(by=(channel,))


def test_ordinary_parent_selection_keeps_fixed_keys_and_fresh_source_values(
    session: Session, tmp_path: Path
) -> None:
    with duckdb.connect(str(tmp_path / "attribute.duckdb")) as connection:
        connection.execute("DELETE FROM facts")
        connection.execute(
            "INSERT INTO facts(id,region,channel,amount,denominator,day) VALUES (1,'east','A',100,1,'2026-07-01'),(2,'east','B',100,1,'2026-07-01'),(3,'west','A',10,1,'2026-07-01'),(4,'east','A',140,1,'2026-08-01'),(5,'east','B',80,1,'2026-08-01'),(6,'west','A',15,1,'2026-08-01')"
        )
    amount = session.catalog.fields.get("sales.facts.amount").sum()
    identifier = session.catalog.fields.get("sales.facts.id")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    current = session.observe({"q": amount}, by=(region,), where=identifier >= 4)
    baseline = session.observe({"q": amount}, by=(region,), where=identifier < 4)
    compared = _compare(current, baseline).compute()
    selected = compared.where(compared["reference.absolute.q"] >= 20)
    target = selected.select({"change": selected["reference.absolute.q"]}).compute()
    frozen = target.to_pandas()
    with duckdb.connect(str(tmp_path / "attribute.duckdb")) as connection:
        connection.execute("UPDATE facts SET amount=160 WHERE id=4")
        connection.execute("UPDATE facts SET amount=1000 WHERE id=6")
        oracle = connection.execute(
            "SELECT channel,SUM(amount) FILTER(WHERE id>=4)-SUM(amount) FILTER(WHERE id<4) FROM facts WHERE region='east' GROUP BY channel ORDER BY channel"
        ).fetchall()
    assert oracle == [("A", 60), ("B", -20)]
    report = target.attribute(by=(channel,)).compute()
    rows = _flat(report.to_pandas())
    assert list(rows.iloc[:, :2].itertuples(index=False, name=None)) == [
        ("east", "A"),
        ("east", "B"),
    ]
    assert rows["contribution.change"].tolist() == [60, -20]
    assert rows["total.change"].tolist() == [40, 40]
    assert rows["observed_total.change"].tolist() == [20, 20]
    assert rows["contribution_share.change"].tolist() == [1.5, -0.5]
    pd.testing.assert_frame_equal(target.to_pandas(), frozen)


def test_joint_product_and_correlated_population_keep_complete_keys(session: Session) -> None:
    amount = session.catalog.fields.get("sales.facts.amount").sum()
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    regions = mv.coordinates(by=(region,), keys=(("east",), ("west",)))
    channels = mv.coordinates(by=(channel,), keys=(("A",), ("B",)))
    domains = (
        mv.product(regions, channels),
        mv.coordinates(by=(region, channel), keys=(("east", "A"), ("west", "B"))),
        mv.coordinates(by=(channel, region), keys=(("A", "east"), ("B", "west"))),
    )
    expected = (
        [("east", "A", 2), ("east", "B", 0), ("west", "A", 0), ("west", "B", 2)],
        [("east", "A", 2), ("west", "B", 2)],
        [("A", "east", 2), ("B", "west", 2)],
    )
    for domain, oracle in zip(domains, expected, strict=True):
        source = session.observe(
            {"q": amount}, by=domain, where=(channel == "A") | (channel == "B")
        )
        rows = source.compute().to_pandas()
        assert list(rows.itertuples(index=False, name=None)) == oracle
    with pytest.raises(AnalysisError):
        session.observe({"q": amount}).attribute(by=(cast("Deferred", domains[1]), channel))


def test_unsupported_max_can_still_be_investigated_against_native_sql(
    session: Session, tmp_path: Path
) -> None:
    with duckdb.connect(str(tmp_path / "attribute.duckdb")) as connection:
        connection.execute("DELETE FROM facts")
        connection.execute(
            "INSERT INTO facts(id,region,channel,amount,denominator,day) VALUES (1,'east','A',100,300,'2026-08-01'),(2,'east','B',0,207,'2026-08-01'),(3,'east','C',10,152,'2026-08-01'),(4,'east','A',0,200,'2026-07-01'),(5,'east','B',100,205,'2026-07-01'),(6,'east','C',0,150,'2026-07-01')"
        )
        oracle = connection.execute(
            "SELECT channel,MAX(denominator) FILTER(WHERE id<=3)-MAX(denominator) FILTER(WHERE id>3),SUM(amount) FILTER(WHERE id<=3)-SUM(amount) FILTER(WHERE id>3) FROM facts GROUP BY channel ORDER BY channel"
        ).fetchall()
    amount = session.catalog.fields.get("sales.facts.amount")
    denominator = session.catalog.fields.get("sales.facts.denominator")
    identifier = session.catalog.fields.get("sales.facts.id")
    channel = session.catalog.fields.get("sales.facts.channel")
    metrics = {"peak": denominator.max(), "net": amount.sum()}
    parent = session.observe(metrics, where=identifier <= 3).compute()
    with pytest.raises(AnalysisError):
        parent.attribute(by=(channel,))
    rows = _flat(
        _compare(
            session.observe(metrics, by=(channel,), where=identifier <= 3),
            session.observe(metrics, by=(channel,), where=identifier > 3),
        )
        .compute()
        .to_pandas()
    )
    assert oracle == [("A", 100, 100), ("B", 2, -100), ("C", 2, 10)]
    assert (
        list(
            rows.loc[
                :, [rows.columns[0], "reference.absolute.peak", "reference.absolute.net"]
            ].itertuples(index=False, name=None)
        )
        == oracle
    )
    assert rows["reference.absolute.peak"].sum() == 104
    assert rows["reference.absolute.net"].sum() == 10


def test_parent_frame_bridge_conjoins_original_predicate(session: Session) -> None:
    amount = session.catalog.fields.get("sales.facts.amount").sum()
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    parent = session.observe({"q": amount}, by=(region,))
    parent = parent.top(1, by=parent["q"]).compute()
    keys = session.population_from(parent, on=((parent.axes[0], region),))
    detail = session.observe(
        {"q": amount}, by=(region, channel), where=mv.in_population(keys) & (channel != "C")
    ).compute()
    assert list(detail.to_pandas().itertuples(index=False, name=None)) == [("east", "A", 2)]


def test_explicit_fine_question_keeps_original_ratio_without_parent_formula(
    session: Session,
) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    denominator = session.catalog.fields.get("sales.facts.denominator")
    channel = session.catalog.fields.get("sales.facts.channel")
    metric = amount.sum().divide(denominator.sum(), zero_denominator="error")
    fine = session.observe({"q": metric}, by=(channel,), where=(channel == "A") | (channel == "C"))
    rows = _flat(_compare(fine, fine).compute().to_pandas())
    assert rows["q"].tolist() == [-2.0, 100.0]
    assert rows["reference.absolute.q"].tolist() == [0.0, 0.0]


def test_complete_population_null_keys_and_empty_parent(session: Session) -> None:
    amount = session.catalog.fields.get("sales.facts.amount").sum()
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    parents = mv.coordinates(by=(region,), keys=((None,), ("missing",)))
    rows = (
        session.observe({"q": amount}, by=(region, channel), where=mv.in_population(parents))
        .compute()
        .to_pandas()
    )
    assert rows.iloc[:, 1].tolist() == ["N"]
    fine = mv.coordinates(by=(channel,), keys=(("A",), ("B",), ("C",), ("N",), ("Z",)))
    assert len(session.observe({"q": amount}, by=fine).compute().to_pandas()) == 5
    empty = mv.coordinates(by=(region,), keys=())
    assert (
        session.observe({"q": amount}, by=(region, channel), where=mv.in_population(empty))
        .compute()
        .to_pandas()
        .empty
    )


def test_complete_parent_exact_checks_survive_top_zero(session: Session) -> None:
    amount = session.catalog.fields.get("sales.facts.amount").sum()
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    left = session.observe({"q": amount}, by=(region,), where=channel == "A")
    right = session.observe({"q": amount}, by=(region,), where=channel == "B")
    invalid = _compare(left, right, how="exact")
    with pytest.raises(AnalysisError):
        invalid.top(0, by=invalid["q"]).compute()
    assert len(_compare(left, right).compute().to_pandas()) == 2


def test_fixed_and_pending_selection_keep_original_execution_boundary(
    session: Session, tmp_path: Path
) -> None:
    amount = session.catalog.fields.get("sales.facts.amount").sum()
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    selection = session.observe({"q": amount}, by=(region,), where=channel != "N")
    selection = selection.top(1, by=selection["q"])
    fixed = selection.compute()
    with duckdb.connect(str(tmp_path / "attribute.duckdb")) as connection:
        connection.execute("UPDATE facts SET amount=1000 WHERE region='west'")
    for parent, expected in ((fixed, "east"), (selection, "west")):
        rows = parent.attribute(by=(channel,)).compute().to_pandas()
        assert set(rows.iloc[:, 0]) == {expected}


def test_calendar_tuple_actual_domain_and_complete_fine_population(session: Session) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    channel = session.catalog.fields.get("sales.facts.channel")
    calendar = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    metric = amount.sum(time=day)
    assert len(session.observe({"q": metric}, by=(calendar, channel)).compute().to_pandas()) == 6
    parent = session.observe({"q": metric}, by=(calendar,)).compute()
    assert len(parent.attribute(by=(channel,)).compute().to_pandas()) == 3
    candidates = mv.coordinates(by=(channel,), keys=(("A",), ("B",), ("C",), ("Z",)))
    assert len(parent.attribute(by=candidates).compute().to_pandas()) == 8


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
    keys = mv.coordinates(by=(mv.identity(entity),), keys=((("t", 1),), (("t", 3),), (("t", 999),)))
    detail = session.observe(
        {"q": amount}, by=(mv.identity(entity), channel), where=mv.in_population(keys)
    ).compute()
    assert detail.to_pandas().iloc[:, 0].tolist() == [("t", 1), ("t", 3)]
    members = session.observe({"q": amount}, by=keys, where=mv.in_population(keys)).compute()
    assert members.to_pandas().iloc[:, 0].tolist() == [("t", 1), ("t", 3), ("t", 999)]
    assert members.to_pandas()[("value", "q")].tolist() == [100, 2, 0]


def test_original_checks_survive_projection_top_zero_and_empty_investigation(
    session: Session,
) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    channel = session.catalog.fields.get("sales.facts.channel")
    invalid = session.observe(
        {"q": amount.sum().divide(amount.sum() * 0, zero_denominator="error")}
    ).attribute(by=(channel,))
    selected = invalid.select({"ignored": 1})
    with pytest.raises(AnalysisError, match="denominator"):
        selected.top(0, by=selected["ignored"]).compute()
    empty = mv.coordinates(by=(channel,), keys=())
    assert session.observe({"q": amount.sum()}, by=empty).compute().to_pandas().empty


def test_overlapping_membership_has_explicit_investigation_continuation(session: Session) -> None:
    amount = session.catalog.fields.get("sales.facts.amount").sum()
    label = session.catalog.fields.get("sales.tags.label")
    kind = session.catalog.fields.get("sales.tags.kind")
    route = (
        session.catalog.relationships.get("sales.fact_links"),
        session.catalog.relationships.get("sales.link_tags"),
    )
    buckets = mv.membership((label, kind), via=(route,), unmatched="exclude")
    detail = session.observe({"q": amount}, by=(label, kind), memberships=(buckets,)).compute()
    assert detail.to_pandas()[("value", "q")].tolist() == [102, 100]
    parent = session.observe({"q": amount}, where=session.catalog.fields.get("sales.facts.id") <= 2)
    with pytest.raises(AnalysisError):
        parent.attribute(by=(buckets,)).compute()


def test_explicit_compare_validates_names_units_and_session_ownership(session: Session) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    denominator = session.catalog.fields.get("sales.facts.denominator")
    current = session.observe({"q": amount.sum()})
    for baseline in (
        session.observe({"other": amount.sum()}),
        session.observe({"q": denominator.sum()}),
    ):
        with pytest.raises(AnalysisError):
            _compare(current, baseline)
    foreign_session = mv.session.create("foreign", timezone="UTC")
    foreign = foreign_session.values(by=(), columns={"q": "USD"}, rows=(((), {"q": 1}),))
    with pytest.raises(AnalysisError):
        current.compare({"foreign": foreign})


def test_public_parent_key_bridge_checks_physical_types_before_top_zero(session: Session) -> None:
    amount = session.catalog.fields.get("sales.facts.amount").sum()
    region = session.catalog.fields.get("sales.facts.region")
    denominator = session.catalog.fields.get("sales.facts.denominator")
    channel = session.catalog.fields.get("sales.facts.channel")
    parent = session.frame(mv.coordinates(by=(denominator,), keys=((1,),))).compute()
    keys = session.population_from(parent, on=((parent.axes[0], region),))
    invalid = session.observe({"q": amount}, by=(region, channel), where=mv.in_population(keys))
    with pytest.raises(AnalysisError):
        invalid.top(0, by=invalid["q"]).compute()


def test_window_parent_calendar_keeps_original_history_scope(session: Session) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    channel = session.catalog.fields.get("sales.facts.channel")
    calendar = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    metric = amount.sum(time=day).window(mv.all_history())
    unknown_parent = session.observe({"q": metric}, by=(calendar,)).compute()
    with pytest.raises(AnalysisError, match="undefined_input"):
        unknown_parent.attribute(by=(channel,)).compute()
    parent = session.observe({"q": metric}, by=(calendar,), where=channel != "N").compute()
    rows = parent.attribute(by=(channel,)).compute().to_pandas()
    assert rows[("value", "detail.value.q")].dropna().tolist() == [2, 100, 2, 2, 100]


def test_completed_calendar_top_retains_full_original_directory(session: Session) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    channel = session.catalog.fields.get("sales.facts.channel")
    calendar = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    parent = session.observe({"q": amount.sum(time=day)}, by=(calendar,))
    parent = parent.top(1, by=parent["q"]).compute()
    assert parent._derivation is not None
    binding = parent._derivation.outputs[0].target.current.binding
    assert len(binding.calendars[0].directory.rows) == 2
    report = parent.attribute(by=(channel,)).compute()
    assert report.to_pandas().iloc[:, 0].tolist() == [0, 0]


def test_paired_missing_endpoint_rejects_contribution_and_remains_visible_in_investigation(
    session: Session,
) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    channel = session.catalog.fields.get("sales.facts.channel")
    metric = amount.sum(time=day)
    august = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    july = session.time_scope(start="2026-07-01", end="2026-07-02").by(mv.grain("day"))
    paired = (
        session.observe({"q": metric}, by=(august,))
        .align(session.observe({"q": metric}, by=(july,)), match=mv.ordinal(), how="outer")
        .compute()
    )
    assert not paired.to_pandas()[("presence", "right_present")].all()
    difference = paired.select({"change": paired["left.q"] - paired["right.q"]})
    with pytest.raises(AnalysisError):
        difference.attribute(by=(channel,)).compute()
    fine_left = session.observe({"q": metric}, by=(august, channel))
    fine_right = session.observe({"q": metric}, by=(july, channel))
    fine = fine_left.align(
        fine_right,
        on=tuple(zip(fine_left.axes, fine_right.axes, strict=True)),
        match=mv.ordinal(),
        how="outer",
    ).compute()
    assert not fine.to_pandas()[("presence", "right_present")].all()


def test_reduced_report_uses_explicit_source_bridge_without_slot_collisions(
    session: Session,
) -> None:
    amount = session.catalog.fields.get("sales.facts.amount").sum()
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    source = session.observe({"q": amount}, where=channel != "N")
    first = source.attribute(by=(region, channel)).compute()
    reduced = first.reduce({"delta": first["contribution.q"].sum()}, by=(first.axes[1],)).compute()
    with pytest.raises(AnalysisError):
        reduced.attribute(by=(region,))
    keys = session.population_from(reduced, on=((reduced.axes[0], channel),))
    second = session.observe(
        {"q": amount}, by=(channel, region), where=mv.in_population(keys)
    ).compute()
    assert len({axis.token for axis in second.axes}) == 2
    assert len(second.to_pandas()) == 3


def test_population_count_classified_identity_domain_is_explicit_source_authority(
    session: Session,
) -> None:
    entity = session.catalog.entities.get("sales.facts")
    channel = session.catalog.fields.get("sales.facts.channel")
    members = mv.coordinates(
        by=(mv.identity(entity),), keys=((("t", 1),), (("t", 2),), (("t", 3),))
    ).classify(by=(mv.identity(entity), channel))
    original = session.observe({"q": members.count()}, by=(channel,)).compute()
    parent = session.observe({"q": members.count()}).compute()
    with pytest.raises(AnalysisError):
        parent.attribute(by=(channel,))
    assert original.to_pandas()[("value", "q")].tolist() == [1, 1, 1]
    selected = mv.coordinates(by=(mv.identity(entity),), keys=((("t", 1),),))
    detail = session.observe(
        {"q": members.where(mv.in_population(selected)).count()}, by=(mv.identity(entity), channel)
    ).compute()
    assert detail.to_pandas().iloc[:, 0].tolist() == [("t", 1)]


@pytest.mark.parametrize("scaled", (False, True))
def test_member_count_effective_root_rejects_unrelated_identity_targets(
    session: Session, scaled: bool
) -> None:
    tags = session.catalog.entities.get("sales.tags")
    links = session.catalog.entities.get("sales.links")
    first = mv.coordinates(by=(mv.identity(tags),), keys=(((1,),), ((2,),)))
    second = mv.coordinates(by=(mv.identity(links),), keys=(((1,),), ((2,),), ((3,),)))
    a = first.count() * 2 if scaled else first.count()
    b = second.count() * 2 if scaled else second.count()
    compared = _compare(session.observe({"q": a}), session.observe({"q": b}))
    target = compared.select({"change": compared["reference.absolute.q"]})
    with pytest.raises(AnalysisError):
        target.attribute(by=(mv.identity(tags),))
    same_root = mv.coordinates(by=(mv.identity(tags),), keys=(((2,),),))
    other = same_root.count() * 2 if scaled else same_root.count()
    fine_current = session.observe({"q": a}, by=(mv.identity(tags),))
    fine_baseline = session.observe({"q": other}, by=(mv.identity(tags),))
    investigated = _compare(fine_current, fine_baseline).compute().to_pandas()
    assert investigated.iloc[:, 0].tolist() == [1, 2]
    assert investigated[("presence", "reference.present")].tolist() == [False, True]
    assert investigated[("value", "reference.absolute.q")].dropna().tolist() == [0]


@pytest.mark.parametrize("complete_fine", (False, True))
def test_selected_parent_scopes_member_count_before_fine_candidates(
    session: Session, complete_fine: bool
) -> None:
    entity = session.catalog.entities.get("sales.facts")
    channel = session.catalog.fields.get("sales.facts.channel")
    region = session.catalog.fields.get("sales.facts.region")
    members = mv.coordinates(
        by=(mv.identity(entity),), keys=((("t", 1),), (("t", 2),), (("t", 3),))
    ).classify(by=(mv.identity(entity), region, channel))
    parents = mv.coordinates(by=(region,), keys=(("east",),))
    fine = mv.coordinates(by=(channel,), keys=(("A",), ("C",), ("outside",)))
    original = session.observe({"q": members.count()}, by=parents).compute()
    detail = session.observe(
        {"q": members.where(mv.in_population(parents)).count()},
        by=mv.product(parents, fine) if complete_fine else (region, channel),
    ).compute()
    rows = detail.to_pandas()
    assert set(rows.iloc[:, 0]) == {"east"}
    assert rows[("value", "q")].sum() == 2
    if complete_fine:
        assert rows.iloc[:, 1].tolist() == ["A", "C", "outside"]


def test_correlated_overlapping_parent_membership_is_preserved_for_investigation(
    session: Session,
) -> None:
    amount = session.catalog.fields.get("sales.facts.amount").sum()
    label = session.catalog.fields.get("sales.tags.label")
    kind = session.catalog.fields.get("sales.tags.kind")
    channel = session.catalog.fields.get("sales.facts.channel")
    route = (
        session.catalog.relationships.get("sales.fact_links"),
        session.catalog.relationships.get("sales.link_tags"),
    )
    membership = mv.membership((label, kind), via=(route,), unmatched="exclude")
    parents = mv.coordinates(by=(label, kind), keys=(("x", "online"), ("y", "store")))
    observed = session.observe(
        {"q": amount},
        by=(label, kind, channel),
        where=mv.in_population(parents),
        memberships=(membership,),
    ).compute()
    assert list(observed.to_pandas().itertuples(index=False, name=None)) == [
        ("x", "online", "A", 2),
        ("x", "online", "C", 100),
        ("y", "store", "C", 100),
    ]


def test_native_decimal_investigation_preserves_finisher_and_parent_scope(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from decimal import Decimal

    from marivo.analysis.materialization import graph_source_execution

    with duckdb.connect(str(tmp_path / "attribute.duckdb")) as connection:
        connection.execute("ALTER TABLE facts ALTER amount TYPE DECIMAL(18,2)")
        connection.execute("DELETE FROM facts")
        connection.execute(
            "INSERT INTO facts(id,region,channel,amount,denominator,day) VALUES (1,'east','A',100,1,'2026-08-01'),(2,'east','B',100,1,'2026-08-01'),(3,'east','A',160.25,1,'2026-08-02'),(4,'east','B',80.25,1,'2026-08-02'),(5,'east',NULL,NULL,1,'2026-08-02'),(6,'west','outside',999,1,'2026-08-02')"
        )
    amount = session.catalog.fields.get("sales.facts.amount")
    identifier = session.catalog.fields.get("sales.facts.id")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    metric = (amount.sum() + amount.sum()) * Decimal("0.5")
    parents = session.observe({"q": metric}, by=(region,), where=region == "east").compute()
    keys = session.population_from(parents, on=((parents.axes[0], region),))

    def no_proof(*args: object, **kwargs: object) -> None:
        raise AssertionError("Plain investigation must not collect contribution proof")

    monkeypatch.setattr(graph_source_execution, "prepare_attribute_sources", no_proof)
    current = session.observe(
        {"q": metric}, by=(region, channel), where=mv.in_population(keys) & (identifier >= 3)
    )
    baseline = session.observe(
        {"q": metric}, by=(region, channel), where=mv.in_population(keys) & (identifier <= 2)
    )
    report = _compare(current, baseline).compute()
    rows = _flat(report.to_pandas())
    assert rows.iloc[:, 0].tolist() == ["east", "east", "east"]
    assert set(rows.iloc[:, 1].dropna()) == {"A", "B"}
    assert rows["reference.absolute.q"].dropna().tolist() == [Decimal(60), Decimal(-20)]
    assert report.method_facts == ()
    assert isinstance(report._schema, DataSchema)
    assert report._schema.values[0].dtype == DecimalDataType(38, 1)
    assert pa.array(rows["q"]).type == pa.decimal128(38, 1)
    empty = parents.top(0, by=parents["q"]).compute()
    empty_keys = session.population_from(empty, on=((empty.axes[0], region),))
    assert (
        session.observe({"q": metric}, by=(region, channel), where=mv.in_population(empty_keys))
        .compute()
        .to_pandas()
        .empty
    )


def test_fixed_report_method_facts_survive_named_multi_baseline_compare(session: Session) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    channel = session.catalog.fields.get("sales.facts.channel")
    parent = session.observe({"q": amount.sum()}, where=channel != "N")
    fixed = parent.attribute(by=(channel,)).compute()
    on = tuple(zip(fixed.axes, fixed.axes, strict=True))
    compared = fixed.compare({"one": fixed, "two": fixed}, on={"one": on, "two": on}).compute()
    assert len(fixed.method_facts) == 1 and compared.method_facts == fixed.method_facts
    for alias in ("one", "two"):
        assert (
            compared.to_pandas()[("value", alias + ".absolute.detail.value.q")].dropna().eq(0).all()
        )
    assert isinstance(compared._backing, MemoryFrameBacking)
    assert len(compared._backing.metadata.audit.evaluation.inputs) == 1


def test_member_count_compound_identity_and_nonleading_calendar_order(session: Session) -> None:
    entity = session.catalog.entities.get("sales.facts")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    members = mv.coordinates(
        by=(mv.identity(entity),), keys=((("t", 1),), (("t", 2),), (("t", 3),))
    ).classify(by=(mv.identity(entity), region, channel))
    first = mv.coordinates(by=(mv.identity(entity),), keys=((("t", 1),),))
    compound = (
        session.observe(
            {"q": members.where(mv.in_population(first)).count()}, by=(mv.identity(entity), channel)
        )
        .compute()
        .to_pandas()
    )
    assert compound.iloc[:, 0].tolist() == [("t", 1)]
    assert compound[("value", "q")].tolist() == [1]
    regions = mv.coordinates(by=(region,), keys=(("east",),))
    calendar = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    parents = mv.product(regions, calendar)
    fine = mv.coordinates(by=(channel,), keys=(("A",), ("C",)))
    rows = (
        session.observe(
            {"q": members.where(mv.in_population(regions)).count()}, by=mv.product(parents, fine)
        )
        .compute()
        .to_pandas()
    )
    assert rows.iloc[:, 0].tolist() == ["east"] * 4
    assert rows.iloc[:, 1].tolist() == [0, 0, 1, 1]
    assert rows[("value", "q")].tolist() == [1, 1, 1, 1]
