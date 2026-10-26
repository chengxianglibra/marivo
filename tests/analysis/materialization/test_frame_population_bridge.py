"""Frame selections feed explicit source reads through one checked schedule."""

import json
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir
import pandas as pd
import pyarrow as pa
import pytest

import marivo.analysis as mv
from marivo.analysis.compiler.frame_plan import CompiledFramePlan
from marivo.analysis.compiler.graph_lowering import LoweredRead
from marivo.analysis.compiler.graph_plan import (
    FrameCompute,
    FrameInput,
    FrameKeysInput,
    LocalCompute,
    SourceRead,
)
from marivo.analysis.core.artifact_data import DataAxis, DataColumn, DataSchema
from marivo.analysis.core.data_types import ScalarDataType
from marivo.analysis.core.frame_audit import EvaluationRecord, FrameAudit, FrameResultMetadata
from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    CompletedFrameResult,
    ExpressionFrameBacking,
    MemoryFrameBacking,
    SourceFrameBinding,
)
from marivo.analysis.core.model import CoreRuleError
from marivo.analysis.dsl import frame_sources
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.dsl.frame_population import population_from
from marivo.analysis.dsl.population import Population, in_population, product
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.graph_source_execution import PreparedSources
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.datasource.source_cases import source_params


@pytest.fixture
def session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    database = tmp_path / "bridge.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE facts(tenant VARCHAR,id BIGINT,region VARCHAR,channel VARCHAR,city VARCHAR,amount BIGINT,denominator BIGINT)"
        )
        connection.execute(
            "INSERT INTO facts VALUES ('t1',1,'east','A','Alpha',100,1),('t1',2,'east','A','Alpha',1,1),('t2',1,'west','B','Beta',50,1),('t2',2,'east','B','Gamma',5,1),('t3',1,'west','A','Delta',7,1),('t4',1,NULL,'C','Nullcity',11,1)"
        )
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
city = ms.dimension_column(name='city', entity=facts, column='city')
amount = ms.measure_column(name='amount', entity=facts, column='amount', unit='USD', additivity=ms.additive_all())
denominator = ms.measure_column(name='denominator', entity=facts, column='denominator', unit='1', additivity=ms.additive_all())
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("frame-bridge", timezone="UTC")


def _memory_backing(frame: AnalysisFrame) -> MemoryFrameBacking:
    assert isinstance(frame._backing, MemoryFrameBacking)
    return frame._backing


def _memory(
    session: Session, *, identity: bool = False, empty: bool = False, null: bool = False
) -> AnalysisFrame:
    columns: tuple[DataColumn, ...]
    axes: tuple[DataAxis, ...]
    rows: list[tuple[str | int | None, ...]]
    if identity:
        columns = (
            DataColumn("k0", "tenant", ScalarDataType("string"), False),
            DataColumn("k1", "id", ScalarDataType("int64"), False),
        )
        axes = (DataAxis("identity", columns, name="identity", kind="member"),)
        rows = [("t1", 1), ("t2", 1)]
    else:
        columns = (
            DataColumn("k0", "region", ScalarDataType("string"), True),
            DataColumn("k1", "channel", ScalarDataType("string"), False),
            DataColumn("k2", "detail", ScalarDataType("string"), False),
        )
        axes = tuple(
            DataAxis(f"axis.{i}", (column,), name=column.name) for i, column in enumerate(columns)
        )
        rows = [("east", "A", "one"), ("east", "A", "two"), ("west", "B", "three")]
        if null:
            rows = [(None, "C", "one")]
    schema = DataSchema(axes, ())
    data = CompletedFrameData(
        schema, pd.DataFrame([] if empty else rows, columns=[c.slot_id for c in columns])
    )
    metadata = FrameResultMetadata(
        "selected-result",
        FrameAudit((), EvaluationRecord(datetime(2026, 10, 19, tzinfo=timezone.utc), (), ())),
    )
    return AnalysisFrame._from_memory(session._runtime.local, CompletedFrameResult(data, metadata))


