"""Temporal Attribute inputs retain frozen full-key and actual source fibers."""

from collections.abc import Callable
from pathlib import Path

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.compiler.graph_plan import SourceRead, require_reduction
from marivo.analysis.core.frame_data import MemoryFrameBacking
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import CompiledRead
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
other_day = ms.time_dimension_column(name='day', entity=otherfacts, column='day')
other_amount = ms.measure_column(name='amount', entity=otherfacts, column='amount', unit='USD', additivity=ms.additive_all())
other_fact = ms.relationship(name='other_fact', from_entity=otherfacts, to_entity=facts, keys=[ms.join_on(other_id,identifier)])
phase = ms.dimension_column(name='phase', entity=facts, column='phase')
region = ms.dimension_column(name='region', entity=facts, column='region')
channel = ms.dimension_column(name='channel', entity=facts, column='channel')
day = ms.time_dimension_column(name='day', entity=facts, column='day')
amount = ms.measure_column(name='amount', entity=facts, column='amount', unit='USD', additivity=ms.additive_all())
weight = ms.measure_column(name='weight', entity=facts, column='weight', unit='1', additivity=ms.additive_all())
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("temporal", timezone="UTC")


def _change(current: mv.AnalysisFrame, baseline: mv.AnalysisFrame) -> mv.AnalysisFrame:
    """Build a fixed absolute target from actual captured observed endpoints."""
    temporal = any(axis.role in ("calendar", "interval") for axis in current.axes)
    compared = current.compare(
        {"base": baseline},
        on={"base": tuple(zip(current.axes, baseline.axes, strict=True))},
        match={"base": mv.ordinal()} if temporal else None,
    )
    return compared.select({"q": compared["base.absolute.q"]}).compute()


@pytest.mark.parametrize("calendar", [False, True])
def test_reused_temporal_baseline_keeps_each_alias_projection(
    session: Session, calendar: bool
) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    current = session.time_scope(start="2026-08-01", end="2026-08-03")
    baseline = session.time_scope(start="2026-07-01", end="2026-07-03")
    left = session.observe(
        {"q": amount.sum(time=day)},
        by=(current.by(mv.grain("day")) if calendar else current,),
        where=phase == "current",
    )
    right = session.observe(
        {"q": amount.sum(time=day)},
        by=(baseline.by(mv.grain("day")) if calendar else baseline,),
        where=phase == "base",
    )
    compared = left.compare(
        {"one": right, "two": right},
        on={name: tuple(zip(left.axes, right.axes, strict=True)) for name in ("one", "two")},
        match={name: mv.ordinal() for name in ("one", "two")},
    ).compute()
    report = compared.attribute(by=(channel,)).compute()
    rows = report.to_pandas()
    factor = 1 if calendar else 2
    repetitions = 2 if calendar else 1
    assert len(rows) == 2 * repetitions
    assert list(
        rows[
            [
                rows.attrs["axes"][0]["name"] + (".start" if not calendar else ""),
                "sales.facts.channel",
            ]
        ].itertuples(index=False, name=None)
    ) == [(key, channel) for key in compared.to_pandas().iloc[:, 0] for channel in ("A", "B")]
    for alias in ("one", "two"):
        for role, expected in (
            ("baseline", [100 * factor, 100 * factor]),
            ("absolute", [60 * factor, -20 * factor]),
            ("relative", [0.3, -0.1]),
        ):
            assert rows[f"contribution.{alias}.{role}.q"].tolist() == pytest.approx(
                expected * repetitions
            )


