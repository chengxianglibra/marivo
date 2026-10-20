"""Temporal Attribute inputs retain frozen full-key and actual source fibers."""

from collections.abc import Callable
from pathlib import Path

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.dsl.attribute import AttributeEndpoint
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject


@pytest.fixture
def session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    database = tmp_path / "temporal.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE facts(id BIGINT, phase VARCHAR, region VARCHAR, channel VARCHAR, amount BIGINT, weight BIGINT, day DATE)"
        )
        connection.execute(
            "INSERT INTO facts VALUES (1,'current','east','A',160,1,'2026-08-01'),(2,'current','east','B',80,1,'2026-08-01'),(3,'base','east','A',100,1,'2026-08-01'),(4,'base','east','B',100,1,'2026-08-01'),(5,'current','east','A',160,1,'2026-08-02'),(6,'current','east','B',80,1,'2026-08-02'),(7,'base','east','A',100,1,'2026-08-02'),(8,'base','east','B',100,1,'2026-08-02'),(9,'base','east','A',100,1,'2026-07-01'),(10,'base','east','B',100,1,'2026-07-01'),(11,'base','east','A',100,1,'2026-07-02'),(12,'base','east','B',100,1,'2026-07-02'),(13,'current','east','A',10,1,'2026-06-01'),(14,'base','east','A',10,1,'2026-06-01')"
        )
        connection.execute("CREATE TABLE otherfacts AS SELECT * FROM facts")
        connection.execute("CREATE TABLE tags(id BIGINT, label VARCHAR)")
        connection.execute("INSERT INTO tags VALUES (1,'x'),(2,'y')")
        connection.execute("CREATE TABLE links(id BIGINT, fact_id BIGINT, tag_id BIGINT)")
        connection.execute("INSERT INTO links VALUES (1,1,1),(2,1,2)")
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + f"md.duckdb(name='warehouse', path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Analytics', default=True)\n",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
facts = ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table('facts'), primary_key=['id'])
identifier = ms.dimension_column(name='id', entity=facts, column='id')
tags = ms.entity(name='tags', datasource=ms.ref.datasource('warehouse'), source=md.table('tags'), primary_key=['id'])
tag_id = ms.dimension_column(name='id', entity=tags, column='id')
label = ms.dimension_column(name='label', entity=tags, column='label')
links = ms.entity(name='links', datasource=ms.ref.datasource('warehouse'), source=md.table('links'), primary_key=['id'])
link_fact = ms.dimension_column(name='fact_id', entity=links, column='fact_id')
link_tag = ms.dimension_column(name='tag_id', entity=links, column='tag_id')
fact_links = ms.relationship(name='fact_links', from_entity=facts, to_entity=links, keys=[ms.join_on(identifier,link_fact)])
link_tags = ms.relationship(name='link_tags', from_entity=links, to_entity=tags, keys=[ms.join_on(link_tag,tag_id)])
otherfacts = ms.entity(name='otherfacts', datasource=ms.ref.datasource('warehouse'), source=md.table('otherfacts'), primary_key=['id'])
other_id = ms.dimension_column(name='id', entity=otherfacts, column='id')
other_day = ms.time_dimension_column(name='day', entity=otherfacts, column='day', granularity='day')
other_amount = ms.measure_column(name='amount', entity=otherfacts, column='amount', unit='USD', additivity=ms.additive_all())
other_fact = ms.relationship(name='other_fact', from_entity=otherfacts, to_entity=facts, keys=[ms.join_on(other_id,identifier)])
phase = ms.dimension_column(name='phase', entity=facts, column='phase')
region = ms.dimension_column(name='region', entity=facts, column='region')
channel = ms.dimension_column(name='channel', entity=facts, column='channel')
day = ms.time_dimension_column(name='day', entity=facts, column='day', granularity='day')
amount = ms.measure_column(name='amount', entity=facts, column='amount', unit='USD', additivity=ms.additive_all())
weight = ms.measure_column(name='weight', entity=facts, column='weight', unit='1', additivity=ms.additive_all())
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.create("temporal", timezone="UTC")


