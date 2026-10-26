"""Explicit source leaves, fixed Frame operations and typed Population constraints."""

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo._definition.graph import computation_fingerprint
from marivo._definition.query import PopulationValues, TopValues
from marivo.analysis.binding.correspondence import ComparisonEndpoint
from marivo.analysis.compiler.graph_lowering import LoweredRead, lower_query
from marivo.analysis.compiler.graph_plan import (
    FrameCompute,
    LocalCompute,
    NativeRead,
    SourceRead,
    plan_query,
)
from marivo.analysis.compiler.observation import FormulaProgram
from marivo.analysis.compiler.query_program import FrameProgram, ResultProgram
from marivo.analysis.core.frame_data import ExpressionFrameBacking, SourceFrameBinding
from marivo.analysis.materialization import graph_local_execution, graph_source_execution
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.graph_local_execution import LocalKernel
from marivo.analysis.materialization.graph_source_execution import PreparedSources
from marivo.analysis.materialization.query_key_fusion import fuse_query_terminal
from marivo.analysis.session.core import Session
from marivo.semantic._numeric_lowering import NumericCheck
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import DslCaseFactory, terminal_runs
from tests.support.source_trace import capture_source


def _capture_stages(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    list[tuple[SourceRead, LoweredRead]],
    list[StageOutput],
    list[tuple[LocalCompute | FrameCompute, tuple[StageOutput, ...], StageOutput]],
]:
    reads: list[tuple[SourceRead, LoweredRead]] = []
    outputs: list[StageOutput] = []
    locals_: list[tuple[LocalCompute | FrameCompute, tuple[StageOutput, ...], StageOutput]] = []
    read_stage = graph_source_execution.read_stage
    run_local = graph_local_execution.run_local

    def read(
        prepared: PreparedSources,
        stage: SourceRead,
        request: LoweredRead,
        *,
        value_keys: tuple[str, ...] = (),
    ) -> StageOutput:
        assert stage.stage_id == request.stage_id
        reads.append((stage, request))
        output = read_stage(prepared, stage, request, value_keys=value_keys)
        outputs.append(output)
        return output

    def local(
        stage: LocalCompute | FrameCompute,
        inputs: tuple[StageOutput, ...],
        kernel: LocalKernel | None = None,
    ) -> StageOutput:
        output = run_local(stage, inputs, kernel)
        locals_.append((stage, inputs, output))
        return output

    monkeypatch.setattr(graph_source_execution, "read_stage", read)
    monkeypatch.setattr(graph_local_execution, "run_local", local)
    return reads, outputs, locals_


@pytest.fixture
def source_query_session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    database = tmp_path / "query.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE orders(id BIGINT, region VARCHAR, amount BIGINT, baseline BIGINT)"
        )
        connection.execute(
            "INSERT INTO orders VALUES (1,'A',10,0),(2,'A',30,0),(3,'B',20,0),(4,NULL,NULL,0)"
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Analytics', default=True)\n",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'), primary_key=['id'])
region = ms.dimension_column(name='region', entity=orders, column='region')
amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all(), unit='CNY')
baseline = ms.measure_column(name='baseline', entity=orders, column='baseline', additivity=ms.additive_all(), unit='CNY')
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("source-query", timezone="UTC")


def _attributes(session: Session) -> mv.AnalysisFrame:
    return session.read(
        session.members(session.catalog.entities.get("sales.orders")),
        {
            "amount": session.catalog.fields.get("sales.orders.amount"),
            "region": session.catalog.fields.get("sales.orders.region"),
        },
    )


def _identities(session: Session, frame: mv.AnalysisFrame) -> mv.Population:
    return session.population_from(
        frame, on=((frame.axes[0], session.catalog.entities.get("sales.orders")),)
    )


def _regions(session: Session, frame: mv.AnalysisFrame) -> mv.Population:
    return session.population_from(
        frame, on=((frame.axes[0], session.catalog.fields.get("sales.orders.region")),)
    )


@pytest.fixture
def collated_source_query_session(source_query_session: Session, tmp_path: Path) -> Session:
    with duckdb.connect(str(tmp_path / "query.duckdb")) as connection:
        connection.execute("DROP TABLE orders")
        connection.execute(
            "CREATE TABLE orders(id VARCHAR COLLATE NOCASE, region VARCHAR COLLATE NOCASE, amount BIGINT)"
        )
        connection.executemany(
            "INSERT INTO orders VALUES (?, ?, ?)",
            (("a", "a", 1), ("B", "A", 1), ("é", "é", 1), ("Z", "Z", 1), ("c", None, 1)),
        )
    return source_query_session


