"""Private J4 publication, retained continuation and exact recovery."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace

import duckdb
import pytest

from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import LocalReceipt, RunDatasetInput
from marivo.analysis.materialization.dsl_j1_artifact import load_j1_artifact, publish_j1_artifact
from marivo.analysis.materialization.dsl_j4_source import capture_j4_endpoints
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.analysis.observation.dsl_j1 import J4Association, j1_row_contracts
from tests.shared_fixtures import DslCase, DslCaseFactory
from tests.test_analysis_dsl_s3_p1 import _association, _source


def test_j4_source_publishes_and_filters_coefficient(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j4")
    association = _association(case)
    runtime = DatasetRuntime(SessionStore.open_existing(case.root), "session")
    saved = runtime.execute_j1(association, source=lambda: _source(case))
    frame = saved.to_pandas()
    assert frame["coefficient"].tolist() == pytest.approx([-0.4])
    assert frame["status"].tolist() == ["valid"]
    assert frame["metric_key_a"].tolist() == [f"metric:{case.names.domain}.revenue"]
    assert frame["metric_key_b"].tolist() == [f"metric:{case.names.domain}.order_count"]
    assert frame["complete_pair_count"].tolist() == [4]
    record = runtime.store.artifact(saved.state.artifact_ref.ref)
    assert record is not None and record.descriptor.j1_exchange is not None
    assert record.descriptor.j1_exchange.method_id == "dsl.j4.spearman"
    assert record.descriptor.j1_exchange.method_version == 1
    assert record.descriptor.j1_exchange.input_binding == record.execution_key_digest
    assert record.descriptor.j1_exchange.member_binding == record.producing_run_ref
    selection = association.coefficient.where(association.coefficient.value.lt(0))
    selected = runtime.execute_j1(
        selection, input_node=association, input_artifact_ref=saved.state.artifact_ref.ref
    )
    assert selected.to_pandas()["coefficient"].tolist() == pytest.approx([-0.4])
    mean = runtime.execute_j1(
        selection.summarize("mean"),
        input_node=selection,
        input_artifact_ref=selected.state.artifact_ref.ref,
    )
    assert mean.to_pandas()["value"].tolist() == pytest.approx([-0.4])
    count = runtime.execute_j1(
        association.coefficient.summarize("count"),
        input_node=association,
        input_artifact_ref=saved.state.artifact_ref.ref,
    )
    assert count.to_pandas()["value"].tolist() == [1]
    empty_selection = association.coefficient.where(association.coefficient.value.gt(0))
    empty = runtime.execute_j1(
        empty_selection,
        input_node=association,
        input_artifact_ref=saved.state.artifact_ref.ref,
    )
    assert empty.to_pandas().empty
    empty_mean = runtime.execute_j1(
        empty_selection.summarize("mean"),
        input_node=empty_selection,
        input_artifact_ref=empty.state.artifact_ref.ref,
    )
    assert empty_mean.to_pandas()["cell_tag"].tolist() == ["undefined"]
    assert empty_mean.to_pandas()["cell_reason"].tolist() == ["empty_mean"]
    empty_sum = runtime.execute_j1(
        empty_selection.summarize("sum"),
        input_node=empty_selection,
        input_artifact_ref=empty.state.artifact_ref.ref,
    )
    empty_count = runtime.execute_j1(
        empty_selection.summarize("count"),
        input_node=empty_selection,
        input_artifact_ref=empty.state.artifact_ref.ref,
    )
    assert empty_sum.to_pandas()["value"].tolist() == [0.0]
    assert empty_count.to_pandas()["value"].tolist() == [0]
    second = runtime.execute_j1(association, source=lambda: _source(case))
    assert second.state.artifact_ref != saved.state.artifact_ref
    assert second.state.producing_run_ref != saved.state.producing_run_ref


def _capture_fixed_pair(
    case: DslCase,
    association: J4Association,
    *,
    key_prefix: str = "j4_fixed_endpoint",
    member_binding: str = "j4_one_member_implementation",
) -> tuple[str, str]:
    store = SessionStore.open_existing(case.root)
    with _source(case) as (backend, tables):
        left, right = capture_j4_endpoints(association, backend, tables)
    refs: list[str] = []
    with session_writer_guard(store.layout.lock_path("session"), session_ref="session"):
        for index, (node, result) in enumerate(
            ((association.left, left), (association.right, right))
        ):
            row, _ = j1_row_contracts(association.context, node.root)
            key = f"{key_prefix}_{index}"
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
                member_binding=member_binding,
            )
            refs.append(saved.artifact_ref)
    return refs[0], refs[1]


@pytest.mark.runtime
def test_j4_fixed_pair_and_selection_run_without_source(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j4")
    association = _association(case)
    refs = _capture_fixed_pair(case, association)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("fixed J4 reopened the source")

    monkeypatch.setattr(duckdb, "connect", forbidden)
    runtime = DatasetRuntime(SessionStore.open_existing(case.root), "session")
    before = len(runtime.runs(limit=20).items)
    with pytest.raises(DatasetConstructionError, match="live source and explicit Artifact"):
        runtime.execute_j1(
            association,
            source=lambda: _source(case),
            input_nodes=(association.left, association.right),
            input_artifact_refs=refs,
        )
    assert len(runtime.runs(limit=20).items) == before
    saved = runtime.execute_j1(
        association, input_nodes=(association.left, association.right), input_artifact_refs=refs
    )
    assert saved.to_pandas()["coefficient"].tolist() == pytest.approx([-0.4])
    assert saved.to_pandas()["complete_pair_count"].tolist() == [4]
    first_run = runtime.last_run_ref
    again = runtime.execute_j1(
        association, input_nodes=(association.left, association.right), input_artifact_refs=refs
    )
    assert again.state.artifact_ref == saved.state.artifact_ref
    assert runtime.last_run_ref == first_run
    selection = association.coefficient.where(association.coefficient.value.lt(0))
    selected = runtime.execute_j1(
        selection, input_node=association, input_artifact_ref=saved.state.artifact_ref.ref
    )
    assert selected.to_pandas()["coefficient"].tolist() == pytest.approx([-0.4])


@pytest.mark.runtime
def test_j4_source_and_selection_recover_in_new_process(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j4")
    association = _association(case)
    runtime = DatasetRuntime(SessionStore.open_existing(case.root), "session")
    saved = runtime.execute_j1(association, source=lambda: _source(case))
    artifact_ref = saved.state.artifact_ref.ref
    fixed_refs = _capture_fixed_pair(case, association)
    fixed_saved = runtime.execute_j1(
        association,
        input_nodes=(association.left, association.right),
        input_artifact_refs=fixed_refs,
    )
    fixed_ref = fixed_saved.state.artifact_ref.ref
    child = """
