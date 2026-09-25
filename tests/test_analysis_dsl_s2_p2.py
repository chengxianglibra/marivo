"""Private J2 strict selection and next-period observation."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager

import duckdb
import ibis
import ibis.expr.types as ir
import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.compiler.dsl_j1_source import J1SourcePlan
from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.datasets.handles import _RunNodeBindings
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import LocalReceipt, RunDatasetInput
from marivo.analysis.materialization.dsl_j1_artifact import (
    load_j1_artifact,
    publish_j1_artifact,
)
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.local_stage import run_j1_compare_local, run_j1_local
from marivo.analysis.materialization.source_stage import run_j1_source
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.analysis.observation.dsl_j1 import (
    J1Context,
    J1Difference,
    J1Members,
    J1Observed,
    J1SelectedDifference,
    j1_row_contracts,
)
from marivo.analysis.operators.dsl_j1_values import J1ExecutionResult
from tests.shared_fixtures import DslCase, DslCaseFactory


def _j2(
    case: DslCase, store: SessionStore
) -> tuple[J1Members, J1Difference, J1SelectedDifference, J1Members, J1Observed]:
    state = case.catalog._state
    context = J1Context(state.registry, state.sidecar, "session", store.store_id)
    domain = case.names.domain
    members = context.members(ms.ref.entity(f"{domain}.customer"))
    revenue = ms.ref.metric(f"{domain}.revenue")
    buyer = ms.ref.relationship(f"{domain}.order_buyer")
    july = members.observe(
        revenue, during=mv.time_scope(start="2026-07-01", end="2026-08-01"), via=buyer
    )
    august = members.observe(
        revenue, during=mv.time_scope(start="2026-08-01", end="2026-09-01"), via=buyer
    )
    change = august.compare(july)
    selected = change.where(change.value.lt(0))
    selected_members = selected.members()
    september = selected_members.observe(
        revenue, during=mv.time_scope(start="2026-09-01", end="2026-10-01"), via=buyer
    )
    return members, change, selected, selected_members, september


@contextmanager
def _source(case: DslCase) -> Iterator[tuple[ibis.BaseBackend, dict[str, ir.Table]]]:
    backend = ibis.duckdb.connect(str(case.database_path))
    try:
        domain = case.names.domain
        yield (
            backend,
            {
                f"{domain}.customer": backend.table("customer"),
                f"{domain}.order": backend.table("order"),
            },
        )
    finally:
        backend.disconnect()


def test_p2_j2_lazy_source_chain_matches_independent_oracle(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    store = SessionStore(case.root)
    store.create_session("p2-source", session_ref="session")
    members, change, selected, selected_members, september = _j2(case, store)
    assert selected.domain != members.domain
    assert september.domain == selected.domain
    assert september.root.inputs[0].root is selected_members.root
    runtime = DatasetRuntime(store, "session")

    saved_change = runtime.execute_j1(change, source=lambda: _source(case))
    changed = saved_change.to_pandas()
    assert dict(zip(changed["member"], changed["value"], strict=True)) == {
        "A": -40,
        "B": 20,
        "C": -50,
        "D": 0,
    }
    saved_selected = runtime.execute_j1(selected, source=lambda: _source(case))
    selected_frame = saved_selected.to_pandas()
    assert dict(zip(selected_frame["member"], selected_frame["value"], strict=True)) == {
        "A": -40,
        "C": -50,
    }
    assert not hasattr(change, "rollup")
    selected_mean = runtime.execute_j1(selected.summarize("mean"), source=lambda: _source(case))
    assert selected_mean.to_pandas()["value"].tolist() == [-45.0]
    saved_members = runtime.execute_j1(selected_members, source=lambda: _source(case))
    assert set(saved_members.to_pandas()["member"]) == {"A", "C"}
    final = runtime.execute_j1(september.summarize("mean"), source=lambda: _source(case))
    frame = final.to_pandas()
    assert frame["value"].tolist() == [15.0]
    assert frame["cell_tag"].tolist() == ["defined"]


def _capture_endpoints(
    case: DslCase, store: SessionStore, members: J1Members, change: J1Difference
) -> tuple[str, str]:
    with _source(case) as (backend, tables):
        member_result = run_j1_source(change.context, members.root, backend, tables)
        shared: _RunNodeBindings[J1SourcePlan] = _RunNodeBindings(change.context.session_id)
        shared.bind(members.root, J1SourcePlan(ibis.memtable(member_result.primary)))
        current = run_j1_source(
            change.context, change.current.root, backend, tables, shared_plans=shared
        )
        baseline = run_j1_source(
            change.context, change.baseline.root, backend, tables, shared_plans=shared
        )
    refs: list[str] = []
    with session_writer_guard(store.layout.lock_path("session"), session_ref="session"):
        for index, (node, result) in enumerate(
            ((change.current, current), (change.baseline, baseline))
        ):
            row, _ = j1_row_contracts(change.context, node.root)
            key = f"p2_endpoint_{index}"
            run = store.admit(
                "session",
                key,
                RunDatasetInput(
                    node.root.definition_fingerprint,
                    row.shape_id,
                    node.root.row_contract_fingerprint,
                    node.root.row_set_contract_fingerprint,
                    (node.root.operator_id,),
                    (f"{case.names.domain}.customer", f"{case.names.domain}.order"),
                ),
            )
            published = publish_j1_artifact(
                store,
                run,
                node,
                result,
                input_binding=key,
                member_binding="p2_shared_member_realization",
            )
            refs.append(published.artifact_ref)
    return refs[0], refs[1]


def test_p2_fixed_compare_select_members_and_current_mean_offline(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j2")
    store = SessionStore(case.root)
    store.create_session("p2-fixed", session_ref="session")
    members, change, selected, selected_members, september = _j2(case, store)
    refs = _capture_endpoints(case, store, members, change)

    def forbidden_connect(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("fixed continuation reopened DuckDB")

    monkeypatch.setattr(duckdb, "connect", forbidden_connect)
    runtime = DatasetRuntime(store, "session")
    before = len(runtime.runs(limit=20).items)
    with pytest.raises(DatasetConstructionError, match="live source and explicit Artifact"):
        runtime.execute_j1(
            change,
            source=lambda: _source(case),
            input_nodes=(change.current, change.baseline),
            input_artifact_refs=refs,
        )
    assert len(runtime.runs(limit=20).items) == before
    fixed = runtime.execute_j1(
        change,
        input_nodes=(change.current, change.baseline),
        input_artifact_refs=refs,
    )
    fixed_ref = fixed.state.artifact_ref.ref
    fixed_selected = runtime.execute_j1(selected, input_node=change, input_artifact_ref=fixed_ref)
    selected_ref = fixed_selected.state.artifact_ref.ref
    selected_frame = fixed_selected.to_pandas()
    assert dict(zip(selected_frame["member"], selected_frame["value"], strict=True)) == {
        "A": -40,
        "C": -50,
    }
    fixed_members = runtime.execute_j1(
        selected_members, input_node=selected, input_artifact_ref=selected_ref
    )
    assert set(fixed_members.to_pandas()["member"]) == {"A", "C"}
    difference_mean = runtime.execute_j1(
        selected.summarize("mean"), input_node=selected, input_artifact_ref=selected_ref
    )
    assert difference_mean.to_pandas()["value"].tolist() == [-45.0]
    for method, expected in (("sum", -90), ("count", 2)):
        statistic = runtime.execute_j1(
            selected.summarize(method), input_node=selected, input_artifact_ref=selected_ref
        )
        assert statistic.to_pandas()["value"].tolist() == [expected]
    total_count = runtime.execute_j1(
        change.summarize("count"), input_node=change, input_artifact_ref=fixed_ref
    )
    assert total_count.to_pandas()["value"].tolist() == [4]
    empty_selection = change.where(change.value.lt(-100))
    fixed_empty = runtime.execute_j1(
        empty_selection, input_node=change, input_artifact_ref=fixed_ref
    )
    assert fixed_empty.to_pandas().empty
    empty_ref = fixed_empty.state.artifact_ref.ref
    empty_record = store.artifact(empty_ref)
    assert empty_record is not None and empty_record.descriptor.j1_exchange is not None
    recovered_empty = load_j1_artifact(
        SessionStore.open_existing(case.root),
        "session",
        empty_ref,
        empty_selection,
        input_binding=empty_record.descriptor.j1_exchange.input_binding,
    )
    assert recovered_empty.primary.num_rows == 0
    assert tuple((role, part.num_rows) for role, part in recovered_empty.parts) == (
        ("current_endpoint", 0),
        ("baseline_endpoint", 0),
    )
    with pytest.raises(DatasetConstructionError, match="live source and explicit Artifact"):
        runtime.execute_j1(
            september,
            input_node=selected_members,
            input_artifact_ref=fixed_members.state.artifact_ref.ref,
        )
    with pytest.raises(DatasetConstructionError, match="live source and explicit Artifact"):
        runtime.execute_j1(
            september,
            source=lambda: _source(case),
            input_node=selected_members,
            input_artifact_ref=fixed_members.state.artifact_ref.ref,
        )
    record = store.artifact(selected_ref)
    assert record is not None and record.descriptor.j1_exchange is not None
    endpoint = next(
        part.storage_receipt
        for part in record.descriptor.retained_parts
        if part.role == "current_endpoint"
    )
    assert isinstance(endpoint, LocalReceipt)
    with (case.root / endpoint.project_relative_path / "data.parquet").open("ab") as output:
        output.write(b"corrupt")
    with pytest.raises(MaterializationError):
        load_j1_artifact(
            SessionStore.open_existing(case.root),
            "session",
            selected_ref,
            selected,
            input_binding=record.descriptor.j1_exchange.input_binding,
        )
    fixed_record = store.artifact(fixed_ref)
    assert fixed_record is not None and fixed_record.descriptor.j1_exchange is not None
    load_j1_artifact(
        SessionStore.open_existing(case.root),
        "session",
        fixed_ref,
        change,
        input_binding=fixed_record.descriptor.j1_exchange.input_binding,
    )


def test_p2_nested_compare_realizes_members_once(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j2")
    store = SessionStore(case.root)
    store.create_session("p2-sharing", session_ref="session")
    members, change, selected, selected_members, september = _j2(case, store)
    assert change.current.root.inputs[0].root is members.root
    assert change.baseline.root.inputs[0].root is members.root
    assert selected.root.inputs[0].root is change.root
    assert selected_members.root.inputs[0].root is selected.root
    assert september.root.inputs[0].root is selected_members.root
    member_reads = 0
    source_opens = 0

    @contextmanager
    def counted_source() -> Iterator[tuple[ibis.BaseBackend, dict[str, ir.Table]]]:
        nonlocal member_reads, source_opens
        source_opens += 1
        with _source(case) as (backend, tables):
            original = backend.to_pyarrow_batches

            def counted(expression: ir.Table, *, chunk_size: int):
                nonlocal member_reads
                if tuple(expression.columns) == ("member",):
                    member_reads += 1
                return original(expression, chunk_size=chunk_size)

            monkeypatch.setattr(backend, "to_pyarrow_batches", counted)
            yield backend, tables

    runtime = DatasetRuntime(store, "session")
    first = runtime.execute_j1(september.summarize("mean"), source=counted_source)
    second = runtime.execute_j1(september.summarize("mean"), source=counted_source)
    assert first.to_pandas()["value"].tolist() == second.to_pandas()["value"].tolist() == [15]
    assert (member_reads, source_opens) == (2, 2)
    assert first.state.artifact_ref != second.state.artifact_ref


def test_p2_empty_selected_domain_produces_undefined_mean(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    store = SessionStore(case.root)
    store.create_session("p2-empty", session_ref="session")
    _, change, _, _, _ = _j2(case, store)
    empty = change.where(change.value.lt(-100)).members()
    september = empty.observe(
        ms.ref.metric(f"{case.names.domain}.revenue"),
        during=mv.time_scope(start="2026-09-01", end="2026-10-01"),
        via=ms.ref.relationship(f"{case.names.domain}.order_buyer"),
    )
    runtime = DatasetRuntime(store, "session")
    result = runtime.execute_j1(september.summarize("mean"), source=lambda: _source(case))
    frame = result.to_pandas()
    assert frame["cell_tag"].tolist() == ["undefined"]
    assert frame["cell_reason"].tolist() == ["empty_mean"]
    assert frame["value"].isna().tolist() == [True]


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ("lt", {"A", "C"}),
        ("lte", {"A", "C", "D"}),
        ("gt", {"B"}),
        ("gte", {"B", "D"}),
        ("eq", {"D"}),
    ],
)
def test_p2_numeric_predicates_match_source_and_local(
    analysis_dsl_case_factory: DslCaseFactory, operation: str, expected: set[str]
) -> None:
    case = analysis_dsl_case_factory("j2")
    store = SessionStore(case.root)
    store.create_session("p2-predicate", session_ref="session")
    _, change, _, _, _ = _j2(case, store)
    selected = change.where(getattr(change.value, operation)(0))
    with _source(case) as (backend, tables):
        source = run_j1_source(change.context, selected.root, backend, tables)
        prior = run_j1_source(change.context, change.root, backend, tables)
    local = run_j1_local(selected.root, prior)
    assert {row["member"] for row in source.primary.to_pylist()} == expected
    assert source.primary.equals(local.primary)
    assert all(
        left.equals(right) for (_, left), (_, right) in zip(source.parts, local.parts, strict=True)
    )


def test_p2_rejects_independent_selected_members_and_invalid_thresholds(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    store = SessionStore(case.root)
    store.create_session("p2-contract", session_ref="session")
    members, change, selected, _, _ = _j2(case, store)
    assert not hasattr(selected, "value")
    with pytest.raises(DatasetConstructionError, match="distinct time-scoped observations"):
        change.current.compare(change.current)
    for threshold in (True, float("nan"), float("inf"), 2**63):
        with pytest.raises(DatasetConstructionError, match="finite int64 or float64"):
            change.value.lt(threshold)
    another_change = change.current.compare(change.baseline)
    with pytest.raises(DatasetConstructionError, match="exact Difference node"):
        change.where(another_change.value.lt(0))

    region = ms.ref.dimension(f"{case.names.domain}.customer.region")
    read = members.read(region)
    first = read.where(read.value.eq("east")).members()
    second = read.where(read.value.eq("east")).members()
    assert first.domain == second.domain
    assert first.root is not second.root
    revenue = ms.ref.metric(f"{case.names.domain}.revenue")
    buyer = ms.ref.relationship(f"{case.names.domain}.order_buyer")
    august = first.observe(
        revenue, during=mv.time_scope(start="2026-08-01", end="2026-09-01"), via=buyer
    )
    july = second.observe(
        revenue, during=mv.time_scope(start="2026-07-01", end="2026-08-01"), via=buyer
    )
    with pytest.raises(DatasetConstructionError, match="one explicit member node"):
        august.compare(july)


def test_p2_rejects_distinct_authored_quantity_and_unit(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    model = case.root / "models" / "semantic" / case.names.domain / "models.py"
    with model.open("a") as output:
        output.write(
            "\nusd_amount = ms.measure_column(name='usd_amount', entity=orders, "
            "column='amount', additivity=ms.additive_all(), unit='USD')\n"
            "usd_revenue = ms.aggregate(name='usd_revenue', measure=usd_amount, "
            "agg='sum', time=ordered_at)\n"
        )
    catalog = ms.load(workspace_dir=case.root)
    state = catalog._state
    store = SessionStore(case.root)
    store.create_session("p2-units", session_ref="session")
    context = J1Context(state.registry, state.sidecar, "session", store.store_id)
    domain = case.names.domain
    members = context.members(ms.ref.entity(f"{domain}.customer"))
    buyer = ms.ref.relationship(f"{domain}.order_buyer")
    august = members.observe(
        ms.ref.metric(f"{domain}.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=buyer,
    )
    july_usd = members.observe(
        ms.ref.metric(f"{domain}.usd_revenue"),
        during=mv.time_scope(start="2026-07-01", end="2026-08-01"),
        via=buyer,
    )
    with pytest.raises(DatasetConstructionError, match="incompatible comparison endpoints"):
        august.compare(july_usd)


def test_p2_rejects_lossy_threshold_before_source_rows(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j2")
    store = SessionStore(case.root)
    store.create_session("p2-threshold", session_ref="session")
    _, change, _, _, _ = _j2(case, store)
    selected = change.where(change.value.lt(0.5))
    with _source(case) as (backend, tables):

        def forbidden_rows(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("inadmissible threshold read source rows")

        monkeypatch.setattr(backend, "to_pyarrow_batches", forbidden_rows)
        with pytest.raises(DatasetCompilationError, match="lossless numeric threshold"):
            run_j1_source(change.context, selected.root, backend, tables)


def test_p2_independent_fixed_endpoints_reject_before_artifact_rows(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j2")
    store = SessionStore(case.root)
    store.create_session("p2-independent", session_ref="session")
    _, change, _, _, _ = _j2(case, store)
    runtime = DatasetRuntime(store, "session")
    current = runtime.execute_j1(change.current, source=lambda: _source(case))
    baseline = runtime.execute_j1(change.baseline, source=lambda: _source(case))
    before = len(runtime.runs(limit=20).items)

    def forbidden_rows(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("independent endpoint Artifact rows opened")

    monkeypatch.setattr(
        "marivo.analysis.materialization.dsl_j1_runtime.load_j1_artifact",
        forbidden_rows,
    )
    with pytest.raises(DatasetConstructionError, match="shared member implementation"):
        runtime.execute_j1(
            change,
            input_nodes=(change.current, change.baseline),
            input_artifact_refs=(
                current.state.artifact_ref.ref,
                baseline.state.artifact_ref.ref,
            ),
        )
    assert len(runtime.runs(limit=20).items) == before


def test_p2_missing_d_and_non_defined_endpoints_reject(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    store = SessionStore(case.root)
    store.create_session("p2-missing", session_ref="session")
    _, change, _, _, _ = _j2(case, store)
    with _source(case) as (backend, tables):
        current = run_j1_source(change.context, change.current.root, backend, tables)
        baseline = run_j1_source(change.context, change.baseline.root, backend, tables)
    nonfinite = current.primary.set_column(
        current.primary.schema.get_field_index("value"),
        "value",
        pa.array(
            [
                float("nan") if row["member"] == "A" else float(row["value"])
                for row in current.primary.to_pylist()
            ],
            type=pa.float64(),
        ),
    )
    with pytest.raises(MaterializationError, match="finite int64 or float64"):
        J1ExecutionResult(current.root, nonfinite, current.parts, current.completed_checks)
    without_d = J1ExecutionResult(
        baseline.root,
        baseline.primary.filter(
            pa.array([row["member"] != "D" for row in baseline.primary.to_pylist()])
        ),
        baseline.parts,
        baseline.completed_checks,
    )
    with pytest.raises(MaterializationError, match="complete exact-key endpoint pairing"):
        run_j1_compare_local(change.root, current, without_d)

    connection = duckdb.connect(str(case.database_path))
    try:
        connection.execute('DELETE FROM "order" WHERE order_id = ?', ["j2_ac"])
    finally:
        connection.close()
    runtime = DatasetRuntime(store, "session")
    closed = 0

    @contextmanager
    def tracked_source() -> Iterator[tuple[ibis.BaseBackend, dict[str, ir.Table]]]:
        nonlocal closed
        try:
            with _source(case) as selected:
                yield selected
        finally:
            closed += 1

    with pytest.raises(MaterializationError, match="strict_numeric_cell"):
        runtime.execute_j1(change, source=tracked_source)
    assert closed == 1
    assert runtime.last_run_ref is not None
    run = store.run(runtime.last_run_ref)
    assert run is not None and run.lifecycle == "failed"
    assert store.lookup("session", run.execution_key_digest) is None


@pytest.mark.runtime
def test_p2_cold_fixed_pair_and_selection_use_only_receipts(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    store = SessionStore(case.root)
    store.create_session("p2-cold", session_ref="session")
    members, change, _, _, _ = _j2(case, store)
    current_ref, baseline_ref = _capture_endpoints(case, store, members, change)
    child = """
