"""Independent v7 publication, corruption and original-transaction counterexamples."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import ibis
import pyarrow as pa
import pytest

from marivo.analysis.compiler.graph_lowering import (
    CellColumns,
    CoordinateColumn,
    LoweredPlan,
    RelationLayout,
    SourceBinding,
)
from marivo.analysis.compiler.graph_plan import RouteChoice
from marivo.analysis.core.graph import Edge, FixedLeaf, SourceDefinition, SourceLeaf, method_node
from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    DomainSignature,
    ObservedQuantity,
    Signature,
)
from marivo.analysis.core.predicates import ValuePredicate
from marivo.analysis.core.rules import AssociationScore, CellDerive, PartsTransport, RowState
from marivo.analysis.materialization import graph_local_execution, graph_publication
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import canonical_json
from marivo.analysis.materialization.errors import (
    IntegrityError,
    MaterializationError,
    RecoveryPendingError,
)
from marivo.analysis.materialization.execution_key import SourceKeyBinding
from marivo.analysis.materialization.graph_exchange import ExchangeContract, from_arrow
from marivo.analysis.materialization.graph_protocol import (
    DESCRIPTOR,
    SourceRunInput,
    decode,
    digest,
    encode,
    fixed_signature,
    freeze_graph,
    thaw_graph,
)
from marivo.analysis.materialization.graph_storage import read_result
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.methods.physical import FixedShape, NoTime, ScalarType, SourceShape
from marivo.analysis.methods.semantics import MethodKey
from marivo.analysis.refs import ArtifactRef
from marivo.datasource.adapters import SourceSession, provider_for
from marivo.datasource.ir import AiContextIR, DatasourceIR, DatasourceSourceLocation, TableSourceIR
from marivo.refs import ref
from tests.shared_fixtures import graph_count_continuation as _fixed


def _root(session):
    binding = Binding(session, "inventory", "products", "all")
    entity = ref.entity("inventory.product")
    coordinate = Coordinate(entity, "id", "identity")
    domain = DomainSignature(binding, "entity", (coordinate,), (coordinate,), "products")
    quantity = ObservedQuantity(
        "stock",
        ref.metric("inventory.stock"),
        "stock-v1",
        "units",
        "all",
        "products",
        "strict",
        "sum@v1",
    )
    leaf = SourceLeaf(
        SourceDefinition(
            quantity.metric_ref,
            "stock-v1",
            ref.datasource("db"),
            SourceShape("duckdb", "table", "native", NoTime()),
        ),
        Signature(domain, quantity),
        ScalarType("int64"),
    )
    return leaf, _count(leaf)


def _count(leaf):
    domain = DomainSignature(leaf.signature.domain.binding, "singleton", (), (), "all-products")
    return method_node(
        (Edge("quantity", leaf),),
        RowState("count", domain, "current-count", "count_all"),
        value_type=ScalarType("int64"),
    )


def test_optional_singleton_exchange_rejects_multiple_rows() -> None:
    leaf, _ = _root("test-session")
    quantity = leaf.signature.quantity
    assert quantity is not None
    singleton = DomainSignature(leaf.signature.domain.binding, "singleton", (), (), "selected")
    table = pa.table(
        {
            "value": pa.array([1, 2], type=pa.int64()),
            "cell_tag": ["defined", "defined"],
            "cell_reason": pa.array([None, None], type=pa.string()),
        }
    )
    contract = ExchangeContract(
        Signature(singleton, quantity),
        MethodKey("parts_transport"),
        "exact-binding",
        table.schema,
        (),
        allow_empty_singleton=True,
    )
    with pytest.raises(MaterializationError, match="singleton result has an invalid row count"):
        from_arrow(table, contract)


@pytest.fixture
def case(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session("graph")
    runtime = DatasetRuntime(store, session.session_ref)
    leaf, root = _root(session.session_ref)
    backend = ibis.duckdb.connect(tmp_path / "source.duckdb")
    backend.create_table(
        "facts",
        pa.table(
            {
                "id": [9007199254740993, 2, 3],
                "amount": [4, 7, 9],
                "tag": ["defined"] * 3,
                "reason": pa.array([None] * 3, type=pa.string()),
            }
        ),
    )
    datasource = DatasourceIR(
        "db", "db", "duckdb", {}, {}, AiContextIR(), "db", DatasourceSourceLocation("source.py", 1)
    )
    opens = []

    @contextmanager
    def source():
        opens.append("open")
        with SourceSession(
            provider_for("duckdb"), datasource, ibis.duckdb.connect(tmp_path / "source.duckdb")
        ) as selected:
            bound = selected.bind(TableSourceIR("facts"), source_identity=leaf.identity)
            layout = RelationLayout(
                (CoordinateColumn(leaf.signature.domain.instance_key[0], "id"),),
                CellColumns("amount", "tag", "reason"),
            )
            yield selected, (SourceBinding(leaf, bound, layout),)

    key = SourceKeyBinding(
        leaf,
        leaf.definition.shape,
        leaf.definition.fingerprint,
        digest(canonical_json(TableSourceIR("facts").to_dict())),
    )
    yield runtime, root, key, source, opens, backend
    backend.disconnect()


def _execute(case):
    runtime, root, key, source, _, _ = case
    return runtime._execute_graph(
        root, (RouteChoice(root.identity, "ibis"),), source_bindings=(key,), source_factory=source
    )


def _capture(case):
    leaf = case[2].leaf
    root = method_node(
        (Edge("quantity", leaf),),
        PartsTransport(
            "where",
            leaf.signature.domain,
            (),
            True,
            (ValuePredicate(leaf.signature.domain.binding, "gt", 0, "drop"),),
        ),
        value_type=leaf.value_type,
    )
    return case[0]._execute_graph(
        root,
        (RouteChoice(root.identity, "ibis"),),
        source_bindings=(case[2],),
        source_factory=case[3],
    )


def _counts(store):
    with sqlite3.connect(store.db_path) as conn:
        return tuple(
            conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "analysis_action_runs",
                "dataset_artifacts",
                "analysis_action_run_terminals",
                "action_resource_journal",
            )
        )


@pytest.mark.runtime
def test_fixed_difference_uses_ordered_exact_endpoints_without_source(case):
    first = _capture(case)
    case[5].create_table(
        "facts",
        pa.table(
            {
                "id": [9007199254740993, 2, 3],
                "amount": [1, 3, 8],
                "tag": ["defined"] * 3,
                "reason": pa.array([None] * 3, type=pa.string()),
            }
        ),
        overwrite=True,
    )
    second = _capture(case)
    a = _fixed(first).inputs[0].node
    b = _fixed(second).inputs[0].node
    assert a.signature.quantity is not None

    def difference(current, baseline):
        return method_node(
            (Edge("current", current), Edge("baseline", baseline)),
            CellDerive(
                "difference",
                "fixed-stock-difference",
                "strict",
                a.signature.quantity.unit,
                a.signature.quantity.time_scope,
                "source.exact_pairing@v1",
                "source.finite_numeric@v1",
            ),
            value_type=ScalarType("int64"),
        )

    before_opens = len(case[4])
    forward = difference(a, b)
    saved = case[0]._execute_graph(forward, (RouteChoice(forward.identity, "artifact_python"),))
    actual = read_result(case[0].store.project_root, saved.descriptor)
    assert actual.primary["value"].to_pylist() == [3, 4, 1]
    assert actual.parts[0].table["current_endpoint__value"].to_pylist() == [4, 7, 9]
    assert actual.parts[1].table["baseline_endpoint__value"].to_pylist() == [1, 3, 8]
    reverse = difference(b, a)
    reversed_saved = case[0]._execute_graph(
        reverse, (RouteChoice(reverse.identity, "artifact_python"),)
    )
    assert read_result(case[0].store.project_root, reversed_saved.descriptor).primary[
        "value"
    ].to_pylist() == [-3, -4, -1]
    assert saved.execution_key_digest != reversed_saved.execution_key_digest
    assert len(case[4]) == before_opens
    source_path = case[0].store.project_root / "source.duckdb"
    offline_path = source_path.with_suffix(".offline")
    source_path.rename(offline_path)
    try:
        process = subprocess.run(
            [
                sys.executable,
                "-c",
                """\