@pytest.mark.parametrize("operation", ("eq", "ne", "lt", "top_string", "top_tie"))
def test_collated_strings_keep_host_comparison_and_order(
    collated_source_query_session: Session, operation: str
) -> None:
    source = _attributes(collated_source_query_session)
    result = (
        (
            source.where(source["region"] == "a")
            if operation == "eq"
            else source.where(source["region"] != "a")
            if operation == "ne"
            else source.where(source["region"] < "a")
            if operation == "lt"
            else source.top(2, by=source["region"])
            if operation == "top_string"
            else source.top(2, by=source["amount"])
        )
        .compute()
        .to_pandas()
    )
    expected = {
        "eq": ["a"],
        "ne": ["B", "Z", "é"],
        "lt": ["B", "Z"],
        "top_string": ["é", "a"],
        "top_tie": ["B", "Z"],
    }
    assert result.iloc[:, 0].tolist() == expected[operation]


@pytest.mark.parametrize("default_collation", ("", "nocase"))
def test_collated_string_membership_uses_exact_utf8_bytes(
    tmp_path: Path, default_collation: str
) -> None:
    import ibis

    from marivo.analysis.compiler.source_predicates import parameter_string_comparisons
    from marivo.analysis.materialization.query_key_fusion import _distinct
    from marivo.analysis.materialization.query_parameters import _replace_parameter

    database = tmp_path / "membership.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE labels(label VARCHAR COLLATE NOCASE)")
        connection.executemany("INSERT INTO labels VALUES (?)", (("a",), ("A",), ("é",)))
    backend = ibis.duckdb.connect(str(database))
    try:
        backend.raw_sql(f"SET default_collation = '{default_collation}'")
        labels = backend.table("labels")
        distinct = backend.execute(_distinct(labels, "duckdb"))
        assert sorted(distinct["label"].tolist()) == ["A", "a", "é"]
        parameter = ibis.table({"key": "string"}, name="selected")
        query = labels.semi_join(parameter, labels.label.identical_to(parameter.key))
        query = parameter_string_comparisons(query, "selected", "duckdb")
        selected = ibis.memtable({"key": ["a", "é"]})
        result = backend.execute(_replace_parameter(query, "selected", selected))
        assert sorted(result["label"].tolist()) == ["a", "é"]
    finally:
        backend.disconnect()