import sys
import duckdb
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.observation.dsl_j1 import J1Context

root, current_ref, baseline_ref = sys.argv[1:]
catalog = ms.load(workspace_dir=root)
store = SessionStore.open_existing(root)
state = catalog._state
context = J1Context(state.registry, state.sidecar, 'session', store.store_id)
members = context.members(ms.ref.entity('sales.customer'))
revenue = ms.ref.metric('sales.revenue')
buyer = ms.ref.relationship('sales.order_buyer')
july = members.observe(revenue, during=mv.time_scope(start='2026-07-01', end='2026-08-01'), via=buyer)
august = members.observe(revenue, during=mv.time_scope(start='2026-08-01', end='2026-09-01'), via=buyer)
change = august.compare(july)
selected = change.where(change.value.lt(0))
selected_members = selected.members()
duckdb.connect = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('source reopened'))
runtime = DatasetRuntime(store, 'session')
fixed = runtime.execute_j1(change, input_nodes=(august, july), input_artifact_refs=(current_ref, baseline_ref))
assert fixed.to_pandas()['value'].tolist() == [-40, 20, -50, 0]
filtered = runtime.execute_j1(selected, input_node=change, input_artifact_ref=fixed.state.artifact_ref.ref)
assert filtered.to_pandas()['value'].tolist() == [-40, -50]
chosen = runtime.execute_j1(selected_members, input_node=selected, input_artifact_ref=filtered.state.artifact_ref.ref)
assert chosen.to_pandas()['member'].tolist() == ['A', 'C']
mean = runtime.execute_j1(selected.summarize('mean'), input_node=selected, input_artifact_ref=filtered.state.artifact_ref.ref)
assert mean.to_pandas()['value'].tolist() == [-45.0]
"""
    completed = subprocess.run(
        [sys.executable, "-c", child, str(case.root), current_ref, baseline_ref],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "MARIVO_PROJECT_ROOT": str(case.root)},
    )
    assert completed.returncode == 0, completed.stderr