def test_plain_calendar_complete_parent_order_one_statement(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource.adapters import SourceSession

    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    phase = session.catalog.fields.get("sales.facts.phase")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    calendar = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    metric = amount.sum(time=day)
    parent = session.frame(
        mv.product(mv.coordinates(by=(region,), keys=(("east",),)), calendar)
    ).compute()
    reads: list[object] = []
    original = SourceSession.execute

    def execute(self: SourceSession, issued: object) -> pd.DataFrame:
        reads.append(issued)
        return original(self, issued)

    monkeypatch.setattr(SourceSession, "execute", execute)
    result = (
        session.attribute(
            AttributeEndpoint({"q": metric}, during=calendar, where=phase == "current"),
            AttributeEndpoint({"q": metric}, during=calendar, where=phase == "base"),
            parents=parent,
            parent_on=((parent.axes[0], region, region),),
            by=(channel,),
            contribution=True,
        )
        .compute()
        .to_pandas()
    )
    assert len(reads) == 2
    assert result[("value", "contribution.q")].tolist() == [60, -20, 60, -20]
    assert result[("value", "contribution_share.q")].tolist() == [1.5, -0.5, 1.5, -0.5]
    assert result[("presence", "left_present")].all()
    assert result[("presence", "right_present")].all()


def test_cross_period_paired_calendar_parent(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo._definition.correspondence import CalendarCorrespondence
    from marivo.datasource.adapters import SourceSession

    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    phase = session.catalog.fields.get("sales.facts.phase")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    current = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    baseline = session.time_scope(start="2026-07-01", end="2026-07-03").by(mv.grain("day"))
    metric = amount.sum(time=day)
    left = session.observe({"q": metric}, by=(current, region))
    right = session.observe({"q": metric}, by=(baseline, region))
    paired = left.align(
        right,
        on=tuple(zip(left.axes, right.axes, strict=True)),
        match=CalendarCorrespondence("ordinal"),
        how="outer",
    ).compute()
    calls: list[object] = []
    original = SourceSession.execute

    def execute(self: SourceSession, issued: object) -> pd.DataFrame:
        calls.append(issued)
        return original(self, issued)

    monkeypatch.setattr(SourceSession, "execute", execute)
    rows = (
        session.attribute(
            AttributeEndpoint({"q": metric}, during=current, where=phase == "current"),
            AttributeEndpoint({"q": metric}, during=baseline, where=phase == "base"),
            parents=paired,
            parent_on=((paired.axes[1], region, region),),
            by=(channel,),
            contribution=True,
        )
        .compute()
        .to_pandas()
    )
    assert len(calls) == 2
    assert rows[("value", "left.q")].tolist() == [160, 80, 160, 80]
    assert rows[("value", "right.q")].tolist() == [100, 100, 100, 100]
    assert rows[("value", "contribution.q")].tolist() == [60, -20, 60, -20]
    assert rows[("value", "contribution_share.q")].tolist() == [1.5, -0.5, 1.5, -0.5]


def test_fixed_window_lookback_and_repeated_identity_one_statement(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import marivo.analysis.materialization.graph_source_execution as execution
    from marivo.analysis.compiler.graph_lowering import LoweredRead
    from marivo.analysis.materialization.graph_exchange import StageOutput
    from marivo.analysis.materialization.graph_source_execution import PreparedSources
    from marivo.analysis.materialization.query_components import AttributeRootInputs
    from marivo.datasource.adapters import SourceSession

    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    calendar = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    metric = amount.sum(time=day).window(mv.all_history())
    parent = session.observe({"q": metric}, by=(calendar,)).compute()
    expected: list[tuple[int, int, int, float]] = []
    with duckdb.connect(str(tmp_path / "temporal.duckdb")) as connection:
        for endpoint in ("2026-08-02", "2026-08-03"):
            totals = connection.execute(
                "SELECT channel, SUM(amount) FILTER(WHERE phase='current'), SUM(amount) FILTER(WHERE phase='base') FROM facts WHERE day < ? GROUP BY channel ORDER BY channel",
                [endpoint],
            ).fetchall()
            change = sum(current - base for _, current, base in totals)
            expected.extend(
                (current, base, current - base, (current - base) / change)
                for _, current, base in totals
            )
    calls: list[object] = []
    original = SourceSession.execute

    def execute(self: SourceSession, issued: object) -> pd.DataFrame:
        calls.append(issued)
        return original(self, issued)

    monkeypatch.setattr(SourceSession, "execute", execute)
    captured: list[AttributeRootInputs] = []
    read_original = execution.read_stage

    def read(
        prepared: PreparedSources, lowered: LoweredRead, *, value_keys: tuple[str, ...] = ()
    ) -> StageOutput:
        output = read_original(prepared, lowered, value_keys=value_keys)
        captured.extend(output.attribute_inputs)
        return output

    monkeypatch.setattr(execution, "read_stage", read)
    pending = session.attribute(
        AttributeEndpoint({"q": metric}, during=calendar, where=phase == "current"),
        AttributeEndpoint({"q": metric}, during=calendar, where=phase == "base"),
        parents=parent,
        by=(channel,),
        contribution=True,
    )
    pending.show(n=0)
    assert calls == []
    rows = pending.compute().to_pandas()
    assert len(calls) == 2
    assert len(captured) == 2
    for root in captured:
        assert [leaf.request.binding.window_key for leaf in root.layout.leaves] == [0, 1]
        assert len({leaf.use.logical_occurrence for leaf in root.layout.leaves}) == 1
        assert len({leaf.use.occurrence for leaf in root.layout.leaves}) == 2
        assert all(leaf.request.binding.period.start is None for leaf in root.layout.leaves)
        assert [leaf.request.binding.period.end.isoformat() for leaf in root.layout.leaves] == [
            "2026-08-02T00:00:00+00:00",
            "2026-08-03T00:00:00+00:00",
        ]
        assert {
            "attribute.scoped_identity_coverage",
            "attribute.disjoint_joint_partition",
            "attribute.parent_fine_component_coverage",
        } <= {receipt.check_id for receipt in root.receipts}
        for leaf in root.layout.leaves:
            reduction = leaf.read.reduction
            calendar_slot = reduction.calendars[0][0]
            active = root.parent.loc[root.parent[calendar_slot] == reduction.window_key]
            assert len(active) == 1
            assert active[leaf.support.slot_id].iloc[0] > 0
    assert (
        list(
            zip(
                rows[("value", "left.q")],
                rows[("value", "right.q")],
                rows[("value", "contribution.q")],
                rows[("value", "contribution_share.q")],
                strict=True,
            )
        )
        == expected
    )


@pytest.mark.parametrize("missing", (False, True))
def test_window_partition_context_is_scoped_before_classification(
    session: Session,
    missing: bool,
) -> None:
    from marivo.analysis.core.attribute import (
        AttributeCoverageWitness,
        AttributeInputError,
        AttributePartitionWitness,
        AttributeWindowFiber,
    )

    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    identifier = session.catalog.fields.get("sales.facts.id")
    label = session.catalog.fields.get("sales.tags.label")
    path = (
        session.catalog.relationships.get("sales.fact_links"),
        session.catalog.relationships.get("sales.link_tags"),
    )
    membership = mv.membership(label, via=(path,), unmatched="exclude")
    calendar = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    metric = amount.sum(time=day).window(mv.all_history())
    parent = session.observe({"q": metric}, by=(calendar,)).compute()
    endpoint = AttributeEndpoint(
        {"q": metric},
        during=calendar,
        where=identifier == (2 if missing else 1),
        memberships=(membership,),
    )
    pending = session.attribute(endpoint, endpoint, parents=parent, by=(label,), contribution=True)
    with pytest.raises(AttributeInputError) as actual:
        pending.top(0, by=pending["contribution.q"]).compute()
    assert actual.value.reason == ("undefined_input" if missing else "overlapping_groups")
    context = actual.value.attribute_context
    assert context is not None and isinstance(context.fiber, AttributeWindowFiber)
    assert context.fiber.window_key == 0
    assert context.fiber.period.start is None
    assert context.uses[0].endpoint == "left"
    assert context.uses[0].metric == "q"
    assert context.uses[0].occurrence != context.uses[0].logical_occurrence
    assert isinstance(
        context.witness, AttributeCoverageWitness if missing else AttributePartitionWitness
    )
    assert context.witness.identity[0][1] == (2 if missing else 1)


@pytest.mark.parametrize("scope", ("parent", "fine"))
def test_temporal_native_numeric_check_keeps_actual_scope(
    session: Session,
    tmp_path: Path,
    scope: str,
) -> None:
    from marivo.analysis.core.attribute import (
        AttributeCheckWitness,
        AttributeFineFiber,
        AttributeParentFiber,
    )
    from marivo.analysis.materialization.errors import MaterializationError

    with duckdb.connect(str(tmp_path / "temporal.duckdb")) as connection:
        connection.execute("UPDATE facts SET amount=0")
        connection.execute("UPDATE facts SET amount=? WHERE id=1", [2**62])
        connection.execute(
            "UPDATE facts SET amount=? WHERE id=2", [2**62 if scope == "parent" else -(2**62)]
        )
        if scope == "fine":
            connection.execute(
                "INSERT INTO facts VALUES (15,'current','east','A',?,1,'2026-08-01')", [2**62]
            )
    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    calendar = session.time_scope(start="2026-08-01", end="2026-08-02").by(mv.grain("day"))
    metric = amount.sum(time=day)
    parent = session.observe({"q": amount.count(time=day)}, by=(calendar,)).compute()
    endpoint = AttributeEndpoint({"q": metric}, during=calendar, where=phase == "current")
    with pytest.raises(MaterializationError) as ordinary:
        session.observe(
            {"q": metric},
            where=phase == "current",
            by=(calendar, *(() if scope == "parent" else (channel,))),
        ).compute()
    with pytest.raises(MaterializationError) as actual:
        session.attribute(
            endpoint, endpoint, by=(channel,), parents=parent, contribution=True
        ).compute()
    assert actual.value.location == ordinary.value.location == "dataset.numeric_value"
    assert actual.value.expected == ordinary.value.expected
    assert actual.value.repair.action == ordinary.value.repair.action
    context = actual.value.attribute_context
    assert context is not None
    assert isinstance(
        context.fiber, AttributeParentFiber if scope == "parent" else AttributeFineFiber
    )
    assert len(context.fiber.parent) == 1
    assert len(context.fiber.parent_columns) == 1
    if isinstance(context.fiber, AttributeFineFiber):
        assert context.fiber.fine[0][1] == "A"
    assert isinstance(context.witness, AttributeCheckWitness)
    assert len(context.witness.columns) == (1 if scope == "parent" else 2)
    assert context.uses[0].metric == "q"
    assert context.uses[0].endpoint == "left"


def test_structural_outer_missing_is_not_known_empty(session: Session) -> None:
    from marivo._definition.correspondence import CalendarCorrespondence
    from marivo.analysis.core.attribute import AttributeAggregateWitness, AttributeInputError

    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    channel = session.catalog.fields.get("sales.facts.channel")
    metric = amount.sum(time=day)
    current = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    baseline = session.time_scope(start="2026-07-01", end="2026-07-02").by(mv.grain("day"))
    parent = (
        session.observe({"q": metric}, by=(current,))
        .align(
            session.observe({"q": metric}, by=(baseline,)),
            match=CalendarCorrespondence("ordinal"),
            how="outer",
        )
        .compute()
    )
    with pytest.raises(AttributeInputError) as actual:
        session.attribute(
            AttributeEndpoint({"q": metric}, during=current),
            AttributeEndpoint({"q": metric}, during=baseline),
            by=(channel,),
            parents=parent,
            parent_how="outer",
            contribution=True,
        ).compute()
    assert actual.value.reason == "undefined_input"
    assert actual.value.attribute_context is not None
    assert actual.value.attribute_context.uses[0].endpoint == "right"
    assert isinstance(actual.value.attribute_context.witness, AttributeAggregateWitness)


@pytest.mark.parametrize("weighted", (False, True))
def test_calendar_native_ratio_keeps_common_valid_mask(
    session: Session,
    tmp_path: Path,
    weighted: bool,
) -> None:
    with duckdb.connect(str(tmp_path / "temporal.duckdb")) as connection:
        connection.execute("UPDATE facts SET amount=NULL,weight=9 WHERE id=2")
    amount = session.catalog.fields.get("sales.facts.amount")
    weight = session.catalog.fields.get("sales.facts.weight")
    day = session.catalog.fields.get("sales.facts.day")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    calendar = session.time_scope(start="2026-08-01", end="2026-08-02").by(mv.grain("day"))
    metric = amount.weighted_mean(weight=weight, time=day) if weighted else amount.mean(time=day)
    parent = session.observe({"q": amount.count(time=day)}, by=(calendar,)).compute()
    rows = (
        session.attribute(
            AttributeEndpoint({"q": metric}, during=calendar, where=phase == "current"),
            AttributeEndpoint({"q": metric}, during=calendar, where=phase == "base"),
            by=(channel,),
            parents=parent,
            contribution=True,
        )
        .compute()
        .to_pandas()
    )
    assert rows[("value", "contribution.q")].tolist() == [110, -50]
    assert rows[("value", "contribution_share.q")].tolist() == pytest.approx([11 / 6, -5 / 6])
    assert rows[("value", "left.q")].iloc[0] == 160
    assert pd.isna(rows[("value", "left.q")].iloc[1])
    assert rows[("presence", "left_present")].all()


@pytest.mark.parametrize("corruption", ("mapping", "partial_directory"))
def test_paired_complete_frozen_authority_survives_parent_selection(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    from marivo._definition.correspondence import CalendarCorrespondence
    from marivo.analysis.core.artifact_data import OutputAttachment
    from marivo.analysis.core.data_schema import PairedDataAxis
    from marivo.analysis.core.frame_data import CompletedFrameData
    from marivo.analysis.errors import AnalysisError
    from marivo.datasource.adapters import SourceSession

    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    channel = session.catalog.fields.get("sales.facts.channel")
    metric = amount.sum(time=day)
    current = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    baseline = session.time_scope(start="2026-07-01", end="2026-07-03").by(mv.grain("day"))
    paired = (
        session.observe({"q": metric}, by=(current,))
        .align(
            session.observe({"q": metric}, by=(baseline,)),
            match=CalendarCorrespondence("ordinal"),
            how="outer",
        )
        .compute()
    )
    parent = paired.top(1, by=paired["left.q"]).compute()
    axis = parent._schema.axes[0]
    assert isinstance(axis, PairedDataAxis) and axis.right.frozen is not None
    right_directory = axis.right.frozen.directory_name
    copied = CompletedFrameData.copy_attachments

    def attachments(self: CompletedFrameData) -> tuple[OutputAttachment, ...]:
        result = []
        for attachment in copied(self):
            rows = attachment.rows
            if corruption == "mapping" and attachment.name == axis.mapping_name:
                rows = rows.copy()
                rows.iloc[0, -1] = 999
            elif corruption == "partial_directory" and attachment.name == right_directory:
                rows = rows.copy()
                columns = {column.name: column.slot_id for column in attachment.schema.values}
                actual_start = pd.Timestamp("2026-07-02T01:00:00+00:00")
                rows.loc[1, columns["actual_start"]] = actual_start
                rows.loc[1, columns["actual_start_civil"]] = actual_start.isoformat(
                    timespec="microseconds"
                )
            result.append(OutputAttachment(attachment.name, attachment.schema, rows))
        return tuple(result)

    monkeypatch.setattr(CompletedFrameData, "copy_attachments", attachments)
    calls = []
    original = SourceSession.execute

    def execute(self: SourceSession, issued: object) -> pd.DataFrame:
        calls.append(issued)
        return original(self, issued)

    monkeypatch.setattr(SourceSession, "execute", execute)
    with pytest.raises(
        AnalysisError,
        match="complete actual coverage" if corruption == "partial_directory" else "endpoint",
    ):
        session.attribute(
            AttributeEndpoint({"q": metric}, during=current),
            AttributeEndpoint({"q": metric}, during=baseline),
            by=(channel,),
            parents=parent,
            contribution=True,
        ).compute()
    assert calls == []


def test_calendar_original_nullable_integer_stays_exact(session: Session, tmp_path: Path) -> None:
    exact = 2**53 + 1
    with duckdb.connect(str(tmp_path / "temporal.duckdb")) as connection:
        connection.execute("UPDATE facts SET amount=? WHERE id=1", [exact])
        connection.execute("UPDATE facts SET amount=? WHERE id=3", [exact - 60])
    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    calendar = session.time_scope(start="2026-08-01", end="2026-08-02").by(mv.grain("day"))
    metric = amount.sum(time=day)
    parent = session.observe({"q": amount.count(time=day)}, by=(calendar,)).compute()
    rows = (
        session.attribute(
            AttributeEndpoint({"q": metric}, during=calendar, where=phase == "current"),
            AttributeEndpoint({"q": metric}, during=calendar, where=phase == "base"),
            by=(channel,),
            parents=parent,
            contribution=True,
        )
        .compute()
        .to_pandas()
    )
    assert int(rows[("value", "left.q")].iloc[0]) == exact
    assert int(rows[("value", "right.q")].iloc[0]) == exact - 60
    assert rows[("value", "contribution.q")].tolist() == [60, -20]
    assert str(rows[("value", "left.q")].dtype) == "int64[pyarrow]"


def test_calendar_pre_predicate_check_keeps_real_failure_scope(
    session: Session, tmp_path: Path
) -> None:
    from marivo.analysis.core.attribute import AttributeCheckWitness, AttributeParentFiber
    from marivo.analysis.materialization.errors import MaterializationError

    amount = session.catalog.fields.get("sales.otherfacts.amount")
    day = session.catalog.fields.get("sales.otherfacts.day")
    channel = session.catalog.fields.get("sales.facts.channel")
    calendar = session.time_scope(start="2026-08-01", end="2026-08-02").by(mv.grain("day"))
    metric = amount.sum(time=day)
    parent = session.observe({"q": amount.count(time=day)}, by=(calendar,)).compute()
    with duckdb.connect(str(tmp_path / "temporal.duckdb")) as connection:
        connection.execute("DELETE FROM facts WHERE id=2")
    with pytest.raises(MaterializationError) as ordinary:
        session.observe({"q": metric}, where=channel == "A", by=(calendar, channel)).compute()
    endpoint = AttributeEndpoint({"q": metric}, during=calendar, where=channel == "A")
    with pytest.raises(MaterializationError) as actual:
        session.attribute(
            endpoint, endpoint, by=(channel,), parents=parent, contribution=True
        ).compute()
    assert actual.value.location == ordinary.value.location == "dataset.scalar_field_lookup"
    assert actual.value.expected == ordinary.value.expected
    assert actual.value.repair.action == ordinary.value.repair.action
    context = actual.value.attribute_context
    assert context is not None and isinstance(context.fiber, AttributeParentFiber)
    assert context.fiber.parent[0][1] == 0
    assert isinstance(context.witness, AttributeCheckWitness)
    assert context.witness.coordinates[0][1][1] is None
    assert context.uses[0].root == "sales.otherfacts"


def test_calendar_duplicate_root_is_checked_before_classification(
    session: Session, tmp_path: Path
) -> None:
    from marivo.analysis.core.attribute import AttributeCoverageWitness
    from marivo.analysis.materialization.errors import MaterializationError

    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    channel = session.catalog.fields.get("sales.facts.channel")
    calendar = session.time_scope(start="2026-08-01", end="2026-08-02").by(mv.grain("day"))
    metric = amount.sum(time=day)
    parent = session.observe({"q": metric}, by=(calendar,)).compute()
    with duckdb.connect(str(tmp_path / "temporal.duckdb")) as connection:
        connection.execute("INSERT INTO facts SELECT * FROM facts WHERE id=1")
    endpoint = AttributeEndpoint({"q": metric}, during=calendar)
    with pytest.raises(
        MaterializationError, match="duplicate complete contribution identity"
    ) as actual:
        session.attribute(
            endpoint, endpoint, by=(channel,), parents=parent, contribution=True
        ).compute()
    context = actual.value.attribute_context
    assert context is not None and isinstance(context.witness, AttributeCoverageWitness)
    assert context.witness.identity[0][1] == 1


def test_calendar_selected_known_empty_parent_has_checked_zero_support(session: Session) -> None:
    from marivo.analysis.binding.attribute import attribute_input_request
    from marivo.analysis.materialization.frame_evaluation import evaluate_attribute_inputs

    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    calendar = session.time_scope(start="2026-08-01", end="2026-08-02").by(mv.grain("day"))
    parents = mv.product(mv.coordinates(by=(region,), keys=(("east",), ("absent",))), calendar)
    endpoint = AttributeEndpoint({"q": amount.count(time=day)}, during=calendar)
    prepared = evaluate_attribute_inputs(
        attribute_input_request(session, endpoint, endpoint, parents=parents, by=(channel,))
    )
    for root in prepared.attribute_inputs:
        leaf = root.layout.leaves[0]
        assert root.parent_state.count("known_empty") == 1
        empty = root.parent.iloc[root.parent_state.index("known_empty")]
        assert empty[leaf.support.slot_id] == 0
        assert empty[leaf.read.reduction.value_slot] == 0
        assert len(root.parent) == 2


@pytest.mark.parametrize("failure", ("native", "partition"))
def test_paired_source_failure_retains_original_full_parent(
    session: Session,
    tmp_path: Path,
    failure: str,
) -> None:
    from marivo._definition.correspondence import CalendarCorrespondence
    from marivo.analysis.core.attribute import (
        AttributeCheckWitness,
        AttributeInputError,
        AttributeParentFiber,
        AttributePartitionWitness,
        AttributeWindowFiber,
    )
    from marivo.analysis.materialization.errors import MaterializationError

    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    phase = session.catalog.fields.get("sales.facts.phase")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    identifier = session.catalog.fields.get("sales.facts.id")
    current = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    baseline = session.time_scope(start="2026-07-01", end="2026-07-03").by(mv.grain("day"))
    original = amount.sum(time=day)
    left = session.observe({"q": original}, by=(current, region))
    right = session.observe({"q": original}, by=(baseline, region))
    parent = left.align(
        right,
        on=tuple(zip(left.axes, right.axes, strict=True)),
        match=CalendarCorrespondence("ordinal"),
        how="outer",
    ).compute()
    if failure == "native":
        with duckdb.connect(str(tmp_path / "temporal.duckdb")) as connection:
            connection.execute("UPDATE facts SET amount=? WHERE id IN (9,10)", [2**62])
        left_endpoint = AttributeEndpoint({"q": original}, during=current, where=phase == "current")
        right_endpoint = AttributeEndpoint({"q": original}, during=baseline, where=phase == "base")
        by = (channel,)
    else:
        label = session.catalog.fields.get("sales.tags.label")
        path = (
            session.catalog.relationships.get("sales.fact_links"),
            session.catalog.relationships.get("sales.link_tags"),
        )
        membership = mv.membership(label, via=(path,), unmatched="exclude")
        by = (label,)
        metric = original.window(mv.all_history())
        left_endpoint = AttributeEndpoint(
            {"q": metric}, during=current, where=identifier == 1, memberships=(membership,)
        )
        right_endpoint = AttributeEndpoint(
            {"q": metric}, during=baseline, where=identifier == 1, memberships=(membership,)
        )
    pending = session.attribute(
        left_endpoint,
        right_endpoint,
        parents=parent,
        parent_on=((parent.axes[1], region, region),),
        by=by,
        contribution=True,
    )
    with pytest.raises(
        MaterializationError if failure == "native" else AttributeInputError
    ) as actual:
        pending.top(0, by=pending["contribution.q"]).compute()
    context = actual.value.attribute_context
    assert context is not None
    fiber = (
        context.fiber.fiber if isinstance(context.fiber, AttributeWindowFiber) else context.fiber
    )
    assert isinstance(fiber, AttributeParentFiber)
    assert fiber.parent_columns == tuple(
        column for axis in parent._schema.axes for column in axis.columns
    )
    from marivo.analysis.core.frozen_calendar import frozen_key_rows

    assert fiber.parent in frozen_key_rows(parent._backing.data.copy_rows(), fiber.parent_columns)
    assert context.uses[0].metric == "q"
    assert context.uses[0].endpoint == ("right" if failure == "native" else "left")
    if failure == "native":
        assert actual.value.location == "dataset.numeric_value"
        assert isinstance(context.witness, AttributeCheckWitness)
        assert context.witness.columns != fiber.parent_columns
        assert {value for _, value in context.witness.coordinates[0]} == {0, "east"}
        assert "key.0" in actual.value.received
    else:
        assert isinstance(context.fiber, AttributeWindowFiber)
        assert context.fiber.period.start is None
        assert isinstance(context.witness, AttributePartitionWitness)
        assert context.witness.identity[0][1] == 1
        assert len(context.witness.groups) == 2


@pytest.mark.parametrize("wide_role", ("numerator", "denominator"))
def test_weighted_integer_pair_sums_preserve_native_admitted_range(
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    wide_role: str,
) -> None:
    from decimal import Decimal
    from fractions import Fraction

    import marivo.analysis.materialization.graph_source_execution as execution
    from marivo.analysis.compiler.graph_lowering import LoweredRead
    from marivo.analysis.core.data_types import DecimalDataType, ScalarDataType
    from marivo.analysis.materialization.graph_exchange import StageOutput
    from marivo.analysis.materialization.graph_source_execution import PreparedSources
    from marivo.analysis.materialization.query_components import AttributeRootInputs
    from marivo.datasource.adapters import SourceSession

    x = 2**62
    if wide_role == "numerator":
        data = [(1, "base", "A", x, 1), (2, "base", "B", x, 1)]
        data.extend([(3, "current", "A", x + 1024, 1), (4, "current", "B", x + 2048, 1)])
        expected_sums = {"left": (2 * x + 3072, 2), "right": (2 * x, 2)}
        expected_contributions = [float(Fraction(1024, 2)), float(Fraction(2048, 2))]
        input_count = 2
    else:
        data = [(1, "base", "A", 0, x), (2, "base", "B", 0, x)]
        data.extend([(3, "current", "A", 1, x), (4, "current", "B", 1, x)])
        data.extend([(5, "current", "A", None, x), (6, "base", "A", None, x)])
        expected_sums = {"left": (2 * x, 2 * x), "right": (0, 2 * x)}
        expected_contributions = [float(Fraction(x, 2 * x))] * 2
        input_count = 3
    with duckdb.connect(str(tmp_path / "temporal.duckdb")) as connection:
        connection.execute("DELETE FROM facts")
        connection.executemany(
            "INSERT INTO facts(id,phase,channel,amount,weight) VALUES (?,?,?,?,?)", data
        )
    amount = session.catalog.fields.get("sales.facts.amount")
    weight = session.catalog.fields.get("sales.facts.weight")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    metric = amount.weighted_mean(weight=weight)
    left = AttributeEndpoint({"q": metric}, where=phase == "current")
    right = AttributeEndpoint({"q": metric}, where=phase == "base")
    ordinary = session.attribute(left, right, by=(channel,)).compute().to_pandas()
    parent_values = [
        session.observe({"q": metric}, where=phase == endpoint)
        .compute()
        .to_pandas()[("value", "q")]
        .iloc[0]
        for endpoint in ("current", "base")
    ]
    calls: list[object] = []
    execute_original = SourceSession.execute

    def execute(self: SourceSession, issued: object) -> pd.DataFrame:
        calls.append(issued)
        return execute_original(self, issued)

    monkeypatch.setattr(SourceSession, "execute", execute)
    captured: list[AttributeRootInputs] = []
    read_original = execution.read_stage

    def read(
        prepared: PreparedSources, lowered: LoweredRead, *, value_keys: tuple[str, ...] = ()
    ) -> StageOutput:
        output = read_original(prepared, lowered, value_keys=value_keys)
        captured.extend(output.attribute_inputs)
        return output

    monkeypatch.setattr(execution, "read_stage", read)
    rows = session.attribute(left, right, by=(channel,), contribution=True).compute().to_pandas()
    assert len(calls) == len(captured) == 2
    assert rows.iloc[:, 0].tolist() == ["A", "B"]
    for name in ("left.q", "right.q", "absolute.q"):
        assert rows[("value", name)].tolist() == ordinary[("value", name)].tolist()
    assert rows[("presence", "left_present")].all()
    assert rows[("presence", "right_present")].all()
    assert rows[("value", "contribution.q")].tolist() == expected_contributions
    native_change = parent_values[0] - parent_values[1]
    assert rows[("value", "contribution_share.q")].tolist() == [
        value / native_change for value in expected_contributions
    ]
    for root in captured:
        (leaf,) = root.layout.leaves
        numerator, denominator = expected_sums[leaf.use.endpoint]
        roles = dict(leaf.roles)
        assert roles["pair_product_sum"].dtype == DecimalDataType(38, 2)
        assert roles["pair_weight_sum"].dtype == DecimalDataType(38, 2)
        assert roles["pair_count"].dtype == ScalarDataType("int64")
        assert leaf.support.dtype == leaf.effective.dtype == ScalarDataType("int64")
        assert root.parent[roles["pair_product_sum"].slot_id].tolist() == [Decimal(numerator)]
        assert root.parent[roles["pair_weight_sum"].slot_id].tolist() == [Decimal(denominator)]
        assert root.parent[roles["pair_count"].slot_id].tolist() == [2]
        assert root.parent[leaf.support.slot_id].tolist() == [input_count]
        assert root.parent[leaf.effective.slot_id].tolist() == [2]
        for role in ("pair_product_sum", "pair_weight_sum"):
            assert all(isinstance(value, Decimal) for value in root.fine[roles[role].slot_id])
    for side in ("left", "right"):
        assert all(
            isinstance(value, Decimal) for value in rows[("value", "numerator_" + side + ".q")]
        )
        assert all(
            isinstance(value, Decimal) for value in rows[("value", "denominator_" + side + ".q")]
        )


def test_weighted_wide_pair_sums_keep_original_row_product_overflow(
    session: Session, tmp_path: Path
) -> None:
    from marivo.analysis.core.attribute import AttributeCheckWitness, AttributeParentFiber
    from marivo.analysis.materialization.errors import MaterializationError

    with duckdb.connect(str(tmp_path / "temporal.duckdb")) as connection:
        connection.execute("DELETE FROM facts")
        connection.execute(
            "INSERT INTO facts(id,phase,channel,amount,weight) VALUES "
            "(1,'base','A',1,1),(2,'current','A',?,4)",
            [2**62],
        )
    amount = session.catalog.fields.get("sales.facts.amount")
    weight = session.catalog.fields.get("sales.facts.weight")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    metric = amount.weighted_mean(weight=weight)
    with pytest.raises(MaterializationError) as ordinary:
        session.observe({"q": metric}, where=phase == "current").compute()
    with pytest.raises(MaterializationError) as actual:
        session.attribute(
            AttributeEndpoint({"q": metric}, where=phase == "current"),
            AttributeEndpoint({"q": metric}, where=phase == "base"),
            by=(channel,),
            contribution=True,
        ).compute()
    assert actual.value.location == ordinary.value.location == "dataset.numeric_value"
    assert actual.value.expected == ordinary.value.expected == "signed int64 arithmetic"
    assert actual.value.received == ordinary.value.received
    assert actual.value.repair.action == ordinary.value.repair.action
    context = actual.value.attribute_context
    assert context is not None
    assert isinstance(context.fiber, AttributeParentFiber)
    assert context.fiber.parent == ()
    assert isinstance(context.witness, AttributeCheckWitness)
    assert context.uses[0].endpoint == "left"
    assert context.uses[0].metric == "q"


@pytest.mark.parametrize("contribution", (False, True))
def test_independent_endpoint_time_scopes_keep_source_periods(
    session: Session, contribution: bool
) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    metric = amount.sum(time=day)
    current = session.time_scope(start="2026-08-01", end="2026-08-03")
    baseline = session.time_scope(start="2026-07-01", end="2026-07-03")
    result = session.attribute(
        AttributeEndpoint({"q": metric}, during=current, where=phase == "current"),
        AttributeEndpoint({"q": metric}, during=baseline, where=phase == "base"),
        by=(channel,),
        contribution=contribution,
    ).compute()
    rows = result.to_pandas()
    assert len(result.axes) == 1
    assert rows[("value", "left.q")].tolist() == [320, 160]
    assert rows[("value", "right.q")].tolist() == [200, 200]
    assert rows[("value", "absolute.q")].tolist() == [120, -40]
    if contribution:
        assert rows[("value", "contribution.q")].tolist() == [120, -40]


@pytest.mark.parametrize("paired", (False, True))
def test_interval_parent_rejects_before_source_read(
    session: Session, monkeypatch: pytest.MonkeyPatch, paired: bool
) -> None:
    from marivo.analysis.errors import AnalysisError
    from marivo.datasource.adapters import SourceSession

    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    channel = session.catalog.fields.get("sales.facts.channel")
    metric = amount.sum(time=day)
    scope = session.time_scope(start="2026-08-01", end="2026-08-03")
    parent = session.observe({"q": metric}, by=(scope,))
    if paired:
        parent = parent.align(parent)

    def forbidden(*args: object, **kwargs: object) -> pd.DataFrame:
        raise AssertionError("Interval parent rejection read business rows")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    endpoint = AttributeEndpoint({"q": metric}, during=scope)
    with pytest.raises(AnalysisError, match="interval or paired-interval parent axis") as actual:
        session.attribute(endpoint, endpoint, parents=parent, by=(channel,))
    assert "Reduce the interval axis" in str(actual.value)