import sys
import duckdb
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.observation.dsl_j1 import J1Context

root, artifact_ref, left_ref, right_ref, fixed_ref = sys.argv[1:]
catalog = ms.load(workspace_dir=root)
store = SessionStore.open_existing(root)
state = catalog._state
context = J1Context(state.registry, state.sidecar, 'session', store.store_id)
members = context.members(ms.ref.entity('sales.customer'))
scope = mv.time_scope(start='2026-08-01', end='2026-09-01')
buyer = ms.ref.relationship('sales.order_buyer')
left = members.observe(ms.ref.metric('sales.revenue'), during=scope, via=buyer)
right = members.observe(ms.ref.metric('sales.order_count'), during=scope, via=buyer)
association = left.correlate(right, method='spearman')
duckdb.connect = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('source reopened'))
runtime = DatasetRuntime(store, 'session')
record = store.artifact(artifact_ref)
assert record is not None and record.descriptor.j1_exchange is not None
from marivo.analysis.materialization.dsl_j1_artifact import load_j1_artifact
loaded = load_j1_artifact(store, 'session', artifact_ref, association,
                          input_binding=record.descriptor.j1_exchange.input_binding)
assert loaded.primary.to_pylist()[0]['coefficient'] == -0.4
selection = association.coefficient.where(association.coefficient.value.lt(0))
selected = runtime.execute_j1(selection, input_node=association, input_artifact_ref=artifact_ref)
assert selected.to_pandas()['complete_pair_count'].tolist() == [4]
fixed = runtime.execute_j1(
    association, input_nodes=(left, right), input_artifact_refs=(left_ref, right_ref)
)
assert fixed.state.artifact_ref.ref == fixed_ref
assert fixed.to_pandas()['coefficient'].tolist() == [-0.4]
fixed_record = store.artifact(fixed_ref)
assert fixed_record is not None and fixed_record.descriptor.j1_exchange is not None
loaded_fixed = load_j1_artifact(
    store, 'session', fixed_ref, association,
    input_binding=fixed_record.descriptor.j1_exchange.input_binding
)
assert loaded_fixed.primary.to_pylist()[0]['complete_pair_count'] == 4
fixed_selected = runtime.execute_j1(
    selection, input_node=association, input_artifact_ref=fixed.state.artifact_ref.ref
)
assert fixed_selected.to_pandas()['complete_pair_count'].tolist() == [4]
"""
    completed = subprocess.run(
        [sys.executable, "-c", child, str(case.root), artifact_ref, *fixed_refs, fixed_ref],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "MARIVO_PROJECT_ROOT": str(case.root)},
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.runtime
def test_j4_failed_publication_keeps_committed_artifact(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j4")
    association = _association(case)
    store = SessionStore.open_existing(case.root)
    first = DatasetRuntime(store, "session").execute_j1(association, source=lambda: _source(case))
    first_ref = first.state.artifact_ref.ref
    record = store.artifact(first_ref)
    assert record is not None and record.descriptor.j1_exchange is not None

    def stop(point: str) -> None:
        if point == "before_commit":
            raise RuntimeError("J4 publication stopped")

    failing = DatasetRuntime(store, "session", event=stop)
    with pytest.raises(RuntimeError, match="J4 publication stopped"):
        failing.execute_j1(association, source=lambda: _source(case))
    assert failing.last_run_ref is not None
    run = store.run(failing.last_run_ref)
    assert run is not None and run.lifecycle == "failed"
    assert store.lookup("session", run.execution_key_digest) is None
    assert store.artifact(first_ref) == record
    loaded = load_j1_artifact(
        SessionStore.open_existing(case.root),
        "session",
        first_ref,
        association,
        input_binding=record.descriptor.j1_exchange.input_binding,
    )
    assert loaded.primary.to_pylist()[0]["complete_pair_count"] == 4


@pytest.mark.runtime
def test_j4_independent_captures_reject_before_artifact_rows(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j4")
    association = _association(case)
    first = _capture_fixed_pair(case, association)
    second = _capture_fixed_pair(
        case,
        association,
        key_prefix="j4_independent",
        member_binding="different_member_implementation",
    )

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("independent endpoint rows opened")

    monkeypatch.setattr(
        "marivo.analysis.materialization.dsl_j1_runtime.load_j1_artifact", forbidden
    )
    runtime = DatasetRuntime(SessionStore.open_existing(case.root), "session")
    before = len(runtime.runs(limit=20).items)
    with pytest.raises(DatasetConstructionError, match="shared member implementation"):
        runtime.execute_j1(
            association,
            input_nodes=(association.left, association.right),
            input_artifact_refs=(first[0], second[1]),
        )
    assert len(runtime.runs(limit=20).items) == before


@pytest.mark.runtime
@pytest.mark.parametrize("change", ["method_version", "input_binding"])
def test_j4_wrong_version_or_binding_rejects_before_artifact_rows(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    case = analysis_dsl_case_factory("j4")
    association = _association(case)
    refs = _capture_fixed_pair(case, association)
    store = SessionStore.open_existing(case.root)
    original = store.artifact

    def changed(ref: str):
        record = original(ref)
        if ref == refs[1] and record is not None and record.descriptor.j1_exchange is not None:
            exchange = (
                replace(record.descriptor.j1_exchange, method_version=999)
                if change == "method_version"
                else replace(record.descriptor.j1_exchange, input_binding="wrong_binding")
            )
            return replace(record, descriptor=replace(record.descriptor, j1_exchange=exchange))
        return record

    monkeypatch.setattr(store, "artifact", changed)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("wrong version opened Artifact rows")

    monkeypatch.setattr(
        "marivo.analysis.materialization.dsl_j1_runtime.load_j1_artifact", forbidden
    )
    runtime = DatasetRuntime(store, "session")
    with pytest.raises(DatasetConstructionError, match="wrong method or version"):
        runtime.execute_j1(
            association,
            input_nodes=(association.left, association.right),
            input_artifact_refs=refs,
        )


@pytest.mark.runtime
def test_j4_pair_count_receipt_corruption_blocks_recovery(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j4")
    association = _association(case)
    store = SessionStore.open_existing(case.root)
    saved = DatasetRuntime(store, "session").execute_j1(association, source=lambda: _source(case))
    artifact_ref = saved.state.artifact_ref.ref
    record = store.artifact(artifact_ref)
    assert record is not None and record.descriptor.j1_exchange is not None
    assert tuple(part.role for part in record.descriptor.retained_parts) == ("pair_counts",)
    receipt = record.descriptor.retained_parts[0].storage_receipt
    assert isinstance(receipt, LocalReceipt)
    with (case.root / receipt.project_relative_path / "data.parquet").open("ab") as output:
        output.write(b"corrupt")
    with pytest.raises(MaterializationError):
        load_j1_artifact(
            SessionStore.open_existing(case.root),
            "session",
            artifact_ref,
            association,
            input_binding=record.descriptor.j1_exchange.input_binding,
        )