import sys
from dataclasses import replace
from marivo.analysis.compiler.graph_plan import RouteChoice
from marivo.analysis.core.graph import method_node
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.graph_protocol import thaw_graph
from marivo.analysis.materialization.graph_storage import read_result
from marivo.analysis.materialization.store import SessionStore

store = SessionStore(sys.argv[1], existing_only=True)
runtime = DatasetRuntime(store, sys.argv[2])
prior = thaw_graph(sys.argv[3])
root = method_node(
    prior.inputs,
    replace(prior.parameters, definition_id="cold-fixed-difference"),
    value_type=prior.value_type,
)
saved = runtime._execute_graph(root, (RouteChoice(root.identity, "artifact_python"),))
assert read_result(store.project_root, saved.descriptor).primary["value"].to_pylist() == [3, 4, 1]
assert saved.producing_run_ref != sys.argv[4]
""",
                str(case[0].store.project_root),
                case[0].session_ref,
                freeze_graph(forward),
                saved.producing_run_ref,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        offline_path.rename(source_path)
    assert process.returncode == 0, process.stderr


def test_source_roundtrip_new_identity_and_fixed_exact_hit(case, monkeypatch: pytest.MonkeyPatch):
    first = _execute(case)
    runtime, root, _, _, opens, backend = case
    assert read_result(runtime.store.project_root, first.descriptor).primary[
        "value"
    ].to_pylist() == [3]
    assert thaw_graph(freeze_graph(root)).fingerprint == root.fingerprint
    assert decode(encode(first.descriptor, DESCRIPTOR), DESCRIPTOR) == first.descriptor
    backend.insert(
        "facts",
        pa.table(
            {
                "id": [4],
                "amount": [10],
                "tag": ["defined"],
                "reason": pa.array([None], type=pa.string()),
            }
        ),
    )
    second = _execute(case)
    assert first.artifact_ref != second.artifact_ref
    assert first.producing_run_ref != second.producing_run_ref
    assert read_result(runtime.store.project_root, second.descriptor).primary[
        "value"
    ].to_pylist() == [4]
    fixed = _fixed(_capture(case))
    schedule_calls: list[LoweredPlan] = []
    validate = graph_local_execution.validate_fixed_schedule

    def validate_once(lowered: LoweredPlan) -> None:
        schedule_calls.append(lowered)
        validate(lowered)

    monkeypatch.setattr(graph_publication, "validate_fixed_schedule", validate_once)
    monkeypatch.setattr(graph_local_execution, "validate_fixed_schedule", validate_once)
    result = runtime._execute_graph(fixed, (RouteChoice(fixed.identity, "artifact_python"),))
    assert len(schedule_calls) == 1
    assert read_result(runtime.store.project_root, result.descriptor).primary[
        "value"
    ].to_pylist() == [4]
    before = _counts(runtime.store)
    assert (
        runtime._execute_graph(fixed, (RouteChoice(fixed.identity, "artifact_python"),)) == result
    )
    assert len(schedule_calls) == 2
    assert _counts(runtime.store) == before == (4, 4, 4, 0)
    assert opens == ["open", "open", "open"]


@pytest.mark.runtime
def test_fixed_spearman_accepts_independent_captures_of_one_frozen_membership(case):
    first, second = _capture(case), _capture(case)
    assert first.artifact_ref != second.artifact_ref
    left, right = (
        FixedLeaf(
            ArtifactRef(saved.artifact_ref),
            saved.descriptor.definition_fingerprint,
            saved.descriptor.signature,
            ScalarType("int64"),
            FixedShape(NoTime()),
        )
        for saved in (first, second)
    )
    binding = left.signature.domain.binding
    root = method_node(
        (Edge("quantity", left), Edge("quantity", right)),
        AssociationScore(
            DomainSignature(binding, "singleton", (), (), "all-products"),
            "captured-association",
            "source.exact_pairing@v1",
            "source.finite_numeric@v1",
        ),
        value_type=ScalarType("float64"),
    )
    before = _counts(case[0].store)
    saved = case[0]._execute_graph(root, (RouteChoice(root.identity, "artifact_python"),))
    actual = read_result(case[0].store.project_root, saved.descriptor)
    assert actual.primary["value"].to_pylist() == [1.0]
    assert _counts(case[0].store)[0] == before[0] + 1
    assert case[0]._execute_graph(root, (RouteChoice(root.identity, "artifact_python"),)) == saved


@pytest.mark.runtime
def test_fixed_transport_filters_exact_saved_rows_without_source_open(case):
    captured = _capture(case)
    leaf = _fixed(captured).inputs[0].node
    assert isinstance(leaf, FixedLeaf)
    root = method_node(
        (Edge("quantity", leaf),),
        PartsTransport(
            "where",
            leaf.signature.domain,
            (),
            True,
            (ValuePredicate(leaf.signature.domain.binding, "gt", 5, "drop"),),
        ),
        value_type=ScalarType("int64"),
    )
    before_opens = len(case[4])
    saved = case[0]._execute_graph(root, (RouteChoice(root.identity, "artifact_python"),))
    actual = read_result(case[0].store.project_root, saved.descriptor)
    assert actual.primary["value"].to_pylist() == [7, 9]
    assert len(case[4]) == before_opens


@pytest.mark.runtime
@pytest.mark.parametrize(
    ("method", "value_type", "expected"),
    [
        ("count", "int64", 2),
        ("count_defined", "int64", 2),
        ("sum", "int64", 16),
        ("mean", "float64", 8.0),
    ],
)
def test_fixed_filter_then_row_method_uses_one_graph_run(case, method, value_type, expected):
    captured = _capture(case)
    leaf = _fixed(captured).inputs[0].node
    assert isinstance(leaf, FixedLeaf)
    filtered = method_node(
        (Edge("quantity", leaf),),
        PartsTransport(
            "where",
            leaf.signature.domain,
            (),
            True,
            (ValuePredicate(leaf.signature.domain.binding, "gt", 5, "drop"),),
        ),
        value_type=ScalarType("int64"),
    )
    count_domain = DomainSignature(
        leaf.signature.domain.binding, "singleton", (), (), "selected-stock"
    )
    root = method_node(
        (Edge("quantity", filtered),),
        RowState(
            method,
            count_domain,
            "selected-stock-" + method,
            {"count": "count_all", "count_defined": "defined_only"}.get(method, "strict"),
            numeric_check_id=(
                "source.cell_policy@v1" if method == "count_defined" else "source.finite_numeric@v1"
            ),
        ),
        value_type=ScalarType(value_type),
    )
    before_runs = _counts(case[0].store)[0]
    before_opens = len(case[4])
    saved = case[0]._execute_graph(
        root,
        (
            RouteChoice(filtered.identity, "artifact_python"),
            RouteChoice(root.identity, "artifact_python"),
        ),
    )
    assert read_result(case[0].store.project_root, saved.descriptor).primary[
        "value"
    ].to_pylist() == [expected]
    assert _counts(case[0].store)[0] == before_runs + 1
    assert len(case[4]) == before_opens


@pytest.mark.runtime
def test_fixed_shared_difference_filter_sum_is_one_atomic_run(case, monkeypatch):
    from marivo.analysis.materialization import graph_local_execution

    captured = _capture(case)
    leaf = _fixed(captured).inputs[0].node
    assert isinstance(leaf, FixedLeaf)
    difference = method_node(
        (Edge("current", leaf), Edge("baseline", leaf)),
        CellDerive(
            "difference",
            "self-difference",
            "strict",
            "units",
            "all",
            "source.exact_pairing@v1",
            "source.finite_numeric@v1",
        ),
        value_type=ScalarType("int64"),
    )
    filtered = method_node(
        (Edge("quantity", difference),),
        PartsTransport(
            "where",
            difference.signature.domain,
            ("current_endpoint", "baseline_endpoint", "correspondence"),
            True,
            (ValuePredicate(difference.signature.domain.binding, "eq", 0, "reject"),),
        ),
        value_type=ScalarType("int64"),
    )
    root = method_node(
        (Edge("quantity", filtered),),
        RowState(
            "sum",
            DomainSignature(leaf.signature.domain.binding, "singleton", (), (), "all"),
            "selected-total",
            "strict",
            numeric_check_id="source.finite_numeric@v1",
        ),
        value_type=ScalarType("int64"),
    )
    calls = []
    original = graph_local_execution._difference_stage

    def tracked(*args, **kwargs):
        calls.append("difference")
        return original(*args, **kwargs)

    monkeypatch.setattr(graph_local_execution, "_difference_stage", tracked)
    before = _counts(case[0].store)
    before_opens = len(case[4])
    routes = tuple(
        RouteChoice(node.identity, "artifact_python") for node in (difference, filtered, root)
    )
    saved = case[0]._execute_graph(root, routes)
    actual = read_result(case[0].store.project_root, saved.descriptor)
    assert actual.primary["value"].to_pylist() == [0]
    assert calls == ["difference"]
    assert _counts(case[0].store) == (before[0] + 1, before[1] + 1, before[2] + 1, 0)
    assert len(case[4]) == before_opens
    assert case[0]._execute_graph(root, routes) == saved
    assert calls == ["difference"]


@pytest.mark.runtime
def test_fixed_spearman_coefficient_selection_keeps_verified_pair_counts(case):
    captured = _capture(case)
    association_root = _fixed_pair(captured, captured)
    association = case[0]._execute_graph(
        association_root, (RouteChoice(association_root.identity, "artifact_python"),)
    )
    fixed = FixedLeaf(
        ArtifactRef(association.artifact_ref),
        association.descriptor.definition_fingerprint,
        fixed_signature(association.descriptor),
        ScalarType("float64"),
        FixedShape(NoTime()),
    )
    selected_root = method_node(
        (Edge("quantity", fixed),),
        PartsTransport(
            "where",
            fixed.signature.domain,
            ("pair_counts",),
            True,
            (ValuePredicate(fixed.signature.domain.binding, "gt", 0, "drop"),),
        ),
        value_type=ScalarType("float64"),
    )
    selected = case[0]._execute_graph(
        selected_root, (RouteChoice(selected_root.identity, "artifact_python"),)
    )
    result = read_result(case[0].store.project_root, selected.descriptor)
    assert result.primary["value"].to_pylist() == [1.0]
    assert tuple(part.role for part in result.parts) == ("pair_counts",)
    rejected_root = method_node(
        (Edge("quantity", fixed),),
        PartsTransport(
            "where",
            fixed.signature.domain,
            ("pair_counts",),
            True,
            (ValuePredicate(fixed.signature.domain.binding, "lt", 0, "drop"),),
        ),
        value_type=ScalarType("float64"),
    )
    rejected = case[0]._execute_graph(
        rejected_root, (RouteChoice(rejected_root.identity, "artifact_python"),)
    )
    empty = read_result(case[0].store.project_root, rejected.descriptor)
    assert empty.primary.num_rows == 0
    assert empty.parts[0].table.num_rows == 0
    assert rejected.descriptor.row_set_contract.kind == "optional_singleton"
    source_path = case[0].store.project_root / "source.duckdb"
    offline_path = source_path.with_suffix(".offline")
    source_path.rename(offline_path)
    try:
        process = subprocess.run(
            [
                sys.executable,
                "-c",
                """\
