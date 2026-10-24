"""Private Attribute preparation retains independently checked source roles."""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import NoReturn

import duckdb
import ibis.expr.types as ir
import pandas as pd
import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
from marivo._grouping import Grouping
from marivo.analysis.binding.frame_attribute import source_inputs
from marivo.analysis.binding.frame_attribute_inputs import AttributeInputRequest
from marivo.analysis.binding.observe import ContributionBinding
from marivo.analysis.compiler.frame_plan import CompiledFramePlan, CompiledSourceFrame, plan_frame
from marivo.analysis.compiler.graph_plan import SourceRead, read_attribute, require_reduction
from marivo.analysis.core.frame_expression import FrameExpression
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.dsl.population import Population
from marivo.analysis.materialization.frame_evaluation import evaluate_attribute_inputs
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import CompiledRead
from marivo.datasource.timezone import DatasourceEngineTimezone
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import terminal_runs


def _compare(current: AnalysisFrame, baseline: AnalysisFrame) -> AnalysisFrame:
    return current.compare(
        {"reference": baseline},
        on={"reference": tuple(zip(current.axes, baseline.axes, strict=True))},
    )


def _comparison_inputs(
    current: AnalysisFrame,
    baseline: AnalysisFrame,
    *,
    by: tuple[Deferred | Grouping, ...] | Population,
) -> AttributeInputRequest:
    compared = _compare(current, baseline)
    parent = compared.select({"q": compared["reference.absolute.q"]})
    assert parent._derivation is not None
    return source_inputs(parent, parent._derivation.outputs[0], by)


def _level_inputs(
    parent: AnalysisFrame, *, by: tuple[Deferred | Grouping, ...] | Population
) -> AttributeInputRequest:
    """Prepare one source target without repeating identical comparison endpoints."""
    assert parent._derivation is not None
    return source_inputs(parent, parent._derivation.outputs[0], by)


@pytest.fixture
def session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    database = tmp_path / "inputs.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE facts(tenant VARCHAR, id BIGINT, region VARCHAR, channel VARCHAR, value BIGINT, weight BIGINT)"
        )
        connection.execute(
            "INSERT INTO facts VALUES ('t',1,'east','A',10,1),('t',2,'east','B',NULL,9),('t',3,NULL,'A',30,3)"
        )
        connection.execute("CREATE TABLE otherfacts AS SELECT * FROM facts")
        connection.execute("CREATE TABLE tags(id BIGINT, label VARCHAR, kind VARCHAR)")
        connection.execute("INSERT INTO tags VALUES (1,'x','online'),(2,'y','store')")
        connection.execute("CREATE TABLE links(id BIGINT, fact_id BIGINT, tag_id BIGINT)")
        connection.execute("INSERT INTO links VALUES (1,1,1),(2,1,1),(3,1,2),(4,2,1)")
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + f"md.duckdb(name='warehouse', path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Analytics', default=True)\n",
            "sales/model.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nfacts = ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table('facts'), primary_key=['tenant','id'])\nregion = ms.dimension_column(name='region', entity=facts, column='region')\nchannel = ms.dimension_column(name='channel', entity=facts, column='channel')\nvalue = ms.measure_column(name='value', entity=facts, column='value', unit='USD', additivity=ms.additive_all())\nweight = ms.measure_column(name='weight', entity=facts, column='weight', unit='1', additivity=ms.additive_all())\nfact_id = ms.dimension_column(name='id', entity=facts, column='id')\nfact_tenant = ms.dimension_column(name='tenant', entity=facts, column='tenant')\notherfacts = ms.entity(name='otherfacts', datasource=ms.ref.datasource('warehouse'), source=md.table('otherfacts'), primary_key=['tenant','id'])\nother_value = ms.measure_column(name='value', entity=otherfacts, column='value', unit='USD', additivity=ms.additive_all())\nother_id = ms.dimension_column(name='id', entity=otherfacts, column='id')\nother_tenant = ms.dimension_column(name='tenant', entity=otherfacts, column='tenant')\nother_fact = ms.relationship(name='other_fact', from_entity=otherfacts, to_entity=facts, keys=[ms.join_on(other_tenant, fact_tenant), ms.join_on(other_id, fact_id)])\ntags = ms.entity(name='tags', datasource=ms.ref.datasource('warehouse'), source=md.table('tags'), primary_key=['id'])\ntag_id = ms.dimension_column(name='id', entity=tags, column='id')\nlabel = ms.dimension_column(name='label', entity=tags, column='label')\nkind = ms.dimension_column(name='kind', entity=tags, column='kind')\nlinks = ms.entity(name='links', datasource=ms.ref.datasource('warehouse'), source=md.table('links'), primary_key=['id'])\nlink_fact = ms.dimension_column(name='fact_id', entity=links, column='fact_id')\nlink_tag = ms.dimension_column(name='tag_id', entity=links, column='tag_id')\nfact_links = ms.relationship(name='fact_links', from_entity=facts, to_entity=links, keys=[ms.join_on(fact_id, link_fact)])\nlink_tags = ms.relationship(name='link_tags', from_entity=links, to_entity=tags, keys=[ms.join_on(link_tag, tag_id)])\n",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.create("inputs", timezone="UTC")


