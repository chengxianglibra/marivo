"""Independent v7 publication, corruption and original-transaction counterexamples."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace

import ibis
import pyarrow as pa
import pytest

from marivo.analysis.compiler.graph_lowering import (
    CellColumns,
    CoordinateColumn,
    RelationLayout,
    SourceBinding,
)
from marivo.analysis.compiler.graph_plan import RouteChoice
from marivo.analysis.core.graph import Edge, SourceDefinition, SourceLeaf, method_node
from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    DomainSignature,
    ObservedQuantity,
    Signature,
)
from marivo.analysis.core.predicates import ValuePredicate
from marivo.analysis.core.rules import PartsTransport, RowState
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import canonical_json
from marivo.analysis.materialization.errors import IntegrityError, RecoveryPendingError
from marivo.analysis.materialization.execution_key import SourceKeyBinding
from marivo.analysis.materialization.graph_protocol import (
    DESCRIPTOR,
    NODE,
    SourceRunInput,
    decode,
    digest,
    encode,
    thaw_graph,
)
from marivo.analysis.materialization.graph_storage import read_result
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.methods.physical import NoTime, ScalarType, SourceShape
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


@pytest.fixture
def case(tmp_path):
    store = SessionStore._graph_store(tmp_path)
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


def test_source_roundtrip_new_identity_and_fixed_exact_hit(case):
    first = _execute(case)
    runtime, root, _, _, opens, backend = case
    assert read_result(runtime.store.project_root, first.descriptor).primary[
        "value"
    ].to_pylist() == [3]
    assert thaw_graph(encode(root, NODE)).fingerprint == root.fingerprint
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
    result = runtime._execute_graph(fixed, (RouteChoice(fixed.identity, "artifact_python"),))
    assert read_result(runtime.store.project_root, result.descriptor).primary[
        "value"
    ].to_pylist() == [4]
    before = _counts(runtime.store)
    assert (
        runtime._execute_graph(fixed, (RouteChoice(fixed.identity, "artifact_python"),)) == result
    )
    assert _counts(runtime.store) == before == (4, 4, 4, 0)
    assert opens == ["open", "open", "open"]


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
    with pytest.raises(RuntimeError, match=point):
        _execute(case)
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


@pytest.mark.parametrize("point", ["graph_receipts_verified", "before_commit"])
def test_changed_output_after_validation_cannot_commit(case, point):
    runtime = case[0]
    output = None

    def change_file(event):
        nonlocal output
        if event == "graph_files_published":
            owned = runtime.store.resources(runtime.session_ref)
            final = next(item for item in owned if "/artifacts/" in item.safe_locator)
            output = runtime.store.project_root / final.safe_locator / "primary" / "data.parquet"
        if event == point:
            assert output is not None
            with output.open("ab") as stream:
                stream.write(b"changed after verification")

    runtime._hook = change_file
    with pytest.raises(IntegrityError, match="file sizes"):
        _execute(case)
    assert _counts(runtime.store) == (1, 0, 1, 0)
    assert output is not None and not output.exists()


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
    with pytest.raises(RecoveryPendingError):
        _execute(case)
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
        lambda p: p.update(schema="marivo.dataset_artifact_descriptor/v2"),
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


def test_old_project_untouched_and_legacy_writer_cannot_write_v7(tmp_path):
    old = SessionStore(tmp_path)
    before = old.db_path.read_bytes()
    with pytest.raises(IntegrityError):
        SessionStore._graph_store(tmp_path)
    assert old.db_path.read_bytes() == before
    assert not (old.layout.generation_dir.parent / "v7").exists()
    new = SessionStore._graph_store(tmp_path / "fresh")
    with pytest.raises(IntegrityError):
        new.run("missing")


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
    broken = replace(record.descriptor, completed_checks=())
    from marivo.analysis.materialization.graph_protocol import validate_descriptor

    with pytest.raises(IntegrityError):
        validate_descriptor(broken)


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
    frozen = thaw_graph(encode(root, NODE))
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


def test_snapshot_receipt_and_state_binding_tampering_rejects(case):
    from marivo.analysis.materialization.graph_protocol import validate_descriptor

    record = _execute(case)
    for descriptor in (
        replace(record.descriptor, continuation_snapshot_digest="0" * 64),
        replace(record.descriptor, parts=()),
        replace(
            record.descriptor,
            primary_receipt=replace(record.descriptor.primary_receipt, input_binding="foreign"),
        ),
        replace(record.descriptor, method_bindings=()),
    ):
        with pytest.raises(IntegrityError):
            validate_descriptor(descriptor)


def _worker(store, reference, mode, point=""):
    import subprocess
    import sys

    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tests.graph_publication_runtime_worker",
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


def test_inconsistent_live_method_part_never_publishes(case, monkeypatch):
    import marivo.analysis.materialization.graph_publication as publication

    original = publication.execute_source_graph

    def inconsistent(*args):
        result = original(*args)
        part = result.parts[0]
        return replace(result, parts=(replace(part, table=pa.table({"row_state__count": [999]})),))

    monkeypatch.setattr(publication, "execute_source_graph", inconsistent)
    with pytest.raises(Exception, match="disagree"):
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
    with pytest.raises(RecoveryPendingError):
        _execute(case)
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


def test_v7_never_calls_old_descriptor_or_scenario_codecs(case, monkeypatch):
    from marivo.analysis.materialization import contracts, dsl_public_snapshot

    def forbidden(*args, **kwargs):
        raise AssertionError("legacy codec forbidden")

    monkeypatch.setattr(contracts, "encode_descriptor", forbidden)
    monkeypatch.setattr(contracts, "decode_descriptor", forbidden)
    monkeypatch.setattr(dsl_public_snapshot, "decode_public_node", forbidden)
    record = _capture(case)
    root = _fixed(record)
    result = case[0]._execute_graph(root, (RouteChoice(root.identity, "artifact_python"),))
    assert read_result(case[0].store.project_root, result.descriptor).primary[
        "value"
    ].to_pylist() == [3]


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
    with pytest.raises(RecoveryPendingError):
        _execute(case)
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


def test_independent_captures_reject_before_artifact_read_or_run(case, monkeypatch):
    import marivo.analysis.materialization.graph_publication as publication

    first, second = _capture(case), _capture(case)
    root = _fixed_pair(first, second)
    monkeypatch.setattr(
        publication,
        "_read_artifact",
        lambda *args: (_ for _ in ()).throw(AssertionError("Artifact read forbidden")),
    )
    with pytest.raises(IntegrityError, match="independent captures"):
        case[0]._execute_graph(root, (RouteChoice(root.identity, "artifact_python"),))
    assert _counts(case[0].store) == (2, 2, 2, 0)


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


@pytest.mark.parametrize("target", ["primary", "part"])
def test_valid_parquet_with_changed_bytes_cannot_hit(case, target):
    import pyarrow.parquet as pq

    saved = _capture(case)
    runtime = case[0]
    root = _fixed(saved)
    output = runtime._execute_graph(root, (RouteChoice(root.identity, "artifact_python"),))
    receipt = (
        saved.descriptor.primary_receipt if target == "primary" else output.descriptor.parts[0]
    )
    data = runtime.store.project_root / receipt.local.project_relative_path / "data.parquet"
    table = pq.read_table(data)
    name = "value" if target == "primary" else "row_state__count"
    values = table[name].to_pylist()
    values[0] += 1
    index = table.schema.get_field_index(name)
    table = table.set_column(index, table.schema.field(index), pa.array(values, type=pa.int64()))
    size = data.stat().st_size
    pq.write_table(table, data, write_page_checksum=True)
    assert data.stat().st_size == size
    before = _counts(runtime.store)
    with pytest.raises(IntegrityError, match="rows or bytes"):
        runtime._execute_graph(root, (RouteChoice(root.identity, "artifact_python"),))
    assert _counts(runtime.store) == before


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
    with pytest.raises(MaterializationError, match="local Parquet write failed"):
        _execute(case)
    assert _counts(case[0].store) == (1, 0, 1, 0)