import sys
from marivo.analysis.materialization import graph_store
from marivo.analysis.materialization.graph_storage import read_result
from marivo.analysis.materialization.store import SessionStore

store = SessionStore(sys.argv[1], existing_only=True)
with store._read() as conn:
    saved = graph_store.artifact(store, conn, sys.argv[2])
assert saved is not None
result = read_result(store.project_root, saved.descriptor)
assert result.primary.num_rows == 0
assert result.parts[0].table.num_rows == 0
""",
                str(case[0].store.project_root),
                rejected.artifact_ref,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        offline_path.rename(source_path)
    assert process.returncode == 0, process.stderr


@pytest.mark.runtime
def test_source_difference_retains_both_ordered_endpoint_parts(case):
    runtime, _, binding, _, _, backend = case
    leaf = binding.leaf
    assert leaf.signature.quantity is not None
    baseline_ref = ref.metric("inventory.baseline")
    baseline = SourceLeaf(
        SourceDefinition(
            baseline_ref,
            "baseline-v1",
            leaf.definition.datasource,
            leaf.definition.shape,
        ),
        Signature(
            leaf.signature.domain,
            replace(
                leaf.signature.quantity,
                definition_id="baseline-stock",
                metric_ref=baseline_ref,
                graph_fingerprint="baseline-v1",
            ),
        ),
        ScalarType("int64"),
    )
    backend.create_table(
        "baseline",
        pa.table(
            {
                "id": [9007199254740993, 2, 3],
                "amount": [1, 3, 8],
                "tag": ["defined"] * 3,
                "reason": pa.array([None] * 3, type=pa.string()),
            }
        ),
    )
    datasource = DatasourceIR(
        "db", "db", "duckdb", {}, {}, AiContextIR(), "db", DatasourceSourceLocation("source.py", 1)
    )

    @contextmanager
    def source():
        with SourceSession(
            provider_for("duckdb"),
            datasource,
            ibis.duckdb.connect(runtime.store.project_root / "source.duckdb"),
        ) as selected:
            first = selected.bind(TableSourceIR("facts"), source_identity=leaf.identity)
            second = selected.bind(TableSourceIR("baseline"), source_identity=baseline.identity)
            layout = RelationLayout(
                (CoordinateColumn(leaf.signature.domain.instance_key[0], "id"),),
                CellColumns("amount", "tag", "reason"),
            )
            yield (
                selected,
                (
                    SourceBinding(leaf, first, layout),
                    SourceBinding(baseline, second, layout),
                ),
            )

    baseline_key = SourceKeyBinding(
        baseline,
        baseline.definition.shape,
        baseline.definition.fingerprint,
        digest(canonical_json(TableSourceIR("baseline").to_dict())),
    )
    root = method_node(
        (Edge("current", leaf), Edge("baseline", baseline)),
        CellDerive(
            "difference",
            "same-stock-difference",
            "strict",
            leaf.signature.quantity.unit,
            leaf.signature.quantity.time_scope,
            "source.exact_pairing@v1",
            "source.finite_numeric@v1",
        ),
        value_type=ScalarType("int64"),
    )
    saved = runtime._execute_graph(
        root,
        (RouteChoice(root.identity, "ibis"),),
        source_bindings=(binding, baseline_key),
        source_factory=source,
    )
    actual = read_result(runtime.store.project_root, saved.descriptor)
    assert actual.primary["value"].to_pylist() == [3, 4, 1]
    assert tuple(part.role for part in actual.parts) == (
        "current_endpoint",
        "baseline_endpoint",
        "correspondence",
    )
    assert actual.parts[0].table["current_endpoint__value"].to_pylist() == [4, 7, 9]
    assert actual.parts[1].table["baseline_endpoint__value"].to_pylist() == [1, 3, 8]
    fixed = FixedLeaf(
        ArtifactRef(saved.artifact_ref),
        saved.descriptor.definition_fingerprint,
        fixed_signature(saved.descriptor),
        ScalarType("int64"),
        FixedShape(NoTime()),
    )
    continuation = method_node(
        (Edge("quantity", fixed),),
        PartsTransport(
            "where",
            fixed.signature.domain,
            ("current_endpoint", "baseline_endpoint", "correspondence"),
            True,
            (ValuePredicate(fixed.signature.domain.binding, "gt", 2, "drop"),),
        ),
        value_type=ScalarType("int64"),
    )
    continued = runtime._execute_graph(
        continuation, (RouteChoice(continuation.identity, "artifact_python"),)
    )
    retained = read_result(runtime.store.project_root, continued.descriptor)
    assert retained.primary["value"].to_pylist() == [3, 4]
    assert retained.parts[0].table["current_endpoint__value"].to_pylist() == [4, 7]
    assert retained.parts[1].table["baseline_endpoint__value"].to_pylist() == [1, 3]


@pytest.mark.parametrize(
    "point",
    [
        "graph_admitted",
        "graph_primary_written",
        "graph_part_written",
        "graph_files_published",
        "graph_receipts_verified",
        "insert_artifact",
        "insert_evidence",
        "insert_terminal",
        "before_commit",
    ],
)
def test_faults_have_no_success_and_clean_only_own_run(case, point):
    saved = _execute(case)
    runtime = case[0]

    def fail(event):
        if event == point:
            raise RuntimeError(point)

    runtime._hook = fail
    with pytest.raises(MaterializationError) as caught:
        _execute(case)
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert str(caught.value.__cause__) == point
    assert caught.value.run_ref is not None
    assert caught.value.run_ref == runtime.last_run_ref
    failed = runtime.get_run(caught.value.run_ref)
    assert failed.lifecycle == "failed"
    assert _counts(runtime.store) == (2, 1, 2, 0)
    assert read_result(runtime.store.project_root, saved.descriptor).primary[
        "value"
    ].to_pylist() == [3]


def test_lost_ack_returns_original_without_replay(case):
    def fail(event):
        if event == "after_commit":
            raise OSError("lost acknowledgement")

    case[0]._hook = fail
    saved = _execute(case)
    assert saved.producing_run_ref == case[0].last_run_ref
    assert case[4] == ["open"]
    assert _counts(case[0].store) == (1, 1, 1, 0)


def test_publication_requires_all_run_resources_discharge(case):
    from marivo.analysis.materialization.resources import backend_reservation

    runtime = case[0]

    def register_obligation(event):
        if event == "graph_receipts_verified":
            runtime.store.reserve(backend_reservation(runtime.last_run_ref, "source-read"))

    runtime._hook = register_obligation
    with pytest.raises(IntegrityError, match="resource obligations"):
        _execute(case)
    assert _counts(runtime.store) == (1, 0, 1, 0)
    runtime._hook = None
    saved = _execute(case)
    assert read_result(runtime.store.project_root, saved.descriptor).primary.num_rows == 1


def test_independent_semantic_dependency_digest_is_preserved(case):
    runtime, root, key, source, opens, _ = case
    selected = replace(key, semantic_dependency_digest=digest("semantic-revision-2"))
    saved = runtime._execute_graph(
        root,
        (RouteChoice(root.identity, "ibis"),),
        source_bindings=(selected,),
        source_factory=source,
    )
    run = runtime.store._graph_run(saved.producing_run_ref)
    assert run is not None and run.lifecycle == "succeeded"
    assert isinstance(run.dataset_input, SourceRunInput)
    assert run.dataset_input.ordered_source_bindings[0][1] == selected.semantic_dependency_digest
    assert opens == ["open"]


def test_parquet_close_failure_preserves_primary_read_error(case, monkeypatch):
    import marivo.analysis.materialization.graph_storage as storage

    saved = _execute(case)
    receipt = saved.descriptor.primary_receipt.local
    original = storage._open_payload

    class BrokenReader:
        def __init__(self, reader, *, fail_read):
            self.reader = reader
            self.schema_arrow = reader.schema_arrow
            self.fail_read = fail_read

        def iter_batches(self, **kwargs):
            if self.fail_read:
                raise pa.ArrowInvalid("original batch read failed")
            return self.reader.iter_batches(**kwargs)

        def close(self):
            self.reader.close()
            raise pa.ArrowInvalid("secondary close failed")

    for fail_read, message in (
        (True, "could not be read completely"),
        (False, "reader did not close successfully"),
    ):

        def opened(root, selected, *, fail_read=fail_read):
            reader, path = original(root, selected)
            return BrokenReader(reader, fail_read=fail_read), path

        monkeypatch.setattr(storage, "_open_payload", opened)
        with pytest.raises(IntegrityError, match=message):
            storage.read_table(case[0].store.project_root, receipt)


def test_unknown_commit_preserves_success_and_does_not_replay(case, monkeypatch):
    def fail(event):
        if event == "after_commit":
            monkeypatch.setattr(
                case[0].store, "_graph_run", lambda _: (_ for _ in ()).throw(OSError("unavailable"))
            )
            raise OSError("lost ack")

    case[0]._hook = fail
    with pytest.raises(RecoveryPendingError) as caught:
        _execute(case)
    assert caught.value.run_ref == case[0].last_run_ref
    assert isinstance(caught.value.__cause__, OSError)
    assert str(caught.value.__cause__) == "unavailable"
    assert case[4] == ["open"]
    assert _counts(case[0].store) == (1, 1, 1, 0)


@pytest.mark.parametrize("target", ["primary", "part"])
def test_corrupt_input_cannot_hit_or_admit(case, target):
    saved = _capture(case)
    runtime = case[0]
    fixed = _fixed(saved)
    output = runtime._execute_graph(fixed, (RouteChoice(fixed.identity, "artifact_python"),))
    receipt = (
        saved.descriptor.primary_receipt if target == "primary" else output.descriptor.parts[0]
    )
    path = runtime.store.project_root / receipt.local.project_relative_path / "data.parquet"
    path.unlink()
    before = _counts(runtime.store)
    with pytest.raises(Exception):
        runtime._execute_graph(fixed, (RouteChoice(fixed.identity, "artifact_python"),))
    assert _counts(runtime.store) == before


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(schema="marivo.dataset_artifact_descriptor/v3"),
        lambda p: p.update(extra=True),
        lambda p: p.pop("parts"),
        lambda p: p["method_state"].update(contract_version=2),
        lambda p: p["primary_receipt"].update(kind="part"),
    ],
)
def test_strict_descriptor_rejects_altered_fields(case, mutate):
    record = _execute(case)
    payload = json.loads(encode(record.descriptor, DESCRIPTOR))
    mutate(payload)
    with pytest.raises(IntegrityError):
        decode(canonical_json(payload), DESCRIPTOR)


@pytest.mark.parametrize("generation", range(1, 9))
def test_existing_generation_rejection_preserves_project_bytes(
    tmp_path: Path, generation: int
) -> None:
    old_path = tmp_path / f".marivo/analysis/generations/v{generation}/store.sqlite3"
    old_path.parent.mkdir(parents=True)
    with sqlite3.connect(old_path) as connection:
        connection.execute(f"PRAGMA user_version={generation}")
    before = old_path.read_bytes()
    files = tuple(sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")))
    with pytest.raises(IntegrityError):
        SessionStore.open_existing(tmp_path)
    assert tuple(sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))) == files
    current = SessionStore(tmp_path)
    assert current.layout.generation == 9
    assert old_path.read_bytes() == before


@pytest.mark.parametrize("method,expected", [("sum", 20), ("mean", 20 / 3), ("count_defined", 3)])
def test_method_state_and_completed_evidence_roundtrip(case, method, expected):
    leaf = case[2].leaf
    domain = DomainSignature(leaf.signature.domain.binding, "singleton", (), (), "all")
    root = method_node(
        (Edge("quantity", leaf),),
        RowState(
            method,
            domain,
            method,
            "defined_only" if method == "count_defined" else "strict",
            numeric_check_id="source.cell_policy@v1"
            if method == "count_defined"
            else "source.finite_numeric@v1",
        ),
        value_type=ScalarType("float64" if method == "mean" else "int64"),
    )
    record = case[0]._execute_graph(
        root,
        (RouteChoice(root.identity, "ibis"),),
        source_bindings=(case[2],),
        source_factory=case[3],
    )
    assert read_result(case[0].store.project_root, record.descriptor).primary[
        "value"
    ].to_pylist() == [expected]
    assert record.descriptor.completed_checks


@pytest.mark.parametrize("route", ["ibis", "ibis_python"])
def test_spearman_shared_node_preserves_frozen_identity_and_parts(case, route):
    from marivo.analysis.core.rules import AssociationScore

    leaf = case[2].leaf
    domain = DomainSignature(leaf.signature.domain.binding, "singleton", (), (), "all")
    root = method_node(
        (Edge("quantity", leaf), Edge("quantity", leaf)),
        AssociationScore(
            domain, "stock-pair", "source.exact_pairing@v1", "source.finite_numeric@v1"
        ),
        value_type=ScalarType("float64"),
    )
    record = case[0]._execute_graph(
        root,
        (RouteChoice(root.identity, route),),
        source_bindings=(case[2],),
        source_factory=case[3],
    )
    result = read_result(case[0].store.project_root, record.descriptor)
    assert result.primary["value"].to_pylist() == pytest.approx([1.0])
    assert result.parts[0].table["pair_counts__complete_pair_count"].to_pylist() == [3]
    frozen = thaw_graph(freeze_graph(root))
    assert frozen.inputs[0].node is frozen.inputs[1].node


def test_foreign_session_rejects_before_source_and_run(case):
    runtime = case[0]
    other = runtime.store.create_session("other")
    _, root = _root(other.session_ref)
    with pytest.raises(Exception, match="Session"):
        runtime._execute_graph(
            root,
            (RouteChoice(root.identity, "ibis"),),
            source_bindings=(case[2],),
            source_factory=case[3],
        )
    assert case[4] == []
    assert _counts(runtime.store) == (0, 0, 0, 0)


def _worker(store, reference, mode, point=""):
    import subprocess
    import sys

    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tests.analysis.materialization.publication_worker",
            str(store.project_root),
            reference,
            mode,
            point,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _finish(process, expected=0):
    output, error = process.communicate(timeout=30)
    assert process.returncode == expected, error
    return output.strip()


@pytest.mark.runtime
def test_cold_source_free_read_and_fixed_hit(case):
    saved = _capture(case)
    case[5].disconnect()
    (case[0].store.project_root / "source.duckdb").unlink()
    first = json.loads(_finish(_worker(case[0].store, saved.artifact_ref, "execute")))
    second = json.loads(_finish(_worker(case[0].store, saved.artifact_ref, "execute")))
    assert first == second
    assert first["value"] == [3]
    assert _counts(case[0].store) == (2, 2, 2, 0)


@pytest.mark.runtime
@pytest.mark.parametrize(
    "point",
    [
        "graph_primary_written",
        "graph_part_written",
        "graph_files_published",
        "before_commit",
        "after_commit",
    ],
)
def test_process_exit_coordinates_original_run_without_replay(case, point):
    saved = _capture(case)
    store = case[0].store
    _finish(_worker(store, saved.artifact_ref, "crash", point), expected=77)
    assert _finish(_worker(store, saved.artifact_ref, "reconcile")) == "reconciled"
    assert _counts(store) == ((2, 2, 2, 0) if point == "after_commit" else (2, 1, 2, 0))
    assert read_result(store.project_root, saved.descriptor).primary.num_rows == 3


@pytest.mark.runtime
def test_two_process_writers_share_one_success_and_release_lock(case):
    saved = _capture(case)
    store = case[0].store
    holder = _worker(store, saved.artifact_ref, "hold")
    try:
        assert holder.stdout.readline().strip() == "locked"
        assert _finish(_worker(store, saved.artifact_ref, "execute")) == "busy"
        holder.stdin.write("go\n")
        holder.stdin.flush()
        first = json.loads(_finish(holder))
        second = json.loads(_finish(_worker(store, saved.artifact_ref, "execute")))
        assert first == second
        assert _counts(store) == (2, 2, 2, 0)
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.communicate(timeout=10)


def test_equal_values_from_distinct_artifacts_do_not_hit(case):
    first, second = _capture(case), _capture(case)
    root_a, root_b = _fixed(first), _fixed(second)
    a = case[0]._execute_graph(root_a, (RouteChoice(root_a.identity, "artifact_python"),))
    b = case[0]._execute_graph(root_b, (RouteChoice(root_b.identity, "artifact_python"),))
    assert a.artifact_ref != b.artifact_ref
    assert a.execution_key_digest != b.execution_key_digest
    assert _counts(case[0].store) == (4, 4, 4, 0)


def test_missing_live_method_part_never_publishes(case, monkeypatch):
    import marivo.analysis.materialization.graph_publication as publication

    original = publication.execute_source_graph

    def inconsistent(*args):
        result = original(*args)
        return replace(result, parts=())

    monkeypatch.setattr(publication, "execute_source_graph", inconsistent)
    with pytest.raises(Exception, match="missing, reordered or extra method state part"):
        _execute(case)
    assert _counts(case[0].store) == (1, 0, 1, 0)


def test_empty_source_and_empty_fixed_input_publish_truthful_state(case):
    case[5].create_table(
        "facts",
        pa.Table.from_batches([], schema=case[5].table("facts").schema().to_pyarrow()),
        overwrite=True,
    )
    saved = _capture(case)
    assert read_result(case[0].store.project_root, saved.descriptor).primary.num_rows == 0
    root = _fixed(saved)
    result = case[0]._execute_graph(root, (RouteChoice(root.identity, "artifact_python"),))
    data = read_result(case[0].store.project_root, result.descriptor)
    assert data.primary["value"].to_pylist() == [0]
    assert data.parts[0].table["row_state__count"].to_pylist() == [0]


def test_unknown_precommit_preserves_original_resources(case, monkeypatch):
    runtime = case[0]
    original = runtime.store._graph_run

    def fail(event):
        if event == "before_commit":
            monkeypatch.setattr(
                runtime.store, "_graph_run", lambda _: (_ for _ in ()).throw(OSError("offline"))
            )
            raise OSError("uncertain")

    runtime._hook = fail
    with pytest.raises(RecoveryPendingError) as caught:
        _execute(case)
    assert caught.value.run_ref == runtime.last_run_ref
    assert isinstance(caught.value.__cause__, OSError)
    assert str(caught.value.__cause__) == "offline"
    assert _counts(runtime.store) == (1, 0, 0, 2)
    assert case[4] == ["open"]
    monkeypatch.setattr(runtime.store, "_graph_run", original)
    from marivo.analysis.materialization.reconciliation import reconcile_session
    from marivo.analysis.materialization.writer_guard import session_writer_guard

    with session_writer_guard(
        runtime.store.layout.lock_path(runtime.session_ref), session_ref=runtime.session_ref
    ):
        reconcile_session(runtime.store, runtime.session_ref, event=lambda _: None)
    assert _counts(runtime.store) == (1, 0, 1, 0)


def test_run_input_canonical_golden_vector():
    from marivo.analysis.materialization.graph_protocol import RUN_INPUT, SourceRunInput

    value = SourceRunInput(
        "marivo.analysis.run_input/v1",
        "source",
        "a" * 64,
        "b" * 64,
        (("metric-v1", "semantic-v1", "binding-v1"),),
    )
    expected = (
        '{"definition_fingerprint":"'
        + "a" * 64
        + '","kind":"source","ordered_source_bindings":[["metric-v1","semantic-v1","binding-v1"]],"plan_digest":"'
        + "b" * 64
        + '","schema":"marivo.analysis.run_input/v1"}'
    )
    assert encode(value, RUN_INPUT) == expected
    assert digest(expected) == "d7825bef0f2575368195e757487ae31ee11e98c29fbef14ba85c690b4c57aa20"
    assert decode(expected, RUN_INPUT) == value


def test_contradictory_commit_readback_is_unknown_and_preserves_files(case):
    runtime = case[0]

    def corrupt(event):
        if event == "after_commit":
            with sqlite3.connect(runtime.store.db_path) as conn:
                conn.execute(
                    "DELETE FROM analysis_action_run_terminals WHERE run_ref=?",
                    (runtime.last_run_ref,),
                )
            raise OSError("lost acknowledgement")

    runtime._hook = corrupt
    with pytest.raises(RecoveryPendingError) as caught:
        _execute(case)
    assert caught.value.run_ref == runtime.last_run_ref
    assert isinstance(caught.value.__cause__, IntegrityError)
    assert _counts(runtime.store) == (1, 1, 0, 0)
    assert case[4] == ["open"]
    assert list(
        runtime.store.layout.session_dir(runtime.session_ref).glob(
            "artifacts/*/primary/data.parquet"
        )
    )


def test_storage_rejects_symlink_before_writing(tmp_path):
    from marivo.analysis.materialization.graph_storage import write_table

    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (tmp_path / "link").symlink_to(foreign, target_is_directory=True)
    with pytest.raises(IntegrityError, match="symlink"):
        write_table(
            tmp_path, tmp_path / "link" / "staging", tmp_path / "output", pa.table({"id": [1]})
        )
    assert not list(foreign.iterdir())


def _fixed_pair(first, second):
    from marivo.analysis.core.rules import AssociationScore

    a = _fixed(first).inputs[0].node
    b = a if first.artifact_ref == second.artifact_ref else _fixed(second).inputs[0].node
    domain = DomainSignature(a.signature.domain.binding, "singleton", (), (), "paired")
    return method_node(
        (Edge("quantity", a), Edge("quantity", b)),
        AssociationScore(
            domain, "stock-pair", "source.exact_pairing@v1", "source.finite_numeric@v1"
        ),
        value_type=ScalarType("float64"),
    )


def test_shared_fixed_spearman_preserves_ordered_slots_and_reads_once(case, monkeypatch):
    import marivo.analysis.materialization.graph_storage as storage

    record = _capture(case)
    root = _fixed_pair(record, record)
    reads = []
    original = storage.read_table

    def counted(project, receipt):
        reads.append(receipt.project_relative_path)
        return original(project, receipt)

    monkeypatch.setattr(storage, "read_table", counted)
    result = case[0]._execute_graph(root, (RouteChoice(root.identity, "artifact_python"),))
    assert reads.count(record.descriptor.primary_receipt.local.project_relative_path) == 1
    assert read_result(case[0].store.project_root, result.descriptor).primary[
        "value"
    ].to_pylist() == pytest.approx([1.0])
    run = case[0].store._graph_run(result.producing_run_ref)
    assert run.input_artifact_refs == (record.artifact_ref, record.artifact_ref)


@pytest.mark.parametrize("failure", ["iteration", "close"])
def test_retained_reader_failure_is_structured_and_closes(case, monkeypatch, failure):
    import marivo.analysis.materialization.graph_storage as storage

    saved = _execute(case)
    original = storage._open_payload
    closed = []

    class Reader:
        def __init__(self, inner):
            self.inner = inner
            self.schema_arrow = inner.schema_arrow

        def iter_batches(self, **kwargs):
            if failure == "iteration":
                raise pa.ArrowInvalid("corrupt batch")
            return self.inner.iter_batches(**kwargs)

        def close(self):
            self.inner.close()
            closed.append(True)
            if failure == "close":
                raise pa.ArrowInvalid("failed close")

    def open_payload(root, receipt):
        reader, path = original(root, receipt)
        return Reader(reader), path

    monkeypatch.setattr(storage, "_open_payload", open_payload)
    with pytest.raises(IntegrityError, match="committed Parquet"):
        read_result(case[0].store.project_root, saved.descriptor)
    assert closed == [True]


def test_native_write_failure_is_structured_and_has_no_publication(case, monkeypatch):
    import marivo.analysis.materialization.graph_storage as storage
    from marivo.analysis.materialization.errors import MaterializationError

    def fail(*args, **kwargs):
        raise OSError("write denied")

    monkeypatch.setattr(storage.pq, "write_table", fail)
    with pytest.raises(MaterializationError, match="local Parquet write failed") as caught:
        _execute(case)
    assert caught.value.__cause__ is None
    assert isinstance(caught.value.__context__, OSError)
    assert str(caught.value.__context__) == "write denied"
    assert caught.value.run_ref is not None
    assert caught.value.run_ref == case[0].last_run_ref
    assert case[0].get_run(caught.value.run_ref).lifecycle == "failed"
    assert _counts(case[0].store) == (1, 0, 1, 0)


@pytest.mark.parametrize("target", ["input", "cached_output"])
def test_invalid_dag_cannot_hit_or_admit(case, target):
    import base64
    import zlib

    from marivo.analysis.materialization.graph_protocol import SNAPSHOT
    from marivo.analysis.materialization.graph_snapshot import PREFIX, document_json, graph_document

    saved = _capture(case)
    runtime = case[0]
    fixed = _fixed(saved)
    output = runtime._execute_graph(fixed, (RouteChoice(fixed.identity, "artifact_python"),))
    victim = saved if target == "input" else output
    snapshot = decode(victim.descriptor.continuation_snapshot, SNAPSHOT)
    document = replace(graph_document(thaw_graph(snapshot.root)), root="missing-root")
    root = (
        PREFIX + base64.b64encode(zlib.compress(document_json(document).encode(), level=9)).decode()
    )
    text = encode(replace(snapshot, root=root), SNAPSHOT)
    descriptor = replace(
        victim.descriptor, continuation_snapshot=text, continuation_snapshot_digest=digest(text)
    )
    payload = encode(descriptor, DESCRIPTOR)
    with runtime.store._write() as connection:
        connection.execute(
            "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
            (payload, victim.artifact_ref),
        )
        connection.execute(
            "UPDATE dataset_evidence SET evidence_digest=? WHERE artifact_ref=?",
            (digest(payload), victim.artifact_ref),
        )
    before, opens = _counts(runtime.store), list(case[4])
    with pytest.raises(IntegrityError, match="root definition reference"):
        runtime._execute_graph(fixed, (RouteChoice(fixed.identity, "artifact_python"),))
    assert _counts(runtime.store) == before
    assert case[4] == opens


@pytest.mark.parametrize(
    "budget", ["MAX_NODES", "MAX_DEPTH", "MAX_VALUE_RECORDS", "MAX_VALUE_REFERENCES"]
)
def test_dag_budget_rejects_before_source_open_or_run_admission(case, monkeypatch, budget):
    from marivo.analysis.materialization import graph_snapshot, graph_value_tables

    owner = graph_value_tables if budget.startswith("MAX_VALUE_") else graph_snapshot
    monkeypatch.setattr(owner, budget, 1)
    with pytest.raises(IntegrityError, match="budget"):
        _execute(case)
    assert case[4] == []
    assert _counts(case[0].store) == (0, 0, 0, 0)


def test_live_cycle_keeps_snapshot_error_before_source_or_run(case) -> None:
    root = case[1]
    object.__setattr__(root, "inputs", (Edge("quantity", root),))
    with pytest.raises(IntegrityError, match="cyclic definition reference"):
        _execute(case)
    assert case[4] == []
    assert _counts(case[0].store) == (0, 0, 0, 0)


def test_fixed_reads_without_producer_registry(case, monkeypatch, capsys):
    from marivo.analysis.materialization import graph_protocol, graph_store
    from marivo.analysis.materialization.graph_dataset import GraphDataset
    from marivo.analysis.materialization.graph_relation import Relation
    from marivo.analysis.methods.registry import MethodRegistry
    from marivo.analysis.public_dsl import wrap_materialized

    output = _execute(case)
    opens = list(case[4])

    def unavailable(*args, **kwargs):
        raise AssertionError("fixed reading accessed producer registry or planner")

    monkeypatch.setattr(MethodRegistry, "lookup", unavailable)
    monkeypatch.setattr(MethodRegistry, "select", unavailable)
    monkeypatch.setattr(graph_protocol, "make_plan", unavailable)
    descriptor = decode(encode(output.descriptor, DESCRIPTOR), DESCRIPTOR)
    assert read_result(case[0].store.project_root, descriptor).primary["value"].to_pylist() == [3]
    with case[0].store._read() as connection:
        recovered = graph_store.artifact(case[0].store, connection, output.artifact_ref)
    assert recovered is not None
    dataset = GraphDataset(case[0], recovered)
    result = wrap_materialized(Relation.restore(dataset), case[0], dataset)
    assert result.to_pandas()["value"].tolist() == [3]
    assert result.evidence_digest().finding_count == 0
    assert fixed_signature(descriptor).obligations == ()
    dataset.show()
    assert capsys.readouterr().out
    assert case[4] == opens


def test_validated_read_is_bound_and_rechecks_bytes(case, monkeypatch):
    from marivo.analysis.materialization import graph_protocol

    output = _execute(case)
    checked = graph_protocol.validate_metadata(output.descriptor)

    def unavailable(*args, **kwargs):
        raise AssertionError("metadata decoded again")

    monkeypatch.setattr(graph_protocol, "validate_metadata", unavailable)
    read_result(case[0].store.project_root, output.descriptor, _validated=checked)
    with pytest.raises(IntegrityError, match="different descriptor"):
        read_result(case[0].store.project_root, replace(output.descriptor), _validated=checked)
    receipt = output.descriptor.primary_receipt.local
    path = case[0].store.project_root / receipt.project_relative_path / "data.parquet"
    path.write_bytes(b"replacement")
    with pytest.raises(IntegrityError):
        read_result(case[0].store.project_root, output.descriptor, _validated=checked)


def test_old_descriptor_rejects_without_migration(case):

    output = _execute(case)
    old = encode(output.descriptor, DESCRIPTOR).replace(
        "artifact_descriptor/v6", "artifact_descriptor/v4"
    )
    with pytest.raises(IntegrityError, match="Re-execute the source analysis"):
        decode(old, DESCRIPTOR)


def test_materialized_show_without_original_implementation(case, monkeypatch, capsys):
    from marivo.analysis.materialization.graph_dataset import GraphDataset
    from marivo.analysis.materialization.graph_relation import Relation
    from marivo.analysis.methods.registry import MethodRegistry
    from marivo.analysis.public_dsl import wrap_materialized

    output = _execute(case)
    lookup = MethodRegistry.lookup

    def without_implementations(self, key):
        registration = lookup(self, key)
        return replace(registration, implementations=())

    monkeypatch.setattr(MethodRegistry, "lookup", without_implementations)
    dataset = GraphDataset(case[0], output)
    result = wrap_materialized(Relation.restore(dataset), case[0], dataset)
    result.show()
    assert capsys.readouterr().out
    assert result.to_pandas()["value"].tolist() == [3]
    from marivo.analysis.errors import AnalysisError

    successor = _fixed(output)
    with pytest.raises(AnalysisError):
        case[0]._execute_graph(successor, (RouteChoice(successor.identity, "artifact_python"),))


def test_cold_fixed_read_without_registry(case):
    output = _execute(case)
    code = """