def test_same_root_ratio_has_direct_parent_and_fine_reads_per_endpoint(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource.adapters import SourceSession

    value = session.catalog.fields.get("sales.facts.value")
    weight = session.catalog.fields.get("sales.facts.weight")
    channel = session.catalog.fields.get("sales.facts.channel")
    request = _comparison_inputs(
        session.observe({"q": value.sum() / weight.sum()}, by=()),
        session.observe({"q": value.sum() / weight.sum()}, by=()),
        by=(channel,),
    )
    reads = []
    original = SourceSession.execute

    def execute(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        rows = original(self, issued)
        if "__attribute_kind" in rows:
            reads.append(issued)
        return rows

    monkeypatch.setattr(SourceSession, "execute", execute)
    prepared = evaluate_attribute_inputs(request)
    assert len(reads) == 4
    assert len(prepared.attribute_inputs) == 4
    assert all(len(root.layout.leaves) == 2 for root in prepared.attribute_inputs)
    assert not any(
        receipt.check_id
        in {"attribute.scoped_identity_coverage", "attribute.disjoint_joint_partition"}
        for root in prepared.attribute_inputs
        for receipt in root.receipts
    )
    for root in prepared.attribute_inputs:
        if root.layout.scope != "parent":
            continue
        assert len(root.parent) == 1
        columns = []
        for column in root.parent:
            assert isinstance(column, str)
            if column.endswith("additive_value"):
                columns.append(column)
        assert root.parent.loc[[0], columns].iloc[0].tolist() == [40, 13]
    assert len(prepared.table) == 2


@pytest.mark.parametrize("weighted", (False, True))
def test_native_components_share_exact_effective_mask(session: Session, weighted: bool) -> None:
    value = session.catalog.fields.get("sales.facts.value")
    weight = session.catalog.fields.get("sales.facts.weight")
    channel = session.catalog.fields.get("sales.facts.channel")
    region = session.catalog.fields.get("sales.facts.region")
    metric = value.weighted_mean(weight=weight) if weighted else value.mean()
    output = evaluate_attribute_inputs(
        _level_inputs(session.observe({"q": metric}, where=region == "east", by=()), by=(channel,))
    )
    for root in output.attribute_inputs:
        if root.layout.scope != "parent":
            continue
        leaf = root.layout.leaves[0]
        values = {role: root.parent.loc[0, column.slot_id] for role, column in leaf.roles}
        assert root.parent.loc[0, leaf.support.slot_id] == 2
        assert root.parent.loc[0, leaf.effective.slot_id] == 1
        assert root.parent.loc[0, require_reduction(leaf.read).value_slot] == 10
        if weighted:
            assert values == {"pair_product_sum": 10, "pair_weight_sum": 1, "pair_count": 1}
        else:
            assert values == {"valid_value_sum": 10, "valid_value_count": 1}


def test_distinct_count_requires_value_partition(session: Session, tmp_path: Path) -> None:
    from marivo.analysis.errors import AnalysisError

    with duckdb.connect(str(tmp_path / "inputs.duckdb")) as connection:
        connection.execute("UPDATE facts SET value=10 WHERE id=2")
    value = session.catalog.fields.get("sales.facts.value")
    channel = session.catalog.fields.get("sales.facts.channel")
    with pytest.raises(AnalysisError, match="overlapping_groups"):
        evaluate_attribute_inputs(
            _level_inputs(session.observe({"q": value.count_distinct()}, by=()), by=(channel,))
        )


def test_attribute_trusts_declared_root_identity(session: Session, tmp_path: Path) -> None:
    with duckdb.connect(str(tmp_path / "inputs.duckdb")) as connection:
        connection.execute("INSERT INTO facts SELECT * FROM facts WHERE id=1")
    value = session.catalog.fields.get("sales.facts.value")
    channel = session.catalog.fields.get("sales.facts.channel")
    result = evaluate_attribute_inputs(
        _level_inputs(session.observe({"q": value.sum()}, by=()), by=(channel,))
    )
    root = next(root for root in result.attribute_inputs if root.layout.scope == "parent")
    assert root.parent[require_reduction(root.layout.leaves[0].read).value_slot].tolist() == [50]


@pytest.mark.parametrize("empty", (False, True))
def test_selected_empty_parent_retains_known_empty_without_fine_rows(
    session: Session, empty: bool
) -> None:
    value = session.catalog.fields.get("sales.facts.value")
    channel = session.catalog.fields.get("sales.facts.channel")
    region = session.catalog.fields.get("sales.facts.region")
    parents = mv.coordinates(by=(region,), keys=() if empty else (("absent",),))
    prepared = evaluate_attribute_inputs(
        _level_inputs(
            session.observe({"q": value.sum()}, by=parents, where=region == "absent"), by=(channel,)
        )
    )
    assert prepared.table.empty
    for root in prepared.attribute_inputs:
        if root.layout.scope != "parent":
            continue
        assert len(root.parent) == (0 if empty else 1)
        assert root.parent_state == (() if empty else ("known_empty",))
        if not empty:
            leaf = root.layout.leaves[0]
            assert root.parent.loc[0, leaf.support.slot_id] == 0
            assert root.parent.loc[0, require_reduction(leaf.read).value_slot] == 0
            assert root.parent.loc[0, leaf.roles[0][1].slot_id] == 0


def test_membership_parent_overlap_is_independent_and_correlated(session: Session) -> None:
    value = session.catalog.fields.get("sales.facts.value")
    channel = session.catalog.fields.get("sales.facts.channel")
    label = session.catalog.fields.get("sales.tags.label")
    kind = session.catalog.fields.get("sales.tags.kind")
    path = (
        session.catalog.relationships.get("sales.fact_links"),
        session.catalog.relationships.get("sales.link_tags"),
    )
    membership = mv.membership((label, kind), via=(path,), unmatched="exclude")
    parents = mv.coordinates(by=(label, kind), keys=(("x", "online"), ("y", "store")))
    prepared = evaluate_attribute_inputs(
        _level_inputs(session.observe({"q": value.sum()}, by=(parents, membership)), by=(channel,))
    )
    assert len(prepared.table) == 3
    for root in prepared.attribute_inputs:
        if root.layout.scope != "parent":
            continue
        assert set(root.parent.iloc[:, :2].itertuples(index=False, name=None)) == {
            ("x", "online"),
            ("y", "store"),
        }
        leaf = root.layout.leaves[0]
        assert sorted(root.parent[leaf.support.slot_id].tolist()) == [1, 2]
        assert root.parent[leaf.roles[0][1].slot_id].tolist() == [10, 10]


@pytest.mark.parametrize("missing", (False, True))
def test_new_membership_proves_coverage_before_exclusion_and_disjointness(
    session: Session, missing: bool
) -> None:
    from marivo.analysis.errors import AnalysisError

    value = session.catalog.fields.get("sales.facts.value")
    region = session.catalog.fields.get("sales.facts.region")
    fact_id = session.catalog.fields.get("sales.facts.id")
    label = session.catalog.fields.get("sales.tags.label")
    path = (
        session.catalog.relationships.get("sales.fact_links"),
        session.catalog.relationships.get("sales.link_tags"),
    )
    membership = mv.membership(label, via=(path,), unmatched="exclude")
    with pytest.raises(AnalysisError, match="undefined_input" if missing else "overlapping_groups"):
        evaluate_attribute_inputs(
            _level_inputs(
                session.observe(
                    {"q": value.sum()},
                    where=region.isnull() if missing else fact_id == 1,
                    by=(),
                ),
                by=(membership,),
            )
        )


def test_identical_membership_assignments_are_idempotent(session: Session, tmp_path: Path) -> None:
    with duckdb.connect(str(tmp_path / "inputs.duckdb")) as connection:
        connection.execute("DELETE FROM links WHERE tag_id=2")
    value = session.catalog.fields.get("sales.facts.value")
    fact_id = session.catalog.fields.get("sales.facts.id")
    label = session.catalog.fields.get("sales.tags.label")
    path = (
        session.catalog.relationships.get("sales.fact_links"),
        session.catalog.relationships.get("sales.link_tags"),
    )
    membership = mv.membership(label, via=(path,), unmatched="exclude")
    prepared = evaluate_attribute_inputs(
        _level_inputs(
            session.observe({"q": value.sum()}, where=fact_id == 1, by=()), by=(membership,)
        )
    )
    assert len(prepared.table) == 1
    for root in prepared.attribute_inputs:
        if root.layout.scope != "parent":
            continue
        leaf = root.layout.leaves[0]
        assert root.parent.loc[0, leaf.support.slot_id] == 1
        assert root.parent.loc[0, leaf.roles[0][1].slot_id] == 10


def test_outer_fine_group_has_typed_empty_components(session: Session) -> None:
    value = session.catalog.fields.get("sales.facts.value")
    channel = session.catalog.fields.get("sales.facts.channel")
    prepared = evaluate_attribute_inputs(
        _comparison_inputs(
            session.observe({"q": value.sum()}, by=()),
            session.observe({"q": value.sum()}, where=channel == "A", by=()),
            by=(channel,),
        )
    )
    right_count = next(
        column
        for column in prepared.columns
        if column.name.startswith("right.fine") and column.name.endswith("input_count")
    )
    assert prepared.table[right_count.slot_id].tolist() == [2, 0]
    assert right_count.unit == "1"
    component = next(
        column
        for column in prepared.columns
        if column.name.startswith("right.fine") and column.name.endswith("additive_value")
    )
    assert component.unit == "USD"
    assert prepared.table[component.slot_id].tolist() == [40, 0]


def test_default_path_collects_no_attribute_input_proof(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.materialization import graph_source_execution

    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("default requested Attribute input proof")

    monkeypatch.setattr(graph_source_execution, "prepare_attribute_sources", unexpected)
    value = session.catalog.fields.get("sales.facts.value")
    channel = session.catalog.fields.get("sales.facts.channel")
    assert (
        len(
            _compare(
                session.observe({"q": value.sum()}, by=(channel,)),
                session.observe({"q": value.sum()}, by=(channel,)),
            )
            .compute()
            .to_pandas()
        )
        == 2
    )


def test_unknown_physical_leaf_fails_before_business_read(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    import ibis

    from marivo.analysis.errors import AnalysisError
    from marivo.analysis.materialization import query_preparation
    from marivo.datasource.adapters import SourceSession

    original = query_preparation.prepare_fields
    calls = []

    def prepare(
        contribution: ContributionBinding,
        entities: Mapping[str, ir.Table],
        state: CompiledSemanticState,
        reader: DatasourceEngineTimezone,
        *,
        root_table: ir.Table | None = None,
    ) -> query_preparation.PreparedFields:
        result = original(
            contribution,
            entities,
            state,
            reader,
            root_table=root_table,
        )
        unknown = ibis.table({"unknown": "int64"}, name="unknown_source")
        return replace(result, table=result.table.cross_join(unknown))

    def execute(*args: object, **kwargs: object) -> pd.DataFrame:
        calls.append(args)
        raise AssertionError("unqualified input reached business read")

    value = session.catalog.fields.get("sales.facts.value")
    captured = session.observe({"q": value.sum()}).compute()
    monkeypatch.setattr(query_preparation, "prepare_fields", prepare)
    monkeypatch.setattr(SourceSession, "execute", execute)
    value = session.catalog.fields.get("sales.facts.value")
    channel = session.catalog.fields.get("sales.facts.channel")
    with pytest.raises(AnalysisError, match="unqualified_contribution_route"):
        evaluate_attribute_inputs(
            _comparison_inputs(
                captured,
                captured,
                by=(channel,),
            )
        )
    assert not calls


def test_decimal_native_mean_preserves_original_finisher_and_component_types(
    session: Session, tmp_path: Path
) -> None:
    from decimal import Decimal

    from marivo.analysis.core.data_types import DecimalDataType, ScalarDataType

    with duckdb.connect(str(tmp_path / "inputs.duckdb")) as connection:
        connection.execute("ALTER TABLE facts ALTER value TYPE DECIMAL(18,2)")
        connection.execute(
            "UPDATE facts SET value=CASE id WHEN 1 THEN 10.01 WHEN 2 THEN 20.02 ELSE 30.03 END"
        )
    value = session.catalog.fields.get("sales.facts.value")
    channel = session.catalog.fields.get("sales.facts.channel")
    prepared = evaluate_attribute_inputs(
        _level_inputs(session.observe({"q": value.mean()}, by=()), by=(channel,))
    )
    for root in prepared.attribute_inputs:
        if root.layout.scope != "parent":
            continue
        leaf = root.layout.leaves[0]
        original = next(
            column
            for column in root.layout.columns
            if column.slot_id == require_reduction(leaf.read).value_slot
        )
        components = dict(leaf.roles)
        assert isinstance(original.dtype, DecimalDataType)
        assert isinstance(components["valid_value_sum"].dtype, DecimalDataType)
        assert components["valid_value_count"].dtype == ScalarDataType("int64")
        assert root.parent.loc[0, original.slot_id] == Decimal("20.02")
        assert root.parent.loc[0, components["valid_value_sum"].slot_id] == Decimal("60.06")


def test_duration_native_mean_keeps_exact_ticks_and_original_finisher(
    session: Session, tmp_path: Path
) -> None:
    import pyarrow as pa

    from marivo.analysis.core.data_types import DecimalDataType, DurationDataType

    with duckdb.connect(str(tmp_path / "inputs.duckdb")) as connection:
        connection.execute(
            "ALTER TABLE facts ALTER value TYPE INTERVAL USING value * INTERVAL '1 microsecond'"
        )
        connection.execute(
            "UPDATE facts SET value=CASE id WHEN 1 THEN INTERVAL '9007199254740993 microseconds' WHEN 2 THEN INTERVAL '9007199254740995 microseconds' ELSE NULL END"
        )
    value = session.catalog.fields.get("sales.facts.value")
    channel = session.catalog.fields.get("sales.facts.channel")
    prepared = evaluate_attribute_inputs(
        _level_inputs(session.observe({"q": value.mean()}, by=()), by=(channel,))
    )
    for root in prepared.attribute_inputs:
        if root.layout.scope != "parent":
            continue
        leaf = root.layout.leaves[0]
        original = next(
            column
            for column in root.layout.columns
            if column.slot_id == require_reduction(leaf.read).value_slot
        )
        assert original.dtype == DurationDataType("us")
        assert pa.array(root.parent[original.slot_id]).cast(pa.int64()).to_pylist() == [
            9007199254740994
        ]
        components = dict(leaf.roles)
        assert isinstance(components["valid_value_sum"].dtype, DecimalDataType)
        assert root.parent[components["valid_value_sum"].slot_id].tolist() == [18014398509481988]


def test_native_mean_retains_wider_sum_without_imposing_sum_int64_overflow(
    session: Session, tmp_path: Path
) -> None:
    from decimal import Decimal

    from marivo.analysis.core.data_types import DecimalDataType

    with duckdb.connect(str(tmp_path / "inputs.duckdb")) as connection:
        connection.execute("UPDATE facts SET value=9223372036854775807")
    value = session.catalog.fields.get("sales.facts.value")
    channel = session.catalog.fields.get("sales.facts.channel")
    prepared = evaluate_attribute_inputs(
        _level_inputs(session.observe({"q": value.mean()}, by=()), by=(channel,))
    )
    for root in prepared.attribute_inputs:
        if root.layout.scope != "parent":
            continue
        leaf = root.layout.leaves[0]
        component = dict(leaf.roles)["valid_value_sum"]
        assert isinstance(component.dtype, DecimalDataType)
        assert root.parent.loc[0, component.slot_id] == Decimal(3 * (2**63 - 1))


@pytest.mark.release
def test_additional_root_has_two_independent_roles_without_fact_fanout(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource.adapters import SourceSession

    value = session.catalog.fields.get("sales.facts.value")
    other = session.catalog.fields.get("sales.otherfacts.value")
    channel = session.catalog.fields.get("sales.facts.channel")
    calls = []
    original = SourceSession.execute

    def execute(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        rows = original(self, issued)
        if "__attribute_kind" in rows:
            calls.append(issued)
        return rows

    monkeypatch.setattr(SourceSession, "execute", execute)
    prepared = evaluate_attribute_inputs(
        _comparison_inputs(
            session.observe({"q": value.sum() + other.sum()}, by=()),
            session.observe({"q": value.sum() + other.sum()}, by=()),
            by=(channel,),
        )
    )
    assert len(calls) == 8
    assert len(prepared.attribute_inputs) == 8
    assert {root.layout.leaves[0].read.source_id for root in prepared.attribute_inputs} == {
        "sales.facts",
        "sales.otherfacts",
    }
    for root in prepared.attribute_inputs:
        if root.layout.scope != "parent":
            continue
        leaf = root.layout.leaves[0]
        assert root.parent[leaf.roles[0][1].slot_id].tolist() == [40]
        assert root.parent[leaf.support.slot_id].tolist() == [3]


def test_plan_and_mutating_execute_batches_bind_parent_and_all_fine_inputs(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.compiler.graph_plan import FrameCompute, SourceRead
    from marivo.analysis.core.attribute import AttributeInputProgram
    from marivo.analysis.materialization import frame_evaluation
    from marivo.datasource.adapters import SourceSession

    value = session.catalog.fields.get("sales.facts.value")
    channel = session.catalog.fields.get("sales.facts.channel")
    captured = []
    compile_plan = plan_frame

    def plan(
        root: FrameExpression,
        source_plans: Mapping[str, CompiledSourceFrame],
        *,
        frame_inputs: Mapping[str, FrameExpression] | None = None,
    ) -> CompiledFramePlan:
        compiled = compile_plan(root, source_plans, frame_inputs=frame_inputs)
        captured.append(compiled)
        return compiled

    execute = SourceSession.execute
    calls = []

    def read(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        rows = execute(self, issued).copy()
        if "__attribute_kind" not in rows:
            return rows
        calls.append(issued)
        for name in rows:
            assert isinstance(name, str)
            if name.endswith("/value") or name.endswith(".additive_value"):
                rows[name] = rows[name] * len(calls)
        return rows

    monkeypatch.setattr(frame_evaluation, "plan_frame", plan)
    monkeypatch.setattr(SourceSession, "execute", read)
    prepared = evaluate_attribute_inputs(
        _comparison_inputs(
            session.observe({"q": value.sum()}, by=()),
            session.observe({"q": value.sum()}, by=()),
            by=(channel,),
        )
    )
    assert len(calls) == 4
    stages = captured[0].lowered.plan.stages
    roots = [
        stage
        for stage in stages
        if isinstance(stage, SourceRead) and read_attribute(stage) is not None
    ]
    bridges = [stage for stage in stages if stage.stage_id.startswith("attribute-bridge:")]
    assert len(roots) == 4 and len(bridges) == 2
    assert max(stages.index(stage) for stage in bridges) < min(
        stages.index(stage) for stage in roots
    )
    final = stages[-1]
    assert isinstance(final, FrameCompute) and isinstance(final.program, AttributeInputProgram)
    parents = []
    for root in prepared.attribute_inputs:
        if root.layout.scope != "parent":
            continue
        leaf = root.layout.leaves[0]
        mass = leaf.roles[0][1].slot_id
        fine_root = next(
            item
            for item in prepared.attribute_inputs
            if item.layout.scope == "fine" and item.layout.leaves[0].use == leaf.use
        )
        assert root.parent[mass].iloc[0] != fine_root.fine[mass].dropna().sum()
        assert all(receipt.stage_id == root.stage_id for receipt in root.receipts)
        parents.append(root.parent[mass].iloc[0])
    assert parents == [40, 120]
    assert {binding.input_index for binding in final.program.reads} == set(range(5, 9))
    assert {final.inputs[binding.input_index] for binding in final.program.reads} == {
        stage.stage_id for stage in roots
    }


@pytest.mark.release
def test_complete_compound_identity_parent_mapping(session: Session) -> None:
    value = session.catalog.fields.get("sales.facts.value")
    channel = session.catalog.fields.get("sales.facts.channel")
    entity = session.catalog.entities.get("sales.facts")
    parents = mv.coordinates(by=(mv.identity(entity),), keys=((("t", 1),), (("t", 3),)))
    prepared = evaluate_attribute_inputs(
        _comparison_inputs(
            session.observe({"q": value.sum()}, by=parents, where=mv.in_population(parents)),
            session.observe({"q": value.sum()}, by=parents, where=mv.in_population(parents)),
            by=(channel,),
        )
    )
    assert len(prepared.keys) == 3
    assert list(prepared.table.iloc[:, :2].itertuples(index=False, name=None)) == [
        ("t", 1),
        ("t", 3),
    ]
    for root in prepared.attribute_inputs:
        if root.layout.scope != "parent":
            continue
        assert len(root.layout.parent_keys) == 2
        assert root.parent.iloc[:, :2].values.tolist() == [["t", 1], ["t", 3]]


def test_duplicate_complete_compound_parent_aggregate_is_rejected(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.datasource.adapters import SourceSession

    execute = SourceSession.execute
    duplicated: list[tuple[object, object]] = []

    def read(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        rows = execute(self, issued)
        if duplicated or "__attribute_kind" not in rows:
            return rows
        selected = rows.loc[
            (rows["__attribute_kind"] == 1) & (rows["key.0"] == "t") & (rows["key.1"] == 1)
        ]
        assert len(selected) == 1
        corrupted = pd.concat([rows, selected.copy()], ignore_index=True)
        pd.testing.assert_frame_equal(corrupted.iloc[: len(rows)], rows.reset_index(drop=True))
        pd.testing.assert_frame_equal(
            corrupted.iloc[len(rows) :].reset_index(drop=True), selected.reset_index(drop=True)
        )
        duplicated.append((selected["key.0"].iloc[0], selected["key.1"].iloc[0]))
        return corrupted

    monkeypatch.setattr(SourceSession, "execute", read)
    value = session.catalog.fields.get("sales.facts.value")
    channel = session.catalog.fields.get("sales.facts.channel")
    entity = session.catalog.entities.get("sales.facts")
    parents = mv.coordinates(by=(mv.identity(entity),), keys=((("t", 1),), (("t", 3),)))
    with pytest.raises(MaterializationError) as caught:
        evaluate_attribute_inputs(
            _level_inputs(
                session.observe({"q": value.sum()}, by=parents, where=mv.in_population(parents)),
                by=(channel,),
            )
        )
    assert duplicated == [("t", 1)]
    assert caught.value.stage == "stage_output"
    assert caught.value.location == "dataset.stage_output"
    assert caught.value.expected == "a complete unique stage key ('key.0', 'key.1')"
    assert caught.value.received == "null or duplicate key values"
    assert caught.value.repair is not None
    assert (
        caught.value.repair.action
        == "Correct the source field type or stage layout; Marivo does not cast lossy values back."
    )


def test_missing_checked_parent_aggregate_cannot_turn_into_known_empty(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.core.attribute import AttributeInputError
    from marivo.datasource.adapters import SourceSession

    execute = SourceSession.execute

    def read(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        rows = execute(self, issued)
        return (
            rows.loc[rows["__attribute_kind"] != 1].reset_index(drop=True)
            if "__attribute_kind" in rows
            else rows
        )

    monkeypatch.setattr(SourceSession, "execute", read)
    value = session.catalog.fields.get("sales.facts.value")
    channel = session.catalog.fields.get("sales.facts.channel")
    with pytest.raises(AttributeInputError) as caught:
        evaluate_attribute_inputs(
            _level_inputs(session.observe({"q": value.sum()}, by=()), by=(channel,))
        )
    assert caught.value.reason == "missing_component_proof"


def test_empty_zero_axis_parent_has_no_required_weighted_total(session: Session) -> None:
    value = session.catalog.fields.get("sales.facts.value")
    weight = session.catalog.fields.get("sales.facts.weight")
    channel = session.catalog.fields.get("sales.facts.channel")
    empty = mv.coordinates(by=(), keys=())
    prepared = evaluate_attribute_inputs(
        _level_inputs(
            session.observe(
                {"q": value.weighted_mean(weight=weight)}, by=empty, where=mv.in_population(empty)
            ),
            by=(channel,),
        )
    )
    assert prepared.table.empty
    assert all(root.parent.empty and (not root.parent_state) for root in prepared.attribute_inputs)


def test_unproved_sample_sequence_stays_closed_before_source_read(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.binding.attribute_rules import AttributeRuleError
    from marivo.datasource.adapters import SourceSession

    calls = []
    monkeypatch.setattr(SourceSession, "execute", lambda *args: calls.append(args))
    value = session.catalog.fields.get("sales.facts.value")
    channel = session.catalog.fields.get("sales.facts.channel")
    region = session.catalog.fields.get("sales.facts.region")
    with pytest.raises(AttributeRuleError) as caught:
        _comparison_inputs(
            session.observe({"q": value.sum().per(channel).mean()}, by=()),
            session.observe({"q": value.sum().per(channel).mean()}, by=()),
            by=(region,),
        )
    assert caught.value.reason == "missing_component_proof"
    assert not calls


def _keyed_values(
    frame: pd.DataFrame, keys: tuple[str, ...], value: str
) -> dict[tuple[object, ...], object]:
    return {
        tuple(None if pd.isna(cell) else cell for cell in row[:-1]): row[-1]
        for row in frame.loc[:, [*keys, value]].itertuples(index=False, name=None)
    }


def test_finished_originals_follow_complete_keys_after_actual_batch_permutation(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource.adapters import SourceSession

    value = session.catalog.fields.get("sales.facts.value")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    original = SourceSession.execute
    reads = []

    def execute(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        frame = original(self, issued)
        if "__attribute_kind" in frame:
            reads.append(issued)
            parts = []
            for kind in (0, 1, 2, 3):
                part = frame.loc[frame["__attribute_kind"] == kind]
                parts.append(
                    part.sort_values(
                        [name for name in ("key.0", "key.1") if name in part],
                        ascending=False,
                        na_position="first",
                    )
                )
            return pd.concat(parts, ignore_index=True)
        return frame

    monkeypatch.setattr(SourceSession, "execute", execute)
    output = evaluate_attribute_inputs(
        _comparison_inputs(
            session.observe(
                {"q": value.sum()}, by=mv.coordinates(by=(region,), keys=(("east",), (None,)))
            ),
            session.observe(
                {"q": value.sum()}, by=mv.coordinates(by=(region,), keys=(("east",), (None,)))
            ),
            by=(channel,),
        )
    )
    assert len(reads) == 4
    assert len(output.table) == 3
    for root in output.attribute_inputs:
        if root.layout.scope != "parent":
            continue
        assert len(root.parent) == 2
        assert len(root.layout.identity) == 2
        leaf = root.layout.leaves[0]
        value_slot = require_reduction(leaf.read).value_slot
        component = leaf.roles[0][1].slot_id
        for slot in (value_slot, component):
            assert _keyed_values(root.parent, root.layout.parent_keys, slot) == {
                ("east",): 10,
                (None,): 30,
            }
            fine_root = next(
                item
                for item in output.attribute_inputs
                if item.layout.scope == "fine" and item.layout.leaves[0].use == leaf.use
            )
            fine = _keyed_values(fine_root.fine, fine_root.layout.keys, slot)
            assert fine[None, "A"] == 30
            assert fine["east", "A"] == 10
            assert pd.isna([fine["east", "B"]])[0]


@pytest.mark.release
def test_native_mean_originals_follow_correlated_null_membership_parent_keys(
    session: Session,
) -> None:
    value = session.catalog.fields.get("sales.facts.value")
    channel = session.catalog.fields.get("sales.facts.channel")
    label = session.catalog.fields.get("sales.tags.label")
    kind = session.catalog.fields.get("sales.tags.kind")
    route = (
        session.catalog.relationships.get("sales.fact_links"),
        session.catalog.relationships.get("sales.link_tags"),
    )
    membership = mv.membership((label, kind), via=(route,), unmatched="null")
    output = evaluate_attribute_inputs(
        _comparison_inputs(
            session.observe(
                {"q": value.mean()},
                by=(
                    mv.coordinates(
                        by=(label, kind), keys=((None, None), ("x", "online"), ("y", "store"))
                    ),
                    membership,
                ),
            ),
            session.observe(
                {"q": value.mean()},
                by=(
                    mv.coordinates(
                        by=(label, kind), keys=((None, None), ("x", "online"), ("y", "store"))
                    ),
                    membership,
                ),
            ),
            by=(channel,),
        )
    )
    for root in output.attribute_inputs:
        if root.layout.scope != "parent":
            continue
        leaf = root.layout.leaves[0]
        assert _keyed_values(
            root.parent, root.layout.parent_keys, require_reduction(leaf.read).value_slot
        ) == {(None, None): 30, ("x", "online"): 10, ("y", "store"): 10}
        assert _keyed_values(root.parent, root.layout.parent_keys, leaf.support.slot_id) == {
            (None, None): 1,
            ("x", "online"): 2,
            ("y", "store"): 1,
        }


def test_mixed_known_empty_parent_preserves_exact_nullable_integer(
    session: Session, tmp_path: Path
) -> None:
    exact = 2**53 + 1
    with duckdb.connect(str(tmp_path / "inputs.duckdb")) as connection:
        connection.execute("UPDATE facts SET value=? WHERE id=1", [exact])
    value = session.catalog.fields.get("sales.facts.value")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    output = evaluate_attribute_inputs(
        _level_inputs(
            session.observe(
                {"q": value.sum()},
                where=region == "east",
                by=mv.coordinates(by=(region,), keys=(("east",), ("absent",))),
            ),
            by=(channel,),
        )
    )
    for root in output.attribute_inputs:
        if root.layout.scope != "parent":
            continue
        leaf = root.layout.leaves[0]
        original = _keyed_values(
            root.parent, root.layout.parent_keys, require_reduction(leaf.read).value_slot
        )
        assert original["east",] == exact
        assert original["absent",] == 0
        assert _keyed_values(root.parent, root.layout.parent_keys, leaf.support.slot_id) == {
            ("east",): 2,
            ("absent",): 0,
        }
        assert _keyed_values(root.parent, root.layout.parent_keys, leaf.roles[0][1].slot_id) == {
            ("east",): exact,
            ("absent",): 0,
        }
        states = dict(zip(root.parent["key.0"], root.parent_state, strict=True))
        assert states == {"east": "present", "absent": "known_empty"}


def test_nullable_integer_parent_keys_remain_exact_through_formula_and_completion(
    session: Session, tmp_path: Path
) -> None:
    exact = 2**53 + 1
    with duckdb.connect(str(tmp_path / "inputs.duckdb")) as connection:
        connection.execute(
            f"ALTER TABLE facts ALTER region TYPE BIGINT USING CASE WHEN region IS NULL THEN NULL ELSE {exact} END"
        )
    value = session.catalog.fields.get("sales.facts.value")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    parents = mv.coordinates(by=(region,), keys=((exact,), (None,)))
    population_rows = session.frame(parents).compute().to_pandas()
    assert {
        None if pd.isna(row[0]) else row[0]
        for row in population_rows.itertuples(index=False, name=None)
    } == {exact, None}
    observed = session.observe({"q": value.count()}, by=parents).compute().to_pandas()
    assert {
        None if pd.isna(row[0]) else row[0]: row[1]
        for row in observed.itertuples(index=False, name=None)
    } == {exact: 1, None: 1}
    default_rows = (
        _compare(
            session.observe(
                {"q": value.count()}, by=(region, channel), where=mv.in_population(parents)
            ),
            session.observe(
                {"q": value.count()}, by=(region, channel), where=mv.in_population(parents)
            ),
        )
        .compute()
        .to_pandas()
    )
    assert {
        tuple(None if pd.isna(cell) else cell for cell in row[:2]): row[2]
        for row in default_rows.itertuples(index=False, name=None)
    } == {(exact, "A"): 1, (exact, "B"): 0, (None, "A"): 1}
    output = evaluate_attribute_inputs(
        _level_inputs(session.observe({"q": value.count()}, by=parents), by=(channel,))
    )
    for root in output.attribute_inputs:
        if root.layout.scope != "parent":
            continue
        leaf = root.layout.leaves[0]
        assert _keyed_values(
            root.parent, root.layout.parent_keys, require_reduction(leaf.read).value_slot
        ) == {(exact,): 1, (None,): 1}


@pytest.mark.parametrize("case", ("missing", "missing_outside_where", "ambiguous_predicate"))
def test_original_scalar_lookup_trusts_declared_matches(
    session: Session, tmp_path: Path, case: str
) -> None:
    value = session.catalog.fields.get("sales.otherfacts.value")
    identifier = session.catalog.fields.get("sales.otherfacts.id")
    channel = session.catalog.fields.get("sales.facts.channel")
    with duckdb.connect(str(tmp_path / "inputs.duckdb")) as connection:
        if case == "ambiguous_predicate":
            connection.execute("INSERT INTO facts VALUES ('t',1,'east','B',999,1)")
            connection.execute("UPDATE otherfacts SET value=20 WHERE id=2")
        else:
            connection.execute("DELETE FROM facts WHERE id=3")
    where = (
        channel == "B"
        if case == "ambiguous_predicate"
        else identifier == 1
        if case == "missing_outside_where"
        else None
    )
    if case == "missing_outside_where":
        ordinary_rows = (
            session.observe({"q": value.sum()}, by=(channel,), where=where).compute().to_pandas()
        )
        assert ordinary_rows["value", "q"].tolist() == [10]
        output = evaluate_attribute_inputs(
            _level_inputs(session.observe({"q": value.sum()}, where=where, by=()), by=(channel,))
        )
        for root in output.attribute_inputs:
            if root.layout.scope != "parent":
                continue
            leaf = root.layout.leaves[0]
            assert root.parent[require_reduction(leaf.read).value_slot].tolist() == [10]
        return
    rows = session.observe({"q": value.sum()}, by=(channel,), where=where).compute().to_pandas()
    assert rows[("value", "q")].sum() == (30 if case == "ambiguous_predicate" else 40)
    output = evaluate_attribute_inputs(
        _level_inputs(session.observe({"q": value.sum()}, where=where, by=()), by=(channel,))
    )
    assert not any(
        "prepare_check" in r.check_id for root in output.attribute_inputs for r in root.receipts
    )


@pytest.mark.release
def test_preparation_check_domain_respects_genuine_parent_scope(
    session: Session, tmp_path: Path
) -> None:
    with duckdb.connect(str(tmp_path / "inputs.duckdb")) as connection:
        connection.execute("DELETE FROM facts WHERE id=3")
    value = session.catalog.fields.get("sales.otherfacts.value")
    identifier = session.catalog.fields.get("sales.otherfacts.id")
    channel = session.catalog.fields.get("sales.facts.channel")
    output = evaluate_attribute_inputs(
        _comparison_inputs(
            session.observe(
                {"q": value.sum()},
                by=mv.coordinates(by=(identifier,), keys=((1,),)),
                where=identifier == 1,
            ),
            session.observe(
                {"q": value.sum()},
                by=mv.coordinates(by=(identifier,), keys=((1,),)),
                where=identifier == 1,
            ),
            by=(channel,),
        )
    )
    for root in output.attribute_inputs:
        if root.layout.scope != "parent":
            continue
        leaf = root.layout.leaves[0]
        assert root.parent[require_reduction(leaf.read).value_slot].tolist() == [10]


@pytest.mark.release
def test_known_empty_weighted_parent_retains_na_original_total(session: Session) -> None:
    value = session.catalog.fields.get("sales.facts.value")
    weight = session.catalog.fields.get("sales.facts.weight")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    output = evaluate_attribute_inputs(
        _comparison_inputs(
            session.observe(
                {"q": value.weighted_mean(weight=weight)},
                where=region == "absent",
                by=mv.coordinates(by=(region,), keys=(("absent",),)),
            ),
            session.observe(
                {"q": value.weighted_mean(weight=weight)},
                where=region == "absent",
                by=mv.coordinates(by=(region,), keys=(("absent",),)),
            ),
            by=(channel,),
        )
    )
    for root in output.attribute_inputs:
        if root.layout.scope != "parent":
            continue
        leaf = root.layout.leaves[0]
        assert root.parent_state == ("known_empty",)
        assert root.parent[require_reduction(leaf.read).value_slot].isna().all()


@pytest.mark.parametrize("failing_scope", ("parent", "fine"))
def test_original_numeric_failure_uses_actual_aggregate_coordinate_scope(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing_scope: str
) -> None:
    from marivo.analysis.materialization import graph_source_execution
    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.datasource.adapters import SourceSession

    value = session.catalog.fields.get("sales.facts.value")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    valid = 1e308
    with duckdb.connect(str(tmp_path / "inputs.duckdb")) as connection:
        connection.execute("ALTER TABLE facts ALTER value TYPE DOUBLE")
    captured = session.observe(
        {"q": value.sum()},
        by=mv.coordinates(by=(region,), keys=(("east",),)),
        where=region == "east",
    ).compute()
    with duckdb.connect(str(tmp_path / "inputs.duckdb")) as connection:
        connection.execute("UPDATE facts SET value=? WHERE region='east'", [valid])
        if failing_scope == "fine":
            connection.execute("UPDATE facts SET value=? WHERE id=2", [-valid])
            connection.execute("INSERT INTO facts VALUES ('t',4,'east','A',?,1)", [valid])
    failed_axes: tuple[Deferred, ...]
    if failing_scope == "parent":
        valid_children = (
            session.observe({"q": value.sum()}, by=(region, channel), where=region == "east")
            .compute()
            .to_pandas()
        )
        assert valid_children["value", "q"].tolist() == [valid, valid]
        failed_axes = (region,)
        expected_coordinates = [{"key.0": "east"}]
    else:
        valid_parent = (
            session.observe({"q": value.sum()}, by=(region,), where=region == "east")
            .compute()
            .to_pandas()
        )
        assert valid_parent["value", "q"].tolist() == [valid]
        failed_axes = (region, channel)
        expected_coordinates = [{"key.0": "east", "key.1": "A"}]
    coordinates = []
    failure = graph_source_execution._source_check_failure

    def failed(
        stage: SourceRead, name: str, expected: str, received: str, rows: pd.DataFrame
    ) -> NoReturn:
        coordinates.append(rows.to_dict(orient="records"))
        failure(stage, name, expected, received, rows)

    monkeypatch.setattr(graph_source_execution, "_source_check_failure", failed)
    with pytest.raises(MaterializationError) as ordinary:
        session.observe({"q": value.sum()}, by=failed_axes, where=region == "east").compute()
    reads = []
    execute = SourceSession.execute

    def read(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        reads.append(issued)
        return execute(self, issued)

    monkeypatch.setattr(SourceSession, "execute", read)
    with pytest.raises(MaterializationError) as actual:
        evaluate_attribute_inputs(
            _comparison_inputs(
                captured,
                captured,
                by=(channel,),
            )
        )
    assert len(reads) == (1 if failing_scope == "parent" else 2)
    assert "attribute-parent:" in reads[0].purpose
    if failing_scope == "fine":
        assert "attribute-parent:" not in reads[1].purpose
    assert coordinates == [expected_coordinates, expected_coordinates]
    assert actual.value.location == ordinary.value.location == "dataset.numeric_value"
    assert actual.value.expected == ordinary.value.expected
    assert actual.value.repair is not None and ordinary.value.repair is not None
    assert actual.value.repair.action == ordinary.value.repair.action
    assert actual.value.received == ordinary.value.received


def test_joint_compound_identity_counts_preserve_prepared_axis_groups(session: Session) -> None:
    facts = session.catalog.entities.get("sales.facts")
    tags = session.catalog.entities.get("sales.tags")
    first = mv.coordinates(by=(mv.identity(facts),), keys=((("t", 1),), (("t", 2),)))
    second = mv.coordinates(by=(mv.identity(tags),), keys=(((1,),), ((2,),)))
    domain = mv.product(first, second)
    ordinary = session.observe({"q": domain.count()}, by=domain).compute()
    result = _compare(
        session.observe({"q": domain.count()}, by=domain),
        session.observe({"q": domain.count()}, by=domain),
    ).compute()
    expected = ordinary.to_pandas()
    actual = result.to_pandas()
    assert len(ordinary.axes) == len(result.axes) == 2
    assert list(expected.iloc[:, :2].itertuples(index=False, name=None)) == [
        (("t", 1), 1),
        (("t", 1), 2),
        (("t", 2), 1),
        (("t", 2), 2),
    ]
    assert list(actual.iloc[:, :2].itertuples(index=False, name=None)) == list(
        expected.iloc[:, :2].itertuples(index=False, name=None)
    )
    assert actual["value", "q"].tolist() == expected["value", "q"].tolist() == [1] * 4
    assert actual["value", "reference.baseline.q"].tolist() == expected["value", "q"].tolist()
    assert actual["value", "reference.absolute.q"].tolist() == [0] * 4


def test_source_capture_copies_metrics_without_business_reads(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource.adapters import SourceSession

    value = session.catalog.fields.get("sales.facts.value")
    metrics = {"sum": value.sum(), "mean": value.mean()}
    before = terminal_runs(session)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Source capture must not execute business reads")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    captured = session.observe(metrics)
    metrics.clear()
    assert captured.output_names == ("sum", "mean")
    assert terminal_runs(session) == before


def test_role_specific_native_carriers_and_shuffled_rows_survive_direct_inputs(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from decimal import Decimal

    import pyarrow as pa

    from marivo.analysis.compiler.graph_plan import FrameCompute
    from marivo.analysis.core.attribute import AttributeInputProgram
    from marivo.analysis.core.data_types import DecimalDataType
    from marivo.analysis.materialization import graph_local_execution
    from marivo.datasource.adapters import SourceSession

    with duckdb.connect(str(tmp_path / "inputs.duckdb")) as connection:
        connection.execute("ALTER TABLE facts ALTER value TYPE DECIMAL(18,2)")
        connection.execute(
            "UPDATE facts SET value=CASE id WHEN 1 THEN 10.02 WHEN 2 THEN 20.04 ELSE 30.06 END"
        )
    value = session.catalog.fields.get("sales.facts.value")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    selected = session.observe({"q": value.sum() * Decimal("0.5")}, by=(region,)).compute()
    execute = SourceSession.execute
    captured = []
    local = graph_local_execution.run_local

    def read(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        rows = execute(self, issued)
        if "__attribute_kind" not in rows:
            return rows
        scale = 2 if "attribute-parent:" in issued.purpose else 4
        for name in rows:
            if (
                name.endswith("/value")
                or name.endswith(".additive_value")
                or name.startswith("attribute.native.")
            ):
                rows[name] = pd.Series(
                    pd.arrays.ArrowExtensionArray(
                        pa.array(rows[name], type=pa.decimal128(38, scale))
                    )
                )
        return rows.iloc[::-1].reset_index(drop=True)

    monkeypatch.setattr(SourceSession, "execute", read)
    from marivo.analysis.compiler.graph_plan import LocalCompute
    from marivo.analysis.materialization.graph_exchange import StageOutput
    from marivo.analysis.materialization.graph_local_execution import LocalKernel

    def run_local(
        stage: LocalCompute | FrameCompute,
        inputs: tuple[StageOutput, ...],
        kernel: LocalKernel | None = None,
    ) -> StageOutput:
        result = local(stage, inputs, kernel)
        if isinstance(stage, FrameCompute) and isinstance(stage.program, AttributeInputProgram):
            captured.extend(result.attribute_inputs)
            assert {binding.scope for binding in stage.program.reads} == {"parent", "fine"}
            assert all(
                inputs[binding.input_index].attribute_inputs for binding in stage.program.reads
            )
        return result

    monkeypatch.setattr(graph_local_execution, "run_local", run_local)
    rows = selected.attribute(by=(channel,)).compute().to_pandas()
    assert {
        (None if pd.isna(row[0]) else row[0], row[1]): row[2]
        for row in rows.iloc[:, :3].itertuples(index=False, name=None)
    } == {
        (None, "A"): Decimal("15.03"),
        ("east", "A"): Decimal("5.01"),
        ("east", "B"): Decimal("10.02"),
    }
    for name in ("total.q", "observed_total.q"):
        assert rows[("value", name)].tolist() == [Decimal("15.03")] * 3
        assert pa.array(rows[("value", name)]).type == pa.decimal128(38, 4)
    assert len(captured) == 2
    for root in captured:
        scale = 2 if root.layout.scope == "parent" else 4
        columns = {column.slot_id: column for column in root.layout.columns}
        native = root.layout.native_formulas[0]
        assert columns[native.column.slot_id].dtype == DecimalDataType(38, scale)
        assert all(receipt.stage_id == root.stage_id for receipt in root.receipts)


@pytest.mark.release
def test_known_empty_parent_native_formula_has_defined_original_zero(session: Session) -> None:
    value = session.catalog.fields.get("sales.facts.value")
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
    selected = session.observe(
        {"q": value.sum() * 2},
        by=mv.coordinates(by=(region,), keys=(("east",), ("absent",))),
        where=region == "east",
    ).compute()
    output = evaluate_attribute_inputs(_comparison_inputs(selected, selected, by=(channel,)))
    for root in output.attribute_inputs:
        if root.layout.scope == "parent":
            original = root.layout.native_formulas[0].column.slot_id
            assert _keyed_values(root.parent, root.layout.parent_keys, original) == {
                ("east",): 20,
                ("absent",): 0,
            }


@pytest.mark.parametrize("source_rows", (100, 10000))
def test_attribute_transport_depends_on_groups_not_source_rows(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_rows: int
) -> None:
    from tests.support.source_trace import capture_source

    with duckdb.connect(str(tmp_path / "inputs.duckdb")) as connection:
        connection.execute("DELETE FROM facts")
        connection.execute(
            "INSERT INTO facts SELECT 't', i, 'east', CASE WHEN i%2=0 THEN 'A' ELSE 'B' END, 1, 1 FROM range(?) t(i)",
            [source_rows],
        )
    value = session.catalog.fields.get("sales.facts.value")
    channel = session.catalog.fields.get("sales.facts.channel")
    selected = session.observe({"q": value.sum()}).compute()
    trace = capture_source(monkeypatch)
    result = selected.attribute(by=(channel,)).compute().to_pandas()
    assert result[("value", "contribution.q")].tolist() == [source_rows // 2] * 2
    assert trace.returned_rows == [1, 2]
    assert all(
        not any(c.startswith("__original_id.") for c in columns)
        for columns in trace.returned_columns
    )
    assert all(
        "__attribute_root_duplicate" not in sql and "__attribute_fine_" not in sql
        for sql in trace.native_sql
    )


@pytest.mark.parametrize("reverse", (False, True))
def test_distinct_failure_returns_one_key_and_six_ordered_groups(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reverse: bool
) -> None:
    from marivo.analysis.core.attribute import AttributeInputError, AttributePartitionWitness
    from tests.support.source_trace import capture_source

    with duckdb.connect(str(tmp_path / "inputs.duckdb")) as connection:
        connection.execute("DELETE FROM facts")
        connection.execute(
            "INSERT INTO facts SELECT 't', i, 'east', 'g'||lpad(cast(i%10 AS VARCHAR),2,'0'), 1+i//10, 1 FROM range(20) t(i) ORDER BY i "
            + ("DESC" if reverse else "ASC")
        )
    value = session.catalog.fields.get("sales.facts.value")
    channel = session.catalog.fields.get("sales.facts.channel")
    weight = session.catalog.fields.get("sales.facts.weight")
    selected = session.observe({"q": value.count_distinct() + weight.count_distinct()}).compute()
    trace = capture_source(monkeypatch)
    with pytest.raises(AttributeInputError) as caught:
        selected.attribute(by=(channel,)).compute()
    context = caught.value.attribute_context
    assert context is not None and isinstance(context.witness, AttributePartitionWitness)
    assert context.witness.identity[0][1] == 1
    assert [group[0][1] for group in context.witness.groups] == [f"g{i:02}" for i in range(6)]
    assert trace.returned_rows == [1, 22]  # Ten aggregates plus six witnesses per check.


def test_explicit_fine_population_still_requires_complete_coverage(session: Session) -> None:
    from marivo.analysis.errors import AnalysisError

    value = session.catalog.fields.get("sales.facts.value")
    channel = session.catalog.fields.get("sales.facts.channel")
    selected = session.observe({"q": value.sum()}).compute()
    with pytest.raises(AnalysisError, match="coverage"):
        selected.attribute(by=mv.coordinates(by=(channel,), keys=(("A",),))).compute()


@pytest.mark.parametrize("kind", ("ordinary", "membership", "distinct"))
def test_attribute_aggregate_and_diagnostic_sql_compiles_for_native_backends(
    session: Session, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    import ibis

    from marivo.analysis.compiler import attribute_inputs
    from marivo.analysis.compiler.attribute_inputs import AttributeCheckLayout, AttributeRootLayout
    from marivo.analysis.errors import AnalysisError
    from marivo.semantic._numeric_lowering import NumericCheck

    original = attribute_inputs.lower_attribute_inputs
    compiled: set[str] = set()

    def lower(
        layout: AttributeRootLayout,
        table: ir.Table,
        field_columns: dict[str, str],
        preparation: ir.Table,
        preparation_checks: tuple[tuple[str, str, str], ...],
    ) -> tuple[ir.Table, tuple[tuple[str, NumericCheck], ...], tuple[AttributeCheckLayout, ...]]:
        result = original(layout, table, field_columns, preparation, preparation_checks)
        for dialect in ("postgres", "mysql", "trino", "clickhouse"):
            sql = ibis.to_sql(result[0].unbind(), dialect=dialect)
            assert "__attribute_root_duplicate" not in sql
            compiled.add(dialect)
        return result

    monkeypatch.setattr(attribute_inputs, "lower_attribute_inputs", lower)
    value = session.catalog.fields.get("sales.facts.value")
    channel = session.catalog.fields.get("sales.facts.channel")
    by: tuple[Deferred | Grouping, ...] = (channel,)
    if kind == "membership":
        label = session.catalog.fields.get("sales.tags.label")
        by = (
            mv.membership(
                label,
                via=(
                    (
                        session.catalog.relationships.get("sales.fact_links"),
                        session.catalog.relationships.get("sales.link_tags"),
                    ),
                ),
                unmatched="exclude",
            ),
        )
    selected = session.observe(
        {"q": value.count_distinct() if kind == "distinct" else value.count()}
    ).compute()
    if kind == "membership":
        with pytest.raises(AnalysisError, match=r"undefined_input|overlapping_groups"):
            selected.attribute(by=by).compute()
    else:
        selected.attribute(by=by).compute()
    assert compiled == {"postgres", "mysql", "trino", "clickhouse"}