def test_native_collated_group_membership_keeps_every_original_contribution(
    collated_source_query_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    session = collated_source_query_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    grouped = session.observe({"count": amount.count()}, by=(region,))
    fixed = grouped.top(1, by=grouped["count"]).compute()
    assert fixed.to_pandas()["value", "count"].tolist() == [2]
    selected = _regions(session, fixed)
    key = fixed.to_pandas().iloc[0, 0]
    with duckdb.connect(str(tmp_path / "query.duckdb")) as db:
        expected = db.execute(
            "SELECT count(amount) FROM orders WHERE region IS NOT DISTINCT FROM ?", [key]
        ).fetchone()
    assert expected is not None
    trace = capture_source(monkeypatch)
    result = session.observe({"count": amount.count()}, where=mv.in_population(selected)).compute()
    assert result.to_pandas()["value", "count"].tolist() == [expected[0]]
    assert trace.uploaded_key_rows == [1]


@pytest.mark.parametrize("operation", ("read", "where", "select", "top", "sum", "rows"))
def test_terminal_relation_returns_only_selected_rows(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    source = _attributes(source_query_session)
    query = (
        source.where(source["amount"] >= 20)
        if operation == "where"
        else source.select({"kept": source["region"]})
        if operation == "select"
        else source.top(2, by=source["amount"])
        if operation == "top"
        else source.reduce({"total": source["amount"].sum()})
        if operation == "sum"
        else source.reduce({"rows": mv.row_count()})
        if operation == "rows"
        else source
    )
    trace = capture_source(monkeypatch)
    reads, outputs, locals_ = _capture_stages(monkeypatch)
    rows = query.compute().to_pandas()
    assert (
        len(rows) == {"read": 4, "where": 2, "select": 4, "top": 2, "sum": 1, "rows": 1}[operation]
    )
    if operation in ("where", "top"):
        assert rows.iloc[:, 0].tolist() == [2, 3]
        assert rows["value", "amount"].tolist() == [30, 20]
    elif operation == "sum":
        assert rows["value", "total"].tolist() == [60]
    elif operation == "rows":
        assert rows["value", "rows"].tolist() == [4]
    else:
        assert rows.iloc[:, 0].tolist() == [1, 2, 3, 4]
        name = "kept" if operation == "select" else "region"
        assert rows["value", name].iloc[:3].tolist() == ["A", "A", "B"]
        assert pd.isna(rows["value", name].iloc[3])
        if operation == "read":
            assert rows["value", "amount"].iloc[:3].tolist() == [10, 30, 20]
            assert pd.isna(rows["value", "amount"].iloc[3])
    assert trace.returned_rows == [2 if operation == "top" else 4]
    assert len(trace.native_sql) == len(reads) == len(outputs) == 1
    assert isinstance(reads[0][0].program, NativeRead)
    if operation == "top":
        assert reads[0][0].program.finish is not None
        assert reads[0][0].program.finish.frame_top is not None
    else:
        assert reads[0][0].program.finish is None
    assert tuple(outputs[0].table.columns) == ("key.0", "value.0", "value.1")
    assert tuple(str(dtype) for dtype in outputs[0].table.dtypes) == (
        "int64[pyarrow]",
        "int64[pyarrow]",
        "string[pyarrow]",
    )
    if operation == "top":
        tops = [
            (stage, inputs, output)
            for stage, inputs, output in locals_
            if isinstance(stage.program, (FrameProgram, ResultProgram))
            and isinstance(stage.program.operation, TopValues)
        ]
        assert len(tops) == 1
        stage, inputs, output = tops[0]
        assert stage.inputs == (reads[0][0].stage_id,)
        assert inputs[0].table.iloc[:, 0].tolist() == [2, 3]
        assert output.table.iloc[:, 0].tolist() == [2, 3]
    assert trace.uploaded_key_rows == []
    assert terminal_runs(source_query_session) == ()


def test_top_and_where_keep_call_order(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _attributes(source_query_session)
    top = source.top(2, by=source["amount"])
    filtered = source.where(source["amount"] < 30)
    trace = capture_source(monkeypatch)
    reads, outputs, locals_ = _capture_stages(monkeypatch)
    assert top.where(top["amount"] < 30).compute().to_pandas().iloc[:, 0].tolist() == [3]
    assert filtered.top(2, by=filtered["amount"]).compute().to_pandas().iloc[:, 0].tolist() == [
        3,
        1,
    ]
    assert trace.returned_rows == [2, 4]
    assert len(trace.native_sql) == len(reads) == len(outputs) == 2
    assert trace.uploaded_key_rows == []
    assert all(isinstance(stage.program, NativeRead) for stage, _ in reads)
    first = reads[0][0].program
    assert isinstance(first, NativeRead) and first.finish is not None
    assert first.finish.frame_top is not None
    second = reads[1][0].program
    assert isinstance(second, NativeRead) and second.finish is None
    tops = [
        (inputs[0].table.iloc[:, 0].tolist(), output.table.iloc[:, 0].tolist())
        for stage, inputs, output in locals_
        if isinstance(stage.program, (FrameProgram, ResultProgram))
        and isinstance(stage.program.operation, TopValues)
    ]
    assert tops == [([2, 3], [2, 3]), ([1, 3], [3, 1])]


@pytest.mark.parametrize("operation", ("union", "intersection", "difference"))
def test_source_population_set_terminal_preserves_native_layout(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    session = source_query_session
    entity = session.catalog.entities.get("sales.orders")
    amount = session.catalog.fields.get("sales.orders.amount")
    left = session.members(entity, where=amount >= 20)
    right = session.members(entity, where=amount <= 20)
    selected = (
        left.union(right)
        if operation == "union"
        else left.intersection(right)
        if operation == "intersection"
        else left.difference(right)
    )
    trace = capture_source(monkeypatch)
    reads, outputs, locals_ = _capture_stages(monkeypatch)
    result = session.frame(selected).compute()
    rows = result.to_pandas()
    assert (
        rows.iloc[:, 0].tolist()
        == {"union": [1, 2, 3], "intersection": [3], "difference": [2]}[operation]
    )
    assert len(reads) == len(outputs) == len(trace.native_sql) == 1
    stage, request = reads[0]
    assert isinstance(stage.program, NativeRead)
    assert stage.program.finish is None
    assert stage.keys == ("key.0",)
    assert tuple(column.slot_id for column in stage.columns) == ("key.0",)
    assert tuple(outputs[0].table.columns) == ("key.0",)
    assert outputs[0].layout is None
    assert outputs[0].key_groups == ()
    assert rows.columns.equals(
        pd.MultiIndex.from_tuples([("coordinate", "axis.0")], names=("kind", "name"))
    )
    assert str(rows.dtypes.iloc[0]) == "int64[pyarrow]"
    assert all(column.nullable for column in outputs[0].columns)
    assert len(request.numeric_checks) == 2
    assert trace.returned_rows == [len(rows)]
    assert locals_ == []
    assert trace.uploaded_key_rows == []
    assert terminal_runs(session) == ()


def test_private_native_union_finish_executes_source_order_layout_and_checks(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the existing private finish builder without enabling a public route."""
    session = source_query_session
    entity = session.catalog.entities.get("sales.orders")
    amount = session.catalog.fields.get("sales.orders.amount")
    left = session.members(entity, where=amount >= 20)
    right = session.members(entity, where=amount <= 20)
    union = left.union(right)
    pending = session.frame(union)
    assert isinstance(pending._backing, ExpressionFrameBacking)
    assert isinstance(pending._backing.root, SourceFrameBinding)
    graph = pending._backing.root.graph
    trace = capture_source(monkeypatch)
    raw_frames: list[pd.DataFrame] = []
    complete = graph_source_execution._complete_source_frame

    def capture_complete(
        stage: SourceRead,
        frame: pd.DataFrame,
        numeric_checks: tuple[tuple[str, NumericCheck], ...],
        *,
        value_keys: tuple[str, ...] = (),
    ) -> StageOutput:
        raw_frames.append(frame.copy())
        return complete(stage, frame, numeric_checks, value_keys=value_keys)

    monkeypatch.setattr(graph_source_execution, "_complete_source_frame", capture_complete)
    with graph_source_execution.open_query_sources(
        session._runtime.connection_service(), graph, session.catalog._state
    ) as prepared:
        plan = plan_query(graph, prepared.schemas)
        endpoint = next(stage for stage in plan.stages if stage.stage_id == plan.publish)
        assert isinstance(endpoint, LocalCompute)
        assert isinstance(endpoint.program, FormulaProgram)
        atoms = (left._definition, right._definition)
        bindings = tuple(
            next(
                binding
                for binding in endpoint.program.populations
                if computation_fingerprint(binding.definition) == computation_fingerprint(atom)
            )
            for atom in atoms
        )
        layout = ComparisonEndpoint(
            endpoint.columns, endpoint.keys, graph.signature.axes, plan.output_groups
        )
        endpoint = replace(
            endpoint,
            inputs=tuple(binding.stage_id for binding in bindings),
            program=ResultProgram(
                PopulationValues(union._definition),
                endpoint.columns,
                endpoint.keys,
                graph.signature.axes,
                plan.output_groups,
                plan.output_groups,
                population_inputs=atoms,
                population_layouts=tuple(binding.key_groups for binding in bindings),
                output_layout=layout,
            ),
        )
        stages = tuple(
            endpoint if stage.stage_id == endpoint.stage_id else stage
            for stage in plan.stages
            if stage.stage_id in (*endpoint.inputs, endpoint.stage_id)
        )
        plan = replace(
            plan,
            stages=stages,
            consumers=tuple(
                (
                    stage.stage_id,
                    tuple(item.stage_id for item in stages if stage.stage_id in item.inputs),
                )
                for stage in stages
            ),
        )
        lowered = fuse_query_terminal(prepared, lower_query(plan, prepared.relations))
        assert len(lowered.plan.stages) == len(lowered.reads) == 1
        stage = lowered.plan.source_read(lowered.plan.publish)
        assert isinstance(stage.program, NativeRead)
        finish = stage.program.finish
        assert finish is not None
        assert finish.order == ("__query_union_order", "key.0")
        assert finish.columns == stage.columns == layout.columns
        assert finish.layout == layout
        request = lowered.read(stage.stage_id)
        assert len(request.numeric_checks) == 2
        output = graph_source_execution.execute_numeric_query(prepared, lowered)
    assert output.table.iloc[:, 0].tolist() == [2, 3, 1]
    assert tuple(output.table.columns) == ("key.0",)
    assert output.layout == layout
    assert output.key_groups == (("key.0",),)
    assert output.layout.key_groups == (("key.0",),)
    assert output.columns[0].nullable
    assert str(output.table.dtypes.iloc[0]) == "int64[pyarrow]"
    assert trace.returned_rows == [3]
    assert len(trace.native_sql) == 1
    assert trace.uploaded_key_rows == []
    assert len(raw_frames) == 1
    raw = raw_frames[0]
    assert "__query_union_order" in raw.columns
    assert all(name in raw.columns for name, _ in request.numeric_checks)
    # The actual finish payload must check failures before projection can discard flags.
    name, check = request.numeric_checks[0]
    raw["__finish_numeric_failure"] = True
    with pytest.raises(SemanticExpressionError, match="non-NA source values"):
        complete(
            stage,
            raw,
            (("__finish_numeric_failure", replace(check, expected="non-NA source values")),),
        )
    assert name not in output.table.columns
    assert terminal_runs(session) == ()


def test_fixed_top_identity_bridge_reads_explicit_source(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = source_query_session
    source = _attributes(session)
    fixed = source.top(1, by=source["amount"]).compute()
    keys = _identities(session, fixed)
    members = session.members(
        session.catalog.entities.get("sales.orders"), where=mv.in_population(keys)
    )
    trace = capture_source(monkeypatch)
    rows = (
        session.observe(
            {"total": session.catalog.fields.get("sales.orders.amount").sum()},
            where=mv.in_population(members),
        )
        .compute()
        .to_pandas()
    )
    assert fixed.to_pandas().iloc[:, 0].tolist() == [2]
    assert rows["value", "total"].tolist() == [30]
    assert trace.uploaded_key_rows == [1]


def test_empty_selection_retains_nonnull_identity_checks(
    source_query_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _attributes(source_query_session)
    filtered = source.where(source["amount"] > 100)
    query = filtered.top(0, by=filtered["amount"])
    with duckdb.connect(str(tmp_path / "query.duckdb")) as db:
        db.execute("INSERT INTO orders VALUES (NULL,'different',99,0)")
    trace = capture_source(monkeypatch)
    reads, outputs, locals_ = _capture_stages(monkeypatch)
    with pytest.raises(MaterializationError, match="non-null complete identity keys") as caught:
        query.compute()
    assert caught.value.stage == "population_identity"
    assert caught.value.expected == "non-null complete identity keys"
    assert caught.value.location == "dataset.population_identity"
    assert len(reads) == len(trace.native_sql) == 1
    assert isinstance(reads[0][0].program, NativeRead)
    assert reads[0][0].program.finish is None
    assert reads[0][1].numeric_checks
    assert outputs == []
    assert locals_ == []
    assert sum(trace.returned_rows) <= 6
    assert trace.uploaded_key_rows == []
    assert terminal_runs(source_query_session) == ()


def test_cell_sum_widens_before_empty_filter(
    source_query_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _attributes(source_query_session)
    total = source.reduce({"total": source["amount"].sum()})
    with duckdb.connect(str(tmp_path / "query.duckdb")) as db:
        db.execute("UPDATE orders SET amount=9223372036854775807 WHERE id IN (1,2)")
    trace = capture_source(monkeypatch)
    reads, outputs, locals_ = _capture_stages(monkeypatch)
    assert total.compute().to_pandas()["value", "total"].tolist() == [2 * (2**63 - 1) + 20]
    assert total.where(total["total"] < 0).compute().to_pandas().empty
    assert len(reads) == len(outputs) == len(trace.native_sql) == 2
    assert trace.returned_rows == [4, 4]
    assert trace.uploaded_key_rows == []
    assert terminal_runs(source_query_session) == ()


def test_fixed_aggregate_axis_bridge_reads_explicit_source(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = source_query_session
    amount = session.catalog.fields.get("sales.orders.amount")
    producer = session.observe(
        {"total": amount.sum()}, by=(session.catalog.fields.get("sales.orders.region"),)
    )
    fixed = producer.where(producer["total"] >= 30).compute()
    trace = capture_source(monkeypatch)
    result = session.observe(
        {"total": amount.sum()}, where=mv.in_population(_regions(session, fixed))
    ).compute()
    assert fixed.to_pandas().iloc[:, 0].tolist() == ["A"]
    assert result.to_pandas()["value", "total"].tolist() == [40]
    assert trace.uploaded_key_rows == [1]


@pytest.mark.parametrize("selected_region", ("A", "missing"))
def test_shared_frame_key_constraint_keeps_complement_masks(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch, selected_region: str
) -> None:
    session = source_query_session
    source = _attributes(session)
    fixed = source.where(source["region"] == selected_region).compute()
    condition = mv.in_population(_identities(session, fixed))
    amount = session.catalog.fields.get("sales.orders.amount")
    trace = capture_source(monkeypatch)
    result = (
        session.observe(
            {
                "selected": amount.sum().where(condition),
                "other": amount.sum().where(~condition),
            }
        )
        .compute()
        .to_pandas()
    )
    assert result["value", "selected"].tolist() == [40 if selected_region == "A" else 0]
    assert result["value", "other"].tolist() == [20 if selected_region == "A" else 60]
    assert trace.uploaded_key_rows == [2 if selected_region == "A" else 0]


@pytest.mark.parametrize("method", ("sum", "mean"))
def test_aggregate_selection_keeps_reducer_owned_null_policy(
    source_query_session: Session, method: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = source_query_session
    amount = session.catalog.fields.get("sales.orders.amount")
    metric = amount.sum(nulls="error") if method == "sum" else amount.mean(nulls="error")
    producer = session.observe(
        {"value": metric}, by=(session.catalog.fields.get("sales.orders.region"),)
    )
    chosen = producer.where(producer["value"] > 1000)
    query = session.observe(
        {"total": amount.sum()}, where=mv.in_population(_regions(session, chosen))
    )
    trace = capture_source(monkeypatch)
    reads, outputs, locals_ = _capture_stages(monkeypatch)
    if method == "mean":
        result = query.compute().to_pandas()
        assert result["value", "total"].tolist() == [0]
        assert not any(
            check.received == "mean(nulls='error')"
            for _, request in reads
            for _, check in request.numeric_checks
        )
        assert terminal_runs(session) == ()
        return
    with pytest.raises(MaterializationError, match="non-NA") as caught:
        query.compute()
    assert caught.value.stage == "numeric_value"
    assert caught.value.expected == "non-NA source values"
    assert (
        caught.value.received
        == f"{method}(nulls='error'); source=sales.orders; coordinates=[{{'key.0': None}}]"
    )
    assert len(reads) == len(trace.native_sql) == 1
    assert reads[0][1].numeric_checks
    assert outputs == []
    assert locals_ == []
    assert trace.returned_rows == [3]
    assert trace.uploaded_key_rows == []
    assert terminal_runs(session) == ()


def test_aggregate_top_population_keeps_original_parent_ranking(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = source_query_session
    amount = session.catalog.fields.get("sales.orders.amount")
    producer = session.observe(
        {"value": amount.sum()}, by=(session.catalog.fields.get("sales.orders.region"),)
    )
    fixed = producer.top(1, by=producer["value"]).compute()
    trace = capture_source(monkeypatch)
    rows = (
        session.observe({"total": amount.sum()}, where=mv.in_population(_regions(session, fixed)))
        .compute()
        .to_pandas()
    )
    assert fixed.to_pandas().iloc[:, 0].tolist() == ["A"]
    assert rows["value", "total"].tolist() == [40]
    assert trace.uploaded_key_rows == [1]


def test_top_ties_use_complete_identity_and_missing_scores_last(
    source_query_session: Session, tmp_path: Path
) -> None:
    source = _attributes(source_query_session)
    with duckdb.connect(str(tmp_path / "query.duckdb")) as db:
        db.execute("UPDATE orders SET amount=20 WHERE id IN (1,2,3)")
    assert source.top(4, by=source["amount"]).compute().to_pandas().iloc[:, 0].tolist() == [
        1,
        2,
        3,
        4,
    ]


def test_cell_sum_empty_input_returns_zero(source_query_session: Session) -> None:
    source = _attributes(source_query_session)
    selected = source.where(source["amount"] > 100)
    rows = selected.reduce({"sum": selected["amount"].sum()}).compute().to_pandas()
    assert len(rows) == 1
    assert rows["value", "sum"].tolist() == [0]


@pytest.mark.parametrize("operation", ("align", "compare"))
def test_shared_source_frames_keep_complete_endpoint_presence(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    source = _attributes(source_query_session)
    left = source.where((source["amount"] <= 20) | source["amount"].is_null())
    left = left.select({"value": left["amount"]})
    right = source.where((source["amount"] >= 20) | source["amount"].is_null())
    right = right.select({"value": right["amount"]})
    on = ((left.axes[0], right.axes[0]),)
    paired = (
        left.align(right, on=on)
        if operation == "align"
        else left.compare({"base": right}, on={"base": on})
    )
    rows = paired.compute().to_pandas()
    assert rows.iloc[:, 0].tolist() == [1, 3, 4, 2]
    current_presence = "left_present" if operation == "align" else "current.present"
    baseline_presence = "right_present" if operation == "align" else "base.present"
    current_value = "left.value" if operation == "align" else "value"
    assert rows["presence", current_presence].tolist() == [True, True, True, False]
    assert rows["presence", baseline_presence].tolist() == [False, True, True, True]
    assert rows["value", current_value].dropna().tolist() == [10, 20]


def test_same_source_outer_alignment_allows_top_zero(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _attributes(source_query_session)
    left = source.where(source["amount"] <= 20)
    right = source.where(source["amount"] >= 20)
    paired = left.align(right, on=((left.axes[0], right.axes[0]),))
    trace = capture_source(monkeypatch)
    assert paired.top(0, by=paired["left.amount"]).compute().to_pandas().empty
    assert trace.uploaded_key_rows == []
    assert terminal_runs(source_query_session) == ()


def test_source_compare_keeps_large_integer_and_na_zero_policy(
    source_query_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = source_query_session
    with duckdb.connect(str(tmp_path / "query.duckdb")) as db:
        db.execute("UPDATE orders SET amount=9007199254740993, baseline=9007199254740992")
    source = session.read(
        session.members(session.catalog.entities.get("sales.orders")),
        {
            "amount": session.catalog.fields.get("sales.orders.amount"),
            "baseline": session.catalog.fields.get("sales.orders.baseline"),
        },
    )
    current = source.select({"value": source["amount"]})
    baseline = source.select({"value": source["baseline"]})
    rows = (
        current.compare({"base": baseline}, on={"base": ((current.axes[0], baseline.axes[0]),)})
        .compute()
        .to_pandas()
    )
    assert rows["value", "value"].tolist() == [9007199254740993] * 4
    assert rows["value", "base.absolute.value"].tolist() == [1] * 4
    with duckdb.connect(str(tmp_path / "query.duckdb")) as db:
        db.execute("UPDATE orders SET amount=NULL, baseline=0 WHERE id=4")
    nullable = source.where(source["amount"].is_null())
    zero = nullable.select({"value": nullable["baseline"]})
    nullable = nullable.select({"value": nullable["amount"]})
    rows = (
        nullable.compare({"base": zero}, on={"base": ((nullable.axes[0], zero.axes[0]),)})
        .compute()
        .to_pandas()
    )
    assert rows["value", "base.absolute.value"].isna().all()


def _sets(session: Session, operation: str) -> mv.Population:
    source = _attributes(session)
    left = _identities(session, source.where(source["amount"] >= 20).compute())
    right = _identities(session, source.where(source["amount"] <= 20).compute())
    return (
        left.union(right)
        if operation == "union"
        else left.intersection(right)
        if operation == "intersection"
        else left.difference(right)
    )


@pytest.mark.parametrize("operation", ("union", "intersection", "difference"))
def test_population_set_algebra_keeps_complete_frame_keys(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    session = source_query_session
    members = session.members(
        session.catalog.entities.get("sales.orders"),
        where=mv.in_population(_sets(session, operation)),
    )
    rows = (
        session.observe(
            {"total": session.catalog.fields.get("sales.orders.amount").sum()},
            where=mv.in_population(members),
        )
        .compute()
        .to_pandas()
    )
    assert rows["value", "total"].tolist() == [
        {"union": 60, "intersection": 20, "difference": 30}[operation]
    ]


def test_grouped_shared_population_consumers_keep_independent_grids(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = source_query_session
    source = _attributes(session)
    fixed = source.where(source["amount"] >= 25).compute()
    condition = mv.in_population(_identities(session, fixed))
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    rows = (
        session.observe(
            {
                "selected": amount.sum().where(condition).where(region == "A"),
                "other": amount.sum().where(~condition).where(region == "B"),
                "nullable": amount.sum().where(~condition),
            },
            by=(region,),
        )
        .compute()
        .to_pandas()
    )
    assert rows.iloc[:, 0].tolist()[:2] == ["A", "B"]
    assert rows["value", "selected"].iloc[0] == 30
    assert rows["value", "other"].iloc[1] == 20
    assert rows["value", "nullable"].iloc[:2].tolist() == [10, 20]
    assert pd.isna(rows["value", "nullable"].iloc[2])
    for name, index in (("selected", 1), ("other", 0)):
        value = rows["value", name].iloc[index]
        assert value == 0


@pytest.mark.parametrize("operation", ("union", "intersection", "difference"))
def test_population_set_terminal_uses_complete_frame_keys(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    selected = _sets(source_query_session, operation)
    trace = capture_source(monkeypatch)
    rows = source_query_session.frame(selected).compute().to_pandas()
    assert (
        rows.iloc[:, 0].tolist()
        == {"union": [2, 3, 1], "intersection": [3], "difference": [2]}[operation]
    )
    assert rows.columns.equals(
        pd.MultiIndex.from_tuples([("coordinate", "axis.0")], names=("kind", "name"))
    )
    assert str(rows.dtypes.iloc[0]) == "int64[pyarrow]"
    assert trace.native_sql == []
    assert trace.uploaded_key_rows == []


@pytest.mark.parametrize("operation", ("product", "project", "filter"))
def test_frame_key_product_projection_and_filter_preserve_domain(
    source_query_session: Session, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    session = source_query_session
    source = _attributes(session)
    selected = _identities(session, source.where(source["amount"] >= 20).compute())
    region = session.catalog.fields.get("sales.orders.region")
    classifications = session.members(session.catalog.entities.get("sales.orders")).classify(
        by=(region,)
    )
    product = mv.product(selected, classifications)
    population = (
        product.project(by=(region,))
        if operation == "project"
        else product.where(mv.in_population(selected))
        if operation == "filter"
        else product
    )
    rows = session.frame(population).compute().to_pandas()
    assert len(rows) == (3 if operation == "project" else 6)
    if operation != "project":
        assert rows.iloc[:, 0].tolist() == [2, 2, 2, 3, 3, 3]
    assert rows.iloc[:, -1].isna().sum() == (1 if operation == "project" else 2)


def test_public_frame_bridge_evidence_keeps_producer_and_consumer_scopes(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = analysis_dsl_case_factory("j1").session
    amount = session.catalog.fields.get("sales.order.amount")
    customer = session.catalog.fields.get("sales.order.customer_id")
    status = session.catalog.fields.get("sales.order.status")
    ordered = session.catalog.fields.get("sales.order.ordered_at")
    producer = session.observe(
        {"count": amount.count(time=ordered).where(status == "paid")},
        by=(session.time_scope(start="2026-09-01", end="2026-10-01"), customer),
    )
    chosen = producer.where(producer["count"] > 0)
    selected = session.population_from(chosen, on=((chosen.axes[1], customer),))
    query = session.observe(
        {"count": amount.count(time=ordered)},
        by=(session.time_scope(start="2026-10-01", end="2026-11-01"),),
        where=mv.in_population(selected),
    )
    trace = capture_source(monkeypatch)
    saved = query.persist()
    reads = [
        dict(item.values) for item in saved.artifact.evidence_digest().items if item.kind == "read"
    ]
    assert len(reads) == 2
    assert len({(read["period_start"], read["period_end"]) for read in reads}) == 2
    starts = set()
    for read in reads:
        start, end = read["period_start"], read["period_end"]
        assert isinstance(start, datetime) and isinstance(end, datetime)
        starts.add((start.month, end.month))
        assert isinstance(read["returned_rows"], int)
        assert read["returned_rows"] >= 0
        assert read["returned_rows"] in trace.returned_rows
        if start.month == 9:
            assert "paid" in str(read["filters"])
    assert starts == {(9, 10), (10, 11)}
    assert len(terminal_runs(session)) == 1