import sys
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization import graph_store
from marivo.analysis.materialization.graph_storage import read_result
from marivo.analysis.methods.registry import MethodRegistry

def unavailable(*args, **kwargs):
    raise AssertionError("cold read reached the registry")
MethodRegistry.lookup = unavailable
MethodRegistry.select = unavailable
store = SessionStore(sys.argv[1])
with store._read() as connection:
    saved = graph_store.artifact(store, connection, sys.argv[2])
assert saved is not None
assert read_result(store.project_root, saved.descriptor,
                   _validated=saved.validated).primary["value"].to_pylist() == [3]
"""
    subprocess.run(
        [sys.executable, "-c", code, str(case[0].store.project_root), output.artifact_ref],
        check=True,
        capture_output=True,
        text=True,
    )


def test_committed_reads_do_not_repeat_production_validation(case, monkeypatch):
    from marivo.analysis.materialization import graph_exchange, graph_findings

    output = _execute(case)

    def forbidden(*args, **kwargs):
        raise AssertionError("committed read repeated production work")

    monkeypatch.setattr(graph_exchange, "collect", forbidden)
    monkeypatch.setattr(graph_findings, "extract", forbidden)
    result = read_result(case[0].store.project_root, output.descriptor)
    assert result.primary.num_rows == output.descriptor.primary_receipt.local.realized_row_count
    with case[0].store._read() as connection:
        findings, evidence = graph_findings.collection(
            case[0].store, connection, output.descriptor, output.artifact_ref
        )
    assert evidence.finding_count == len(findings)


@pytest.mark.parametrize(
    ("column", "value"),
    (
        ("finding_count", 1),
        ("extractor_contract_versions_payload", '["foreign@v1"]'),
        ("extractor_contract_versions_payload", "[1]"),
        ("extractor_contract_versions_payload", "invalid"),
    ),
)
def test_finding_collection_rejects_invalid_stored_envelope(case, column, value):
    from marivo.analysis.materialization import graph_findings, graph_store

    output = _execute(case)
    store = case[0].store
    with store._connection() as connection:
        connection.execute("BEGIN")
        try:
            connection.execute(
                f"UPDATE dataset_evidence SET {column}=? WHERE artifact_ref=?",
                (value, output.artifact_ref),
            )
            with pytest.raises(MaterializationError):
                graph_findings.collection(store, connection, output.descriptor, output.artifact_ref)
            with pytest.raises(MaterializationError):
                graph_store.artifact(store, connection, output.artifact_ref)
        finally:
            connection.rollback()


def test_committed_finding_content_digests_are_read_without_reaudit(case):
    from marivo.analysis.materialization import graph_findings

    output = _execute(case)
    store = case[0].store
    with store._connection() as connection:
        connection.execute("BEGIN")
        try:
            connection.execute(
                "UPDATE dataset_evidence SET evidence_digest=?, finding_set_digest=? "
                "WHERE artifact_ref=?",
                ("a" * 64, "b" * 64, output.artifact_ref),
            )
            findings, evidence = graph_findings.collection(
                store, connection, output.descriptor, output.artifact_ref
            )
            assert not findings
            assert evidence.evidence_digest == "a" * 64
            assert evidence.finding_set_digest == "b" * 64
        finally:
            connection.rollback()


def test_compact_binding_has_one_frozen_storage_owner(case):
    import pyarrow.parquet as pq

    from marivo.analysis.materialization.cell_arrow import binding, column
    from marivo.analysis.materialization.graph_protocol import schema_from

    record = _capture(case)
    descriptor = record.descriptor
    schema = schema_from(descriptor.realized_schema)
    assert binding(schema).cells
    assert "cell_tag" not in schema.names and "cell_reason" not in schema.names
    path = (
        case[0].store.project_root
        / descriptor.primary_receipt.local.project_relative_path
        / "data.parquet"
    )
    assert b"marivo.analysis.cell_table" not in (pq.read_schema(path).metadata or {})
    recovered = read_result(case[0].store.project_root, descriptor)
    assert recovered.primary.schema.equals(schema, check_metadata=True)
    assert column(recovered.primary, "cell_tag").to_pylist() == ["defined"] * 3
    assert descriptor.schema == "marivo.analysis.artifact_descriptor/v6"
    assert descriptor.method_state.schema == "marivo.analysis.method_state/v2"
    assert descriptor.method_state.contract_version == 4
    assert all(item.implementation_version == 7 for item in descriptor.method_bindings)


@pytest.mark.parametrize("target", ["descriptor", "method_state", "part_receipt"])
def test_obsolete_artifact_cannot_hit_cache_or_mutate_saved_files(case, target):
    saved = _execute(case) if target == "part_receipt" else _capture(case)
    fixed = _fixed(saved)
    case[0]._execute_graph(fixed, (RouteChoice(fixed.identity, "artifact_python"),))
    payload = encode(saved.descriptor, DESCRIPTOR).replace(
        "artifact_descriptor/v6"
        if target == "descriptor"
        else "receipt/v2"
        if target == "part_receipt"
        else "method_state/v2",
        "artifact_descriptor/v4"
        if target == "descriptor"
        else "receipt/v1"
        if target == "part_receipt"
        else "method_state/v1",
    )
    with case[0].store._write() as connection:
        connection.execute(
            "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
            (payload, saved.artifact_ref),
        )
        connection.execute(
            "UPDATE dataset_evidence SET evidence_digest=? WHERE artifact_ref=?",
            (digest(payload), saved.artifact_ref),
        )
    paths = tuple(case[0].store.project_root.glob(".marivo/analysis/**/data.parquet"))
    before = {path: path.read_bytes() for path in paths}
    counts, opens = _counts(case[0].store), list(case[4])
    with pytest.raises(IntegrityError, match="Re-execute the source analysis"):
        case[0]._execute_graph(fixed, (RouteChoice(fixed.identity, "artifact_python"),))
    assert _counts(case[0].store) == counts and case[4] == opens
    assert before and all(path.read_bytes() == data for path, data in before.items())