@pytest.mark.release
def test_plain_calendar_complete_parent_order_independent_reads(
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
    domain = mv.product(mv.coordinates(by=(region,), keys=(("east",),)), calendar)
    selected = _change(
        session.observe({"q": metric}, by=domain, where=phase == "current"),
        session.observe({"q": metric}, by=domain, where=phase == "base"),
    )
    reads: list[object] = []
    original = SourceSession.execute

    def execute(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        reads.append(issued)
        return original(self, issued)

    monkeypatch.setattr(SourceSession, "execute", execute)
    result = selected.attribute(by=(channel,)).compute().to_pandas()
    assert len(reads) == 4
    assert result["contribution.q"].tolist() == [60, -20, 60, -20]
    assert result["contribution_share.q"].tolist() == [1.5, -0.5, 1.5, -0.5]
    assert result["detail.current.present.q"].all()
    assert result["detail.baseline.present.q"].all()


@pytest.mark.release
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
    left = session.observe({"q": metric}, by=(current, region), where=phase == "current")
    right = session.observe({"q": metric}, by=(baseline, region), where=phase == "base")
    paired = left.align(
        right,
        on=tuple(zip(left.axes, right.axes, strict=True)),
        match=CalendarCorrespondence("ordinal"),
    ).compute()
    selected = paired.select({"q": paired["left.q"] - paired["right.q"]}).compute()
    calls: list[object] = []
    original = SourceSession.execute

    def execute(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        calls.append(issued)
        return original(self, issued)

    monkeypatch.setattr(SourceSession, "execute", execute)
    rows = selected.attribute(by=(channel,)).compute().to_pandas()
    assert len(calls) == 4
    assert rows["detail.current.q"].tolist() == [160, 80, 160, 80]
    assert rows["detail.baseline.q"].tolist() == [100, 100, 100, 100]
    assert rows["contribution.q"].tolist() == [60, -20, 60, -20]
    assert rows["contribution_share.q"].tolist() == [1.5, -0.5, 1.5, -0.5]


@pytest.mark.release
def test_fixed_window_lookback_and_repeated_identity_independent_reads(
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
    selected = _change(
        session.observe({"q": metric}, by=(calendar,), where=phase == "current"),
        session.observe({"q": metric}, by=(calendar,), where=phase == "base"),
    )
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

    def execute(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        calls.append(issued)
        return original(self, issued)

    monkeypatch.setattr(SourceSession, "execute", execute)
    captured: list[AttributeRootInputs] = []
    read_original = execution.read_stage

    def read(
        prepared: PreparedSources,
        stage: SourceRead,
        lowered: LoweredRead,
        *,
        value_keys: tuple[str, ...] = (),
    ) -> StageOutput:
        output = read_original(prepared, stage, lowered, value_keys=value_keys)
        captured.extend(output.attribute_inputs)
        return output

    monkeypatch.setattr(execution, "read_stage", read)
    pending = selected.attribute(by=(channel,))
    pending.show(n=0)
    assert calls == []
    rows = pending.compute().to_pandas()
    assert len(calls) == 4
    assert len(captured) == 4
    for root in captured:
        if root.layout.scope != "parent":
            continue
        assert [leaf.request.binding.window_key for leaf in root.layout.leaves] == [0, 1]
        assert len({leaf.use.logical_occurrence for leaf in root.layout.leaves}) == 1
        assert len({leaf.use.occurrence for leaf in root.layout.leaves}) == 2
        assert all(leaf.request.binding.period is not None for leaf in root.layout.leaves)
        periods = tuple(leaf.request.binding.period for leaf in root.layout.leaves)
        assert all(period is not None and period.start is None for period in periods)
        assert [period.end.isoformat() for period in periods if period is not None] == [
            "2026-08-02T00:00:00+00:00",
            "2026-08-03T00:00:00+00:00",
        ]
        assert not any(
            receipt.check_id == "attribute.scoped_identity_coverage" for receipt in root.receipts
        )
        for leaf in root.layout.leaves:
            reduction = require_reduction(leaf.read)
            calendar_slot = reduction.calendars[0][0]
            active = root.parent.loc[root.parent[calendar_slot] == reduction.window_key]
            assert len(active) == 1
            assert active[leaf.support.slot_id].iloc[0] > 0
    assert (
        list(
            zip(
                rows["detail.current.q"],
                rows["detail.baseline.q"],
                rows["contribution.q"],
                rows["contribution_share.q"],
                strict=True,
            )
        )
        == expected
    )


@pytest.mark.release
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
    endpoint = session.observe(
        {"q": metric},
        by=(calendar,),
        where=identifier == (2 if missing else 1),
    ).compute()
    pending = _change(endpoint, endpoint).attribute(by=(membership,))
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
    monkeypatch: pytest.MonkeyPatch,
    scope: str,
) -> None:
    from marivo.analysis.core.attribute import (
        AttributeCheckWitness,
        AttributeFineFiber,
        AttributeParentFiber,
    )
    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.datasource.adapters import SourceSession

    with duckdb.connect(str(tmp_path / "temporal.duckdb")) as connection:
        connection.execute("UPDATE facts SET amount=0")
    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    calendar = session.time_scope(start="2026-08-01", end="2026-08-02").by(mv.grain("day"))
    metric = amount.sum(time=day, nulls="error")
    observed = session.observe({"q": metric}, by=(calendar,), where=phase == "current").compute()
    selected = _change(observed, observed)
    with duckdb.connect(str(tmp_path / "temporal.duckdb")) as connection:
        connection.execute("UPDATE facts SET amount=NULL WHERE id=1")
    with pytest.raises(MaterializationError) as ordinary:
        session.observe(
            {"q": metric},
            where=phase == "current",
            by=(calendar, *(() if scope == "parent" else (channel,))),
        ).compute()
    with duckdb.connect(str(tmp_path / "temporal.duckdb")) as connection:
        connection.execute("UPDATE facts SET amount=0 WHERE id=1")
    execute = SourceSession.execute

    def read(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        rows = execute(self, issued)
        parent = "attribute-parent:" in issued.purpose
        if parent == (scope == "parent") and "__attribute_kind" in rows:
            # The ordinary query proves the native NULL decision. Inject its
            # signal at the reader boundary to isolate temporal check dispatch.
            failed = rows["__attribute_kind"] == (1 if parent else 0)
            if not parent:
                failed &= rows["key.1"] == "A"
            checks = [
                name
                for name in rows
                if isinstance(name, str) and name.startswith("attribute.check.")
            ]
            assert checks and failed.any()
            rows = rows.copy()
            for name in checks:
                rows.loc[failed, name] = True
        return rows

    monkeypatch.setattr(SourceSession, "execute", read)
    with pytest.raises(MaterializationError) as actual:
        selected.attribute(by=(channel,)).compute()
    assert actual.value.location == ordinary.value.location == "dataset.numeric_value"
    assert actual.value.expected == ordinary.value.expected
    assert actual.value.repair is not None and ordinary.value.repair is not None
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
            session.observe({"q": metric}, by=(baseline,)), match=CalendarCorrespondence("ordinal")
        )
        .compute()
    )
    with pytest.raises(AttributeInputError) as actual:
        parent.select({"q": parent["left.q"] - parent["right.q"]}).attribute(
            by=(channel,)
        ).compute()
    assert actual.value.reason == "undefined_input"
    assert actual.value.attribute_context is not None
    assert actual.value.attribute_context.uses[0].endpoint == "right"
    assert isinstance(actual.value.attribute_context.witness, AttributeAggregateWitness)


@pytest.mark.release
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
    selected = _change(
        session.observe({"q": metric}, by=(calendar,), where=phase == "current"),
        session.observe({"q": metric}, by=(calendar,), where=phase == "base"),
    )
    rows = selected.attribute(by=(channel,)).compute().to_pandas()
    assert rows["contribution.q"].tolist() == [110, -50]
    assert rows["contribution_share.q"].tolist() == pytest.approx([11 / 6, -5 / 6])
    assert rows["detail.current.q"].iloc[0] == 160
    assert pd.isna(rows["detail.current.q"].iloc[1])
    assert rows["detail.current.present.q"].all()


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
            session.observe({"q": metric}, by=(baseline,)), match=CalendarCorrespondence("ordinal")
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

    def execute(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        calls.append(issued)
        return original(self, issued)

    monkeypatch.setattr(SourceSession, "execute", execute)
    with pytest.raises(
        AnalysisError,
        match="complete actual coverage" if corruption == "partial_directory" else "endpoint",
    ):
        parent.select({"q": parent["left.q"] - parent["right.q"]}).attribute(
            by=(channel,)
        ).compute()
    assert calls == []


def test_calendar_predicate_trusts_declared_matches(session: Session, tmp_path: Path) -> None:
    amount = session.catalog.fields.get("sales.otherfacts.amount")
    day = session.catalog.fields.get("sales.otherfacts.day")
    channel = session.catalog.fields.get("sales.facts.channel")
    calendar = session.time_scope(start="2026-08-01", end="2026-08-02").by(mv.grain("day"))
    metric = amount.sum(time=day)
    parent = session.observe({"q": metric}, by=(calendar,), where=channel == "A").compute()
    with duckdb.connect(str(tmp_path / "temporal.duckdb")) as connection:
        connection.execute("DELETE FROM facts WHERE id=2")
    ordinary = (
        session.observe({"q": metric}, where=channel == "A", by=(calendar, channel))
        .compute()
        .to_pandas()
    )
    assert ordinary["q"].tolist() == [260]
    result = parent.attribute(by=(channel,)).compute().to_pandas()
    assert result["total.q"].tolist() == [260]


def test_calendar_attribute_trusts_declared_root_identity(session: Session, tmp_path: Path) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    channel = session.catalog.fields.get("sales.facts.channel")
    calendar = session.time_scope(start="2026-08-01", end="2026-08-02").by(mv.grain("day"))
    metric = amount.sum(time=day)
    parent = session.observe({"q": metric}, by=(calendar,)).compute()
    with duckdb.connect(str(tmp_path / "temporal.duckdb")) as connection:
        connection.execute("INSERT INTO facts SELECT * FROM facts WHERE id=1")
    rows = parent.attribute(by=(channel,)).compute().to_pandas()
    assert rows["total.q"].tolist() == [600, 600]
    assert rows["contribution.q"].sum() == 600


def test_calendar_selected_known_empty_parent_has_checked_zero_support(session: Session) -> None:
    from marivo.analysis.binding.frame_attribute import source_inputs
    from marivo.analysis.materialization.frame_evaluation import evaluate_attribute_inputs

    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    calendar = session.time_scope(start="2026-08-01", end="2026-08-02").by(mv.grain("day"))
    parents = mv.product(mv.coordinates(by=(region,), keys=(("east",), ("absent",))), calendar)
    parent = session.observe({"q": amount.count(time=day)}, by=parents).compute()
    assert parent._derivation is not None
    prepared = evaluate_attribute_inputs(
        source_inputs(parent, parent._derivation.outputs[0], (channel,))
    )
    for root in prepared.attribute_inputs:
        if root.layout.scope != "parent":
            continue
        leaf = root.layout.leaves[0]
        assert root.parent_state.count("known_empty") == 1
        empty = root.parent.iloc[root.parent_state.index("known_empty")]
        assert empty[leaf.support.slot_id] == 0
        assert empty[require_reduction(leaf.read).value_slot] == 0
        assert len(root.parent) == 2


@pytest.mark.release
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
    original = (
        amount.sum(time=day, nulls="error")
        if failure == "native"
        else amount.sum(time=day).window(mv.all_history())
    )
    if failure == "native":
        left = session.observe({"q": original}, by=(current, region), where=phase == "current")
        right = session.observe({"q": original}, by=(baseline, region), where=phase == "base")
        by = (channel,)
    else:
        label = session.catalog.fields.get("sales.tags.label")
        path = (
            session.catalog.relationships.get("sales.fact_links"),
            session.catalog.relationships.get("sales.link_tags"),
        )
        membership = mv.membership(label, via=(path,), unmatched="exclude")
        left = session.observe({"q": original}, by=(current, region), where=identifier == 1)
        right = session.observe({"q": original}, by=(baseline, region), where=identifier == 1)
        by = (membership,)
    aligned = left.align(
        right,
        on=tuple(zip(left.axes, right.axes, strict=True)),
        match=CalendarCorrespondence("ordinal"),
    )
    parent = aligned.select({"q": aligned["left.q"] - aligned["right.q"]}).compute()
    if failure == "native":
        with duckdb.connect(str(tmp_path / "temporal.duckdb")) as connection:
            connection.execute("UPDATE facts SET amount=NULL WHERE id IN (9,10)")
    pending = parent.attribute(by=by)
    with pytest.raises(
        MaterializationError if failure == "native" else AttributeInputError
    ) as actual:
        pending.top(0, by=pending["contribution.q"]).compute()
    assert isinstance(actual.value, (MaterializationError, AttributeInputError))
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

    assert isinstance(parent._backing, MemoryFrameBacking)
    assert fiber.parent in frozen_key_rows(parent._backing.data.copy_rows(), fiber.parent_columns)
    assert context.uses[0].metric == "q"
    assert context.uses[0].endpoint == ("right" if failure == "native" else "left")
    if failure == "native":
        assert actual.value.location == "dataset.numeric_value"
        assert isinstance(context.witness, AttributeCheckWitness)
        assert context.witness.columns != fiber.parent_columns
        assert {value for _, value in context.witness.coordinates[0]} == {0, "east"}
        assert actual.value.received is not None
        assert "key.0" in actual.value.received
    else:
        assert isinstance(context.fiber, AttributeWindowFiber)
        assert context.fiber.period.start is None
        assert isinstance(context.witness, AttributePartitionWitness)
        assert context.witness.identity[0][1] == 1
        assert len(context.witness.groups) == 2


def test_weighted_integer_pair_sums_preserve_native_admitted_range(
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    # Each parent independently crosses the native int64 sum boundary for one pair role.
    data: list[tuple[int, str, str, str, int | None, int]] = [
        (1, "base", "numerator", "A", x, 1),
        (2, "base", "numerator", "B", x, 1),
        (3, "current", "numerator", "A", x + 1024, 1),
        (4, "current", "numerator", "B", x + 2048, 1),
        (5, "base", "denominator", "A", 0, x),
        (6, "base", "denominator", "B", 0, x),
        (7, "current", "denominator", "A", 1, x),
        (8, "current", "denominator", "B", 1, x),
        (9, "current", "denominator", "A", None, x),
        (10, "base", "denominator", "A", None, x),
    ]
    expected_sums = {
        "numerator": {"left": (2 * x + 3072, 2), "right": (2 * x, 2)},
        "denominator": {"left": (2 * x, 2 * x), "right": (0, 2 * x)},
    }
    expected_contributions = {
        "numerator": [float(Fraction(1024, 2)), float(Fraction(2048, 2))],
        "denominator": [float(Fraction(x, 2 * x))] * 2,
    }
    # Native floating finishes retain their admitted rounding at this integer boundary.
    expected_parent_values = {
        "numerator": (float(x + 1536), float(x), 2048.0),
        "denominator": (1.0, 0.0, 1.0),
    }
    expected_details = {
        "numerator": {
            "detail.current.q": [float(x + 1024), float(x + 2048)],
            "detail.baseline.q": [float(x)] * 2,
            "detail.absolute.q": [1024.0, 2048.0],
        },
        "denominator": {
            "detail.current.q": [1.0, 1.0],
            "detail.baseline.q": [0.0, 0.0],
            "detail.absolute.q": [1.0, 1.0],
        },
    }
    input_counts = {"numerator": 2, "denominator": 3}
    with duckdb.connect(str(tmp_path / "temporal.duckdb")) as connection:
        connection.execute("DELETE FROM facts")
        connection.executemany(
            "INSERT INTO facts(id,phase,region,channel,amount,weight) VALUES (?,?,?,?,?,?)", data
        )
    amount = session.catalog.fields.get("sales.facts.amount")
    weight = session.catalog.fields.get("sales.facts.weight")
    phase = session.catalog.fields.get("sales.facts.phase")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    metric = amount.weighted_mean(weight=weight)
    left = session.observe({"q": metric}, by=(region,), where=phase == "current")
    right = session.observe({"q": metric}, by=(region,), where=phase == "base")
    # Retain both native parent values without reading the same endpoints again.
    parent = left.compare({"base": right}, on={"base": ((left.axes[0], right.axes[0]),)}).compute()
    parent_rows = parent.to_pandas()
    selected = parent.select({"q": parent["base.absolute.q"]})
    calls: list[object] = []
    execute_original = SourceSession.execute

    def execute(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        calls.append(issued)
        return execute_original(self, issued)

    monkeypatch.setattr(SourceSession, "execute", execute)
    captured: list[AttributeRootInputs] = []
    read_original = execution.read_stage

    def read(
        prepared: PreparedSources,
        stage: SourceRead,
        lowered: LoweredRead,
        *,
        value_keys: tuple[str, ...] = (),
    ) -> StageOutput:
        output = read_original(prepared, stage, lowered, value_keys=value_keys)
        captured.extend(output.attribute_inputs)
        return output

    monkeypatch.setattr(execution, "read_stage", read)
    rows = selected.attribute(by=(channel,)).compute().to_pandas()
    assert len(calls) == len(captured) == 4
    assert sorted(parent_rows.iloc[:, 0]) == ["denominator", "numerator"]
    assert sorted(rows.iloc[:, 0]) == ["denominator"] * 2 + ["numerator"] * 2
    for wide_role, contributions in expected_contributions.items():
        role_rows = rows.loc[rows.iloc[:, 0] == wide_role]
        role_parent = parent_rows.loc[parent_rows.iloc[:, 0] == wide_role]
        native_current, native_baseline, native_change = expected_parent_values[wide_role]
        assert role_parent["q"].tolist() == [native_current]
        assert role_parent["base.baseline.q"].tolist() == [native_baseline]
        assert role_parent["base.absolute.q"].tolist() == [native_change]
        assert role_rows.iloc[:, 1].tolist() == ["A", "B"]
        for name, values in expected_details[wide_role].items():
            assert role_rows[name].tolist() == values
        assert role_rows["contribution.q"].tolist() == contributions
        assert role_rows["contribution_share.q"].tolist() == [
            value / native_change for value in contributions
        ]
    assert rows["detail.current.present.q"].all()
    assert rows["detail.baseline.present.q"].all()
    parent_inputs = [root for root in captured if root.layout.scope == "parent"]
    assert sorted(root.layout.leaves[0].use.endpoint for root in parent_inputs) == ["left", "right"]
    for root in parent_inputs:
        (leaf,) = root.layout.leaves
        (parent_key,) = root.layout.parent_keys
        assert sorted(root.parent[parent_key]) == ["denominator", "numerator"]
        roles = dict(leaf.roles)
        assert roles["pair_product_sum"].dtype == DecimalDataType(38, 2)
        assert roles["pair_weight_sum"].dtype == DecimalDataType(38, 2)
        assert roles["pair_count"].dtype == ScalarDataType("int64")
        assert leaf.support.dtype == leaf.effective.dtype == ScalarDataType("int64")
        for wide_role, sums in expected_sums.items():
            numerator, denominator = sums[leaf.use.endpoint]
            role_parent = root.parent.loc[root.parent[parent_key] == wide_role]
            assert role_parent[roles["pair_product_sum"].slot_id].tolist() == [Decimal(numerator)]
            assert role_parent[roles["pair_weight_sum"].slot_id].tolist() == [Decimal(denominator)]
            assert role_parent[roles["pair_count"].slot_id].tolist() == [2]
            assert role_parent[leaf.support.slot_id].tolist() == [input_counts[wide_role]]
            assert role_parent[leaf.effective.slot_id].tolist() == [2]
        for role in ("pair_product_sum", "pair_weight_sum"):
            assert all(isinstance(value, Decimal) for value in root.fine[roles[role].slot_id])
    for side in ("left", "right"):
        assert all(
            isinstance(value, Decimal)
            for value in rows[
                "component." + ("current" if side == "left" else "baseline") + ".numerator.q"
            ]
        )
        assert all(
            isinstance(value, Decimal)
            for value in rows[
                "component." + ("current" if side == "left" else "baseline") + ".denominator.q"
            ]
        )


def test_weighted_wide_pair_sums_keep_original_row_product_overflow(
    session: Session, tmp_path: Path
) -> None:
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
        _change(
            session.observe({"q": metric}, where=phase == "current"),
            session.observe({"q": metric}, where=phase == "base"),
        ).attribute(by=(channel,)).compute()
    assert actual.value.location == ordinary.value.location == "dataset.source_execution"
    assert (
        actual.value.expected
        == ordinary.value.expected
        == "successful native source execution for the compiled expression"
    )
    assert "Overflow" in str(actual.value.received) and "Overflow" in str(ordinary.value.received)
    assert actual.value.repair is not None and ordinary.value.repair is not None
    assert actual.value.repair.action == ordinary.value.repair.action
    # A native engine failure has no evaluated aggregate coordinate witness.
    assert actual.value.attribute_context is None


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
    if contribution:
        selected = _change(
            session.observe({"q": metric}, by=(current,), where=phase == "current"),
            session.observe({"q": metric}, by=(baseline,), where=phase == "base"),
        )
        result = selected.attribute(by=(channel,)).compute()
    else:
        left = session.observe({"q": metric}, by=(current, channel), where=phase == "current")
        right = session.observe({"q": metric}, by=(baseline, channel), where=phase == "base")
        result = left.compare(
            {"base": right},
            on={"base": tuple(zip(left.axes, right.axes, strict=True))},
            match={"base": mv.ordinal()},
        ).compute()
    rows = result.to_pandas()
    assert len(result.axes) == 2
    assert rows["detail.current.q" if contribution else "q"].tolist() == [320, 160]
    assert rows["detail.baseline.q" if contribution else "base.baseline.q"].tolist() == [
        200,
        200,
    ]
    assert rows["detail.absolute.q" if contribution else "base.absolute.q"].tolist() == [
        120,
        -40,
    ]
    if contribution:
        assert rows["contribution.q"].tolist() == [120, -40]


@pytest.mark.parametrize("paired", (False, True))
def test_interval_parent_builds_without_source_read(
    session: Session, monkeypatch: pytest.MonkeyPatch, paired: bool
) -> None:
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
    selected = parent.select({"q": parent["left.q"] - parent["right.q"]}) if paired else parent
    pending = selected.attribute(by=(channel,))
    pending.show(n=0)


@pytest.mark.release
@pytest.mark.parametrize("shape", ("compare", "paired", "right_level"))
def test_captured_interval_parent_keeps_original_endpoint_scopes(
    session: Session, shape: str
) -> None:
    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    phase = session.catalog.fields.get("sales.facts.phase")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    current = session.observe(
        {"q": amount.sum(time=day)},
        by=(session.time_scope(start="2026-08-01", end="2026-08-03"), region),
        where=phase == "current",
    )
    baseline = session.observe(
        {"q": amount.sum(time=day)},
        by=(session.time_scope(start="2026-07-01", end="2026-07-03"), region),
        where=phase == "base",
    )
    pairs = tuple(zip(current.axes, baseline.axes, strict=True))
    if shape == "compare":
        compared = current.compare(
            {"base": baseline}, on={"base": pairs}, match={"base": mv.ordinal()}
        )
        selected = compared.select({"q": compared["base.absolute.q"]}).compute()
    else:
        paired = current.align(baseline, on=pairs, match=mv.ordinal())
        selected = paired.select(
            {
                "q": paired["right.q"]
                if shape == "right_level"
                else paired["left.q"] - paired["right.q"]
            }
        ).compute()
    report = selected.attribute(by=(channel,)).compute()
    rows = report.to_pandas()
    assert len(report.axes) == 3
    assert rows["contribution.q"].tolist() == ([200, 200] if shape == "right_level" else [120, -40])
    assert rows["total.q"].tolist() == ([400, 400] if shape == "right_level" else [80, 80])
    assert rows["observed_total.q"].tolist() == rows["total.q"].tolist()


def test_captured_selected_calendar_parent_keeps_exact_integers_and_saved_map(
    session: Session, tmp_path: Path
) -> None:
    exact = 2**53 + 1
    with duckdb.connect(str(tmp_path / "temporal.duckdb")) as connection:
        # Select August 2 uniquely; its baseline must remain July 2 after Top.
        connection.execute(
            "UPDATE facts SET amount=CASE id WHEN 1 THEN 150 WHEN 5 THEN ? WHEN 11 THEN ? ELSE amount END",
            [exact, exact - 60],
        )
    amount = session.catalog.fields.get("sales.facts.amount")
    day = session.catalog.fields.get("sales.facts.day")
    phase = session.catalog.fields.get("sales.facts.phase")
    channel = session.catalog.fields.get("sales.facts.channel")
    current_calendar = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    baseline_calendar = session.time_scope(start="2026-07-01", end="2026-07-03").by(mv.grain("day"))
    current = session.observe(
        {"q": amount.sum(time=day)}, by=(current_calendar,), where=phase == "current"
    )
    baseline = session.observe(
        {"q": amount.sum(time=day)}, by=(baseline_calendar,), where=phase == "base"
    )
    comparison = current.compare(
        {"base": baseline},
        on={"base": ((current.axes[0], baseline.axes[0]),)},
        match={"base": mv.ordinal()},
    )
    selected = comparison.select({"q": comparison["base.absolute.q"]})
    selected = selected.top(1, by=selected["q"]).compute()
    report = selected.attribute(by=(channel,)).compute()
    rows = report.to_pandas()
    assert int(rows["detail.current.q"].iloc[0]) == exact
    assert int(rows["detail.baseline.q"].iloc[0]) == exact - 60
    assert rows["contribution.q"].tolist() == [60, -20]
    assert rows["total.q"].tolist() == [40, 40]
    assert str(rows["detail.current.q"].dtype) == "decimal128(38, 0)[pyarrow]"
