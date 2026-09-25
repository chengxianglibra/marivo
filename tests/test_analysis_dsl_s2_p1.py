"""Private P1 multi-predecessor comparison and explicit graph sharing."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import duckdb
import ibis
import ibis.expr.types as ir
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.compiler.dsl_j1_source import J1SourcePlan
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.datasets.handles import _RunNodeBindings
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import RunDatasetInput
from marivo.analysis.materialization.dsl_j1_artifact import publish_j1_artifact
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.local_stage import run_j1_compare_local
from marivo.analysis.materialization.source_stage import run_j1_source
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.analysis.observation.dsl_j1 import (
    J1Context,
    J1Difference,
    J1Members,
    j1_row_contracts,
)
from marivo.analysis.operators.dsl_j1_contracts import J1_COMPARE_DIFFERENCE
from marivo.analysis.operators.dsl_j1_values import J1ExecutionResult
from tests.shared_fixtures import DslCase, DslCaseFactory


def _comparison(case: DslCase, store: SessionStore) -> tuple[J1Members, J1Difference]:
    state = case.catalog._state
    context = J1Context(state.registry, state.sidecar, "session", store.store_id)
    domain = case.names.domain
    members = context.members(ms.ref.entity(f"{domain}.customer"))
    metric = ms.ref.metric(f"{domain}.revenue")
    buyer = ms.ref.relationship(f"{domain}.order_buyer")
    july = members.observe(
        metric,
        during=mv.time_scope(start="2026-07-01", end="2026-08-01"),
        via=buyer,
    )
    august = members.observe(
        metric,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=buyer,
    )
    return members, august.compare(july)


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


def _complete_periods(case: DslCase) -> None:
    conn = duckdb.connect(str(case.database_path))
    try:
        conn.executemany(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            [
                ("p1_jb", "B", "web", "paid", "2026-07-15T00:00:00+00:00", 10),
                ("p1_jc", "C", "web", "paid", "2026-07-15T00:00:00+00:00", 20),
                ("p1_jd", "D", "web", "paid", "2026-07-15T00:00:00+00:00", 40),
                ("p1_ad", "D", "web", "paid", "2026-08-15T00:00:00+00:00", 30),
            ],
        )
    finally:
        conn.close()


def test_p1_compare_constructs_shared_graph_and_rejects_independent_members(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    store = SessionStore(case.root)
    store.create_session("p1-construct", session_ref="session")
    members, change = _comparison(case, store)
    assert change.root.inputs[0].role == "current"
    assert change.root.inputs[1].role == "baseline"
    assert change.root.inputs[0].root.inputs[0].root is members.root
    assert change.root.inputs[1].root.inputs[0].root is members.root
    state = case.catalog._state
    other = J1Context(state.registry, state.sidecar, "session", store.store_id)
    domain = case.names.domain
    independently_built = other.members(ms.ref.entity(f"{domain}.customer"))
    with pytest.raises(DatasetConstructionError, match="one explicit member node"):
        change.current.compare(
            independently_built.observe(
                ms.ref.metric(f"{domain}.revenue"),
                during=mv.time_scope(start="2026-07-01", end="2026-08-01"),
                via=ms.ref.relationship(f"{domain}.order_buyer"),
            )
        )


def test_p1_source_compare_exact_keys_and_values(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    store = SessionStore(case.root)
    store.create_session("p1-source", session_ref="session")
    _, change = _comparison(case, store)
    _complete_periods(case)
    with _source(case) as (backend, tables):
        result = run_j1_source(change.context, change.root, backend, tables)
    assert [(row["member"], row["value"]) for row in result.primary.to_pylist()] == [
        ("A", 373),
        ("B", 140),
        ("C", 380),
        ("D", -10),
    ]
    runtime = DatasetRuntime(store, "session")
    saved = runtime.execute_j1(change, source=lambda: _source(case))
    assert saved.to_pandas()["value"].tolist() == [373, 140, 380, -10]


def test_p1_fixed_pair_uses_exact_shared_member_binding(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    _complete_periods(case)
    store = SessionStore(case.root)
    store.create_session("p1-fixed", session_ref="session")
    members, change = _comparison(case, store)
    with _source(case) as (backend, tables):
        member_result = run_j1_source(change.context, members.root, backend, tables)
        shared: _RunNodeBindings[J1SourcePlan] = _RunNodeBindings(change.context.session_id)
        shared.bind(members.root, J1SourcePlan(ibis.memtable(member_result.primary)))
        current_result = run_j1_source(
            change.context, change.current.root, backend, tables, shared_plans=shared
        )
        baseline_result = run_j1_source(
            change.context, change.baseline.root, backend, tables, shared_plans=shared
        )
    refs = []
    with session_writer_guard(store.layout.lock_path("session"), session_ref="session"):
        for index, (node, result) in enumerate(
            ((change.current, current_result), (change.baseline, baseline_result))
        ):
            row, _ = j1_row_contracts(change.context, node.root)
            key = f"p1_shared_endpoint_{index}"
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
            saved = publish_j1_artifact(
                store,
                run,
                node,
                result,
                input_binding=key,
                member_binding="p1_shared_member_realization",
            )
            refs.append(saved.artifact_ref)
    runtime = DatasetRuntime(store, "session")
    run_count = len(runtime.runs(limit=20).items)
    with pytest.raises(DatasetConstructionError, match="ordered J1 predecessor"):
        runtime.execute_j1(
            change,
            input_nodes=(change.current, change.baseline),
            input_artifact_refs=(refs[1], refs[0]),
        )
    assert len(runtime.runs(limit=20).items) == run_count

    def source_must_not_reopen(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("source reopened")

    monkeypatch.setattr(duckdb, "connect", source_must_not_reopen)
    fixed = runtime.execute_j1(
        change,
        input_nodes=(change.current, change.baseline),
        input_artifact_refs=(refs[0], refs[1]),
    )
    assert fixed.to_pandas()["value"].tolist() == [373, 140, 380, -10]
    run_count = len(runtime.runs(limit=20).items)
    repeated = runtime.execute_j1(
        change,
        input_nodes=(change.current, change.baseline),
        input_artifact_refs=(refs[0], refs[1]),
    )
    assert repeated.state.artifact_ref == fixed.state.artifact_ref
    assert len(runtime.runs(limit=20).items) == run_count


def test_p1_shared_member_is_read_once_in_each_source_evaluation(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    _complete_periods(case)
    store = SessionStore(case.root)
    store.create_session("p1-sharing", session_ref="session")
    _, change = _comparison(case, store)
    runtime = DatasetRuntime(store, "session")
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

    first = runtime.execute_j1(change, source=counted_source)
    second = runtime.execute_j1(change, source=counted_source)
    assert member_reads == 2
    assert source_opens == 2
    assert first.state.artifact_ref != second.state.artifact_ref
    assert first.definition_fingerprint == second.definition_fingerprint


def test_p1_mixed_and_independent_fixed_inputs_reject_before_rows(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    store = SessionStore(case.root)
    store.create_session("p1-reject", session_ref="session")
    _, change = _comparison(case, store)
    runtime = DatasetRuntime(store, "session")
    opened = 0

    def forbidden_source():
        nonlocal opened
        opened += 1
        return _source(case)

    with pytest.raises(DatasetConstructionError, match="live source and explicit Artifact"):
        runtime.execute_j1(
            change,
            source=forbidden_source,
            input_nodes=(change.current, change.baseline),
            input_artifact_refs=("first", "second"),
        )
    assert opened == 0
    assert len(runtime.runs(limit=20).items) == 0

    current = runtime.execute_j1(change.current, source=lambda: _source(case))
    baseline = runtime.execute_j1(change.baseline, source=lambda: _source(case))
    run_count = len(runtime.runs(limit=20).items)

    def artifact_rows_must_not_open(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Artifact rows opened")

    monkeypatch.setattr(
        "marivo.analysis.materialization.dsl_j1_runtime.load_j1_artifact",
        artifact_rows_must_not_open,
    )
    with pytest.raises(DatasetConstructionError, match="shared member implementation"):
        runtime.execute_j1(
            change,
            input_nodes=(change.current, change.baseline),
            input_artifact_refs=(current.state.artifact_ref.ref, baseline.state.artifact_ref.ref),
        )
    assert len(runtime.runs(limit=20).items) == run_count


def test_p1_source_failure_closes_source_and_publishes_no_result(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    store = SessionStore(case.root)
    store.create_session("p1-failure", session_ref="session")
    _, change = _comparison(case, store)
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


def test_p1_compare_publication_requires_registered_checks(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    _complete_periods(case)
    store = SessionStore(case.root)
    store.create_session("p1-checks", session_ref="session")
    _, change = _comparison(case, store)
    with _source(case) as (backend, tables):
        result = run_j1_source(change.context, change.root, backend, tables)
    missing = J1_COMPARE_DIFFERENCE.contract.required_checks[0]
    incomplete = J1ExecutionResult(
        change.root,
        result.primary,
        result.parts,
        tuple(check for check in result.completed_checks if check != missing),
    )
    row, _ = j1_row_contracts(change.context, change.root)
    with session_writer_guard(store.layout.lock_path("session"), session_ref="session"):
        run = store.admit(
            "session",
            "p1_missing_required_check",
            RunDatasetInput(
                change.root.definition_fingerprint,
                row.shape_id,
                change.root.row_contract_fingerprint,
                change.root.row_set_contract_fingerprint,
                (change.root.operator_id,),
                (f"{case.names.domain}.customer", f"{case.names.domain}.order"),
            ),
        )
        with pytest.raises(MaterializationError, match="comparison obligations are incomplete"):
            publish_j1_artifact(
                store,
                run,
                change,
                incomplete,
                input_binding="p1_missing_required_check",
                member_binding=run.run_ref,
            )
    assert store.lookup("session", "p1_missing_required_check") is None


def test_p1_local_pair_rejects_missing_side_before_arithmetic(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    _complete_periods(case)
    store = SessionStore(case.root)
    store.create_session("p1-missing-side", session_ref="session")
    _, change = _comparison(case, store)
    with _source(case) as (backend, tables):
        current = run_j1_source(change.context, change.current.root, backend, tables)
        baseline = run_j1_source(change.context, change.baseline.root, backend, tables)
    missing = J1ExecutionResult(
        baseline.root,
        baseline.primary.slice(0, 3),
        completed_checks=baseline.completed_checks,
    )
    with pytest.raises(MaterializationError, match="missing comparison side"):
        run_j1_compare_local(change.root, current, missing)