def _population(
    session: Session, frame: AnalysisFrame, *, scalar: bool = False, identity: bool = False
) -> Population:
    on = (
        ()
        if scalar
        else ((frame.axes[0], session.catalog.entities.get("sales.facts")),)
        if identity
        else (
            (frame.axes[0], session.catalog.fields.get("sales.facts.region")),
            (frame.axes[1], session.catalog.fields.get("sales.facts.channel")),
        )
    )
    return population_from(session._runtime.local, session.catalog._state, frame, on=on)


def _observe(
    session: Session, population: Population, *, metric_where: bool = False, negate: bool = False
) -> AnalysisFrame:
    amount = session.catalog.fields.get("sales.facts.amount").sum()
    condition = ~in_population(population) if negate else in_population(population)
    return frame_sources.observe(
        session._runtime.local,
        session.catalog._state,
        session._runtime.connection_config,
        {"amount": amount.where(condition) if metric_where else amount},
        by=(
            session.catalog.fields.get("sales.facts.region"),
            session.catalog.fields.get("sales.facts.channel"),
        ),
        where=None if metric_where else condition,
    )


@pytest.mark.parametrize("metric_where", (False, True))
def test_joint_keys_deduplicate_together_and_capture_before_metric_consumption(
    session: Session,
    metric_where: bool,
) -> None:
    selected = _memory(session)
    population = _population(session, selected)
    consumer = _observe(session, population, metric_where=metric_where)
    assert isinstance(consumer._backing, ExpressionFrameBacking)
    binding = consumer._backing.root
    assert isinstance(binding, SourceFrameBinding) and binding.frame_key_inputs[0].frame is selected
    result = consumer.compute().to_pandas()
    assert list(result.itertuples(index=False, name=None)) == [
        ("east", "A", 101),
        ("west", "B", 50),
    ]
    keys = (
        frame_sources.frame(
            session._runtime.local,
            session.catalog._state,
            session._runtime.connection_config,
            population,
        )
        .compute()
        .to_pandas()
    )
    assert list(keys.itertuples(index=False, name=None)) == [("east", "A"), ("west", "B")]


@pytest.mark.parametrize("empty", (False, True))
def test_empty_joint_mapping_keeps_singleton_or_empty_for_frame_and_source_filter(
    session: Session, empty: bool
) -> None:
    population = _population(session, _memory(session, empty=empty), scalar=True)
    keys = (
        frame_sources.frame(
            session._runtime.local,
            session.catalog._state,
            session._runtime.connection_config,
            population,
        )
        .compute()
        .to_pandas()
    )
    assert keys.shape == (0 if empty else 1, 0)
    result = _observe(session, population).compute().to_pandas()
    assert len(result) == (0 if empty else 5)
    assert result.iloc[:, -1].sum() == (0 if empty else 174)
    inverse = _observe(session, population, negate=True).compute().to_pandas()
    assert len(inverse) == (5 if empty else 0)


def test_complete_identity_read_and_members_use_the_retained_keys(session: Session) -> None:
    population = _population(session, _memory(session, identity=True), identity=True)
    result = (
        frame_sources.read(
            session._runtime.local,
            session.catalog._state,
            session._runtime.connection_config,
            population,
            {"city": session.catalog.fields.get("sales.facts.city")},
        )
        .compute()
        .to_pandas()
    )
    assert list(result.itertuples(index=False, name=None)) == [
        (("t1", 1), "Alpha"),
        (("t2", 1), "Beta"),
    ]
    counted = (
        frame_sources.observe(
            session._runtime.local,
            session.catalog._state,
            session._runtime.connection_config,
            {"members": population.count()},
        )
        .compute()
        .to_pandas()
    )
    assert counted.iloc[0, 0] == 2


def test_fixed_top_keeps_old_region_while_expression_top_reselects(session: Session) -> None:
    region = session.catalog.fields.get("sales.facts.region")
    source = frame_sources.observe(
        session._runtime.local,
        session.catalog._state,
        session._runtime.connection_config,
        {"amount": session.catalog.fields.get("sales.facts.amount").sum()},
        by=(region,),
    )
    selected = source.top(1, by=source["amount"])
    fixed = selected.compute()
    with duckdb.connect(str(session.project_root / "bridge.duckdb")) as connection:
        connection.execute("UPDATE facts SET amount=999 WHERE tenant='t2' AND id=1")
        connection.execute("UPDATE facts SET amount=20 WHERE tenant='t1' AND id=1")
    outputs = []
    for frame in (fixed, selected):
        domain = population_from(
            session._runtime.local, session.catalog._state, frame, on=((frame.axes[0], region),)
        )
        result = _observe(session, domain).compute().to_pandas()
        outputs.append(list(result.itertuples(index=False, name=None)))
    assert outputs == [
        [("east", "A", 21), ("east", "B", 5)],
        [("west", "A", 7), ("west", "B", 999)],
    ]


def test_one_final_schedule_resolves_every_compiler_leaf(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.analysis.materialization.frame_evaluation as evaluator
    from marivo.analysis.materialization.graph_source_execution import run_frame_plan as original

    plans: list[CompiledFramePlan] = []

    def run(
        compiled: CompiledFramePlan,
        *,
        source_prepared: Mapping[str, PreparedSources],
        read_fixed: Callable[[FrameInput], CompletedFrameData],
        adapt_source: Callable[[str, StageOutput], StageOutput],
        observe_read: Callable[[SourceRead, LoweredRead, StageOutput], None] | None = None,
        observe_local: Callable[[LocalCompute | FrameCompute, StageOutput], None] | None = None,
    ) -> StageOutput:
        plans.append(compiled)
        assert not any(isinstance(stage, FrameKeysInput) for stage in compiled.lowered.plan.stages)
        completed: set[str] = set()
        for stage in compiled.lowered.plan.stages:
            assert set(stage.inputs) <= completed
            completed.add(stage.stage_id)
        return original(
            compiled,
            source_prepared=source_prepared,
            read_fixed=read_fixed,
            adapt_source=adapt_source,
            observe_read=observe_read,
            observe_local=observe_local,
        )

    monkeypatch.setattr(evaluator, "run_frame_plan", run)
    selected = _memory(session)
    population = _population(session, selected)
    _observe(session, population.union(population)).compute()
    assert len(plans) == 1
    assert sum(stage.stage_id.startswith("bridge:") for stage in plans[0].lowered.plan.stages) == 1
    first = _observe(session, population)
    top = first.top(1, by=first["amount"])
    nested = _observe(session, _population(session, top)).compute().to_pandas()
    assert list(nested.itertuples(index=False, name=None)) == [("east", "A", 101)]
    assert len(plans) == 2
    assert sum(stage.stage_id.startswith("bridge:") for stage in plans[1].lowered.plan.stages) == 2


def test_null_field_empty_domain_and_explicit_output_domain_keep_distinct_meanings(
    session: Session,
) -> None:
    null_population = _population(session, _memory(session, null=True))
    null_result = _observe(session, null_population).compute().to_pandas()
    assert pd.isna(null_result.iloc[0, 0]) and null_result.iloc[0, 1:].tolist() == ["C", 11]
    assert (
        _observe(session, _population(session, _memory(session, empty=True)))
        .compute()
        .to_pandas()
        .empty
    )
    selected = _population(session, _memory(session))
    pending = frame_sources.observe(
        session._runtime.local,
        session.catalog._state,
        session._runtime.connection_config,
        {"amount": session.catalog.fields.get("sales.facts.amount").sum()},
        by=selected,
    )
    with pytest.raises(MaterializationError, match="missing output coordinates"):
        pending.compute()
    exact = (
        frame_sources.observe(
            session._runtime.local,
            session.catalog._state,
            session._runtime.connection_config,
            {"amount": session.catalog.fields.get("sales.facts.amount").sum()},
            by=selected,
            where=in_population(selected),
        )
        .compute()
        .to_pandas()
    )
    assert exact.iloc[:, :2].values.tolist() == [["east", "A"], ["west", "B"]]
    assert exact[("value", "amount")].tolist() == [101, 50]


def test_producer_checks_survive_top_zero_before_dependent_source_read(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.analysis.materialization.graph_source_execution as executor

    original = executor.read_stage
    calls: list[str] = []

    def read(
        prepared: PreparedSources,
        stage: SourceRead,
        lowered: LoweredRead,
        *,
        value_keys: tuple[str, ...] = (),
    ) -> StageOutput:
        calls.append("read")
        return original(prepared, stage, lowered, value_keys=value_keys)

    monkeypatch.setattr(executor, "read_stage", read)
    region = session.catalog.fields.get("sales.facts.region")
    source = frame_sources.observe(
        session._runtime.local,
        session.catalog._state,
        session._runtime.connection_config,
        {"amount": session.catalog.fields.get("sales.facts.amount").sum()},
        by=(region,),
    )
    invalid = source.select({"invalid": source["amount"] * 1e308})
    empty = invalid.top(0, by=invalid["invalid"])
    selected = population_from(
        session._runtime.local, session.catalog._state, empty, on=((empty.axes[0], region),)
    )
    with pytest.raises(MaterializationError, match="finite"):
        _observe(session, selected).compute()
    assert calls == ["read"]


def test_typed_transport_keeps_complete_large_identity_tuples(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    large = 2**53
    with duckdb.connect(str(session.project_root / "bridge.duckdb")) as connection:
        connection.execute(f"UPDATE facts SET id=id+{large}")
    original = SourceSession._bind_key_relation
    keys: list[pa.Table] = []

    def bind(self: SourceSession, data: pa.Table) -> ir.Table:
        keys.append(data)
        return original(self, data)

    monkeypatch.setattr(SourceSession, "_bind_key_relation", bind)
    base = _memory(session, identity=True)
    assert isinstance(base._schema, DataSchema)
    selected = AnalysisFrame._from_memory(
        session._runtime.local,
        CompletedFrameResult(
            CompletedFrameData(
                base._schema, pd.DataFrame({"k0": ["t1", "t2"], "k1": [large + 1, large + 1]})
            ),
            _memory_backing(base).metadata,
        ),
    )
    result = _observe(session, _population(session, selected, identity=True)).compute().to_pandas()
    assert result.iloc[:, -1].tolist() == [100, 50]
    assert len(keys) == 1 and keys[0].column(1).to_pylist() == [large + 1, large + 1]
    assert keys[0].column(0).to_pylist() == ["t1", "t2"]


def test_exact_evaluated_key_audit_is_not_truncated(session: Session) -> None:
    long = "key-" + "x" * 3000
    with duckdb.connect(str(session.project_root / "bridge.duckdb")) as connection:
        connection.execute("UPDATE facts SET region=? WHERE region='east'", [long])
    base = _memory(session)
    assert isinstance(base._schema, DataSchema)
    selected = AnalysisFrame._from_memory(
        session._runtime.local,
        CompletedFrameResult(
            CompletedFrameData(
                base._schema, pd.DataFrame({"k0": [long], "k1": ["A"], "k2": ["one"]})
            ),
            _memory_backing(base).metadata,
        ),
    )
    result = _observe(session, _population(session, selected)).compute()
    scopes = [
        dict(fact.values)["source_scopes"]
        for fact in _memory_backing(result).metadata.audit.source_facts
        if fact.kind == "read"
    ]
    decoded = []
    for value in scopes:
        assert isinstance(value, str)
        decoded.append(json.loads(value))
    dynamic = [
        json.loads(token[1])
        for scope in decoded
        for token in scope
        if "frame_key_selection" in token[1]
    ]
    assert dynamic
    assert any(
        long in json.dumps(value)
        and selected._input_id in json.dumps(value)
        and "selected-result" in json.dumps(value)
        for value in dynamic
    )


def test_full_fixed_validation_precedes_projection_and_business_read(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.analysis.materialization.graph_source_execution as executor

    selected = _memory(session)
    assert isinstance(selected._backing, MemoryFrameBacking)
    # Simulate damaged backing in an unselected component. Consumption must
    # validate the complete fixed input before it can publish projected keys.
    selected._backing.data._rows["k2"] = pd.Series([float("inf"), 1.0, 2.0])
    calls: list[str] = []

    def forbidden(*args: object, **kwargs: object) -> StageOutput:
        calls.append("business read")
        raise AssertionError("dependent source read preceded complete input validation")

    monkeypatch.setattr(executor, "read_stage", forbidden)
    with pytest.raises(MaterializationError, match="carrier"):
        _observe(session, _population(session, selected)).compute()
    assert calls == []


def test_physical_target_type_conflict_fails_before_business_read(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.analysis.materialization.graph_source_execution as executor

    selected = _memory(session)
    population = population_from(
        session._runtime.local,
        session.catalog._state,
        selected,
        on=((selected.axes[0], session.catalog.fields.get("sales.facts.denominator")),),
    )
    calls: list[str] = []

    def forbidden(*args: object, **kwargs: object) -> StageOutput:
        calls.append("business read")
        raise AssertionError("type validation must precede reads")

    monkeypatch.setattr(executor, "read_stage", forbidden)
    with pytest.raises(CoreRuleError, match="physical"):
        _observe(session, population).compute()
    assert calls == []


@pytest.mark.parametrize("length", [8, 128])
def test_bounded_varchar_target_uses_admitted_frame_string_type(
    session: Session, length: int
) -> None:
    from marivo.analysis.materialization.graph_source_execution import admit_source_type
    from marivo.analysis.materialization.population_preparation import prepare_population
    from marivo.analysis.materialization.query_parameters import prepare_frame_parameters

    selected = _memory(session)
    assert isinstance(selected._schema, DataSchema)
    domain = _population(session, selected)
    pending = _observe(session, domain)
    assert isinstance(pending._backing, ExpressionFrameBacking)
    assert isinstance(pending._backing.root, SourceFrameBinding)
    source = ibis.table(
        {"region": dt.String(length=length), "channel": dt.String(length=length)},
        name="bounded_varchar_source",
    )
    entities = {"sales.facts": source}
    parameters = prepare_frame_parameters(
        pending._backing.root.graph, {selected._input_id: selected._schema}, entities
    )
    prepared = prepare_population(domain._definition, entities, session.catalog._state)
    assert prepared.key_groups == (("__population_0_0",), ("__population_1_0",))
    assert tuple(column.dtype for column in parameters[0].columns) == (
        ScalarDataType("string"),
        ScalarDataType("string"),
    )
    assert admit_source_type(source.schema().to_pyarrow().field("region"), "region") == (
        ScalarDataType("string")
    )


def test_fixed_key_projection_avoids_source_preparation(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.analysis.materialization.frame_source_preparation as evaluator

    population = _population(session, _memory(session))
    pending = frame_sources.frame(
        session._runtime.local,
        session.catalog._state,
        session._runtime.connection_config,
        population,
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("fixed key projection has no business source dependency")

    monkeypatch.setattr(evaluator, "open_query_sources", forbidden)
    result = pending.compute().to_pandas()
    assert list(result.itertuples(index=False, name=None)) == [("east", "A"), ("west", "B")]


def test_selected_missing_source_key_remains_a_domain_without_facts(session: Session) -> None:
    base = _memory(session, null=True)
    assert isinstance(base._schema, DataSchema)
    selected = AnalysisFrame._from_memory(
        session._runtime.local,
        CompletedFrameResult(
            CompletedFrameData(
                base._schema, pd.DataFrame({"k0": ["absent"], "k1": ["C"], "k2": ["one"]})
            ),
            _memory_backing(base).metadata,
        ),
    )
    domain = _population(session, selected)
    keys = (
        frame_sources.frame(
            session._runtime.local,
            session.catalog._state,
            session._runtime.connection_config,
            domain,
        )
        .compute()
        .to_pandas()
    )
    assert list(keys.itertuples(index=False, name=None)) == [("absent", "C")]
    assert _observe(session, domain).compute().to_pandas().empty


def test_frame_key_set_logic_and_explicit_product_use_actual_complete_domains(
    session: Session,
) -> None:
    selected = _population(session, _memory(session))
    null = _population(session, _memory(session, null=True))
    joined = selected.union(null).where(~in_population(null))
    result = _observe(session, joined).compute().to_pandas()
    assert list(result.itertuples(index=False, name=None)) == [
        ("east", "A", 101),
        ("west", "B", 50),
    ]
    region = selected.project(by=(selected.axes[0],))
    channels = null.project(by=(null.axes[1],))
    crossed = product(region, channels)
    # An explicit Population product is a Cartesian domain. It is distinct
    # from the joint projection that produced selected above.
    keys = (
        frame_sources.frame(
            session._runtime.local,
            session.catalog._state,
            session._runtime.connection_config,
            crossed,
        )
        .compute()
        .to_pandas()
    )
    assert list(keys.itertuples(index=False, name=None)) == [("east", "C"), ("west", "C")]
    full_zero = selected.project(by=())
    empty_zero = selected.difference(selected).project(by=())
    gated = full_zero.where(~in_population(empty_zero))
    assert len(_observe(session, gated).compute().to_pandas()) == 5


def test_source_relationship_preimage_retains_frame_target_keys(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.analysis.materialization.test_population_membership import _session

    session = _session(semantic_project_factory, tmp_path, monkeypatch)
    column = DataColumn("key", "tag", ScalarDataType("int64"), False)
    schema = DataSchema((DataAxis("tags", (column,), name="tag", kind="member"),), ())
    metadata = FrameResultMetadata(
        "tag-selection",
        FrameAudit((), EvaluationRecord(datetime(2026, 10, 19, tzinfo=timezone.utc), (), ())),
    )
    fixed = AnalysisFrame._from_memory(
        session._runtime.local,
        CompletedFrameResult(CompletedFrameData(schema, pd.DataFrame({"key": [1]})), metadata),
    )
    targets = population_from(
        session._runtime.local,
        session.catalog._state,
        fixed,
        on=((fixed.axes[0], session.catalog.entities.get("sales.tags")),),
    )
    orders = session.members(session.catalog.entities.get("sales.orders"))
    path = (
        session.catalog.relationships.get("sales.order_links"),
        session.catalog.relationships.get("sales.link_tags"),
    )
    selected = orders.preimage(targets, via=path)
    result = (
        frame_sources.observe(
            session._runtime.local,
            session.catalog._state,
            session._runtime.connection_config,
            {"amount": session.catalog.fields.get("sales.orders.amount").sum()},
            where=mv.in_population(selected),
        )
        .compute()
        .to_pandas()
    )
    assert result.iloc[0, 0] == 40


@pytest.mark.parametrize(
    "backend,profile", source_params([("postgres", "table"), ("mysql", "innodb-table")])
)
def test_remote_frame_keys_feed_checked_tuple_null_empty_and_zero_axis_routes(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
) -> None:
    from marivo.datasource.ir import TableSourceIR
    from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
    from tests.datasource.source_cases import SourceData, source_case
    from tests.support.source_trace import capture_source

    large = 2**53
    data = SourceData(
        "tenant VARCHAR(8) NOT NULL,id BIGINT NOT NULL,region VARCHAR(8),channel VARCHAR(8),amount BIGINT NOT NULL",
        f"('a',{large + 1},'east','A',10),('a',{large + 2},'east','B',20),('b',{large + 1},'west','B',30),('b',{large + 2},'west','A',40),('n',{large + 3},NULL,'C',50)",
        "",
        [],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        semantic_project_factory(
            {
                "datasources/warehouse.py": _datasource_model(backend, case, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Analytics',default=True)\n",
                "sales/model.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"facts=ms.entity(name='facts',datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key=['tenant','id'])\n"
                + "region=ms.dimension_column(name='region',entity=facts,column='region')\n"
                + "channel=ms.dimension_column(name='channel',entity=facts,column='channel')\n"
                + "amount=ms.measure_column(name='amount',entity=facts,column='amount',unit='USD',additivity=ms.additive_all())\n",
            },
            load=False,
        )
        remote = mv.session.get_or_create("remote-frame-bridge", timezone="UTC")
        trace = capture_source(monkeypatch)
        selected = _memory(remote)
        joint_frame = _observe(remote, _population(remote, selected)).compute()
        joint = joint_frame.to_pandas()
        assert list(joint.itertuples(index=False, name=None)) == [
            ("east", "A", 10),
            ("west", "B", 30),
        ]
        roundtrip = _observe(remote, _population(remote, joint_frame)).compute().to_pandas()
        assert list(roundtrip.itertuples(index=False, name=None)) == list(
            joint.itertuples(index=False, name=None)
        )
        null = (
            _observe(remote, _population(remote, _memory(remote, null=True))).compute().to_pandas()
        )
        assert pd.isna(null.iloc[0, 0]) and null.iloc[0, 1:].tolist() == ["C", 50]
        assert (
            _observe(remote, _population(remote, _memory(remote, empty=True)))
            .compute()
            .to_pandas()
            .empty
        )
        for empty in (False, True):
            zero = _population(remote, _memory(remote, empty=empty), scalar=True)
            result = _observe(remote, zero).compute().to_pandas()
            assert result.iloc[:, -1].sum() == (0 if empty else 150)
        identity = _memory(remote, identity=True)
        assert isinstance(identity._schema, DataSchema)
        keys = AnalysisFrame._from_memory(
            remote._runtime.local,
            CompletedFrameResult(
                CompletedFrameData(
                    identity._schema, pd.DataFrame({"k0": ["a", "b"], "k1": [large + 1, large + 2]})
                ),
                _memory_backing(identity).metadata,
            ),
        )
        domain = _population(remote, keys, identity=True)
        read = (
            frame_sources.read(
                remote._runtime.local,
                remote.catalog._state,
                remote._runtime.connection_config,
                domain,
                {"amount": remote.catalog.fields.get("sales.facts.amount")},
            )
            .compute()
            .to_pandas()
        )
        assert list(read.itertuples(index=False, name=None)) == [
            (("a", large + 1), 10),
            (("b", large + 2), 40),
        ]
        assert trace.native_sql and len(trace.returned_rows) == 7
        trace.save(
            "frame_population_bridge_" + backend,
            case.environment,
            {"large_identity": large + 1, "joint_keys": [["east", "A"], ["west", "B"]]},
            {
                "joint_values": [10, 30],
                "identity_values": [10, 40],
                "null_value": 50,
                "zero_nonempty_sum": 150,
            },
            None,
            (case.session,),
        )


def test_frame_only_nested_metric_where_survives_private_capture_guard(session: Session) -> None:
    domain = _population(session, _memory(session))
    amount = session.catalog.fields.get("sales.facts.amount").sum()
    metric = amount.where(~~in_population(domain)) / 2
    result = (
        frame_sources.observe(
            session._runtime.local,
            session.catalog._state,
            session._runtime.connection_config,
            {"amount": metric},
            by=(
                session.catalog.fields.get("sales.facts.region"),
                session.catalog.fields.get("sales.facts.channel"),
            ),
        )
        .compute()
        .to_pandas()
    )
    assert list(result.itertuples(index=False, name=None)) == [
        ("east", "A", 50.5),
        ("west", "B", 25.0),
    ]
