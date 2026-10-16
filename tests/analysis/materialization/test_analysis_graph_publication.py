"""Independent v7 publication, corruption and original-transaction counterexamples."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

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
from marivo.analysis.core.rules import AssociationScore, PartsTransport, RowState
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
    Descriptor,
    SourceRunInput,
    ValidatedDescriptor,
    decode,
    digest,
    encode,
    freeze_graph,
    thaw_graph,
)
from marivo.analysis.materialization.graph_storage import read_result
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.methods.physical import NoTime, ScalarType, SourceShape
from marivo.analysis.methods.semantics import MethodKey
from marivo.datasource.adapters import SourceSession, provider_for
from marivo.datasource.ir import AiContextIR, DatasourceIR, DatasourceSourceLocation, TableSourceIR
from marivo.refs import ref


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


def test_faults_have_no_success_and_clean_only_own_run(case):
    saved = _execute(case)
    runtime = case[0]
    points = (
        "graph_admitted",
        "graph_files_published",
        "insert_terminal",
    )
    for point in points:
        before = _counts(runtime.store)

        def fail(event, *, selected=point):
            if event == selected:
                raise RuntimeError(selected)

        runtime._hook = fail
        with pytest.raises(MaterializationError) as caught:
            _execute(case)
        assert isinstance(caught.value.__cause__, RuntimeError)
        assert str(caught.value.__cause__) == point
        assert caught.value.run_ref is not None
        assert caught.value.run_ref == runtime.last_run_ref
        failed = runtime.get_run(caught.value.run_ref)
        assert failed.lifecycle == "failed"
        after = _counts(runtime.store)
        assert after == (before[0] + 1, before[1], before[2] + 1, before[3])
    runtime._hook = None
    assert read_result(runtime.store.project_root, saved.descriptor).primary["value"].tolist() == [
        3
    ]


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
    assert len(read_result(runtime.store.project_root, saved.descriptor).primary) == 1


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


def test_strict_descriptor_rejects_altered_fields(case):
    record = _execute(case)
    mutations = (
        lambda p: p.update(schema="marivo.dataset_artifact_descriptor/v3"),
        lambda p: p.update(extra=True),
        lambda p: p.pop("parts"),
        lambda p: p["method_state"].update(contract_version=2),
        lambda p: p["primary_receipt"].update(kind="part"),
    )
    original = encode(record.descriptor, DESCRIPTOR)
    for mutate in mutations:
        payload = json.loads(original)
        mutate(payload)
        with pytest.raises(IntegrityError):
            decode(canonical_json(payload), DESCRIPTOR)


@pytest.mark.parametrize("generation", (8,))
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
    with pytest.raises(IntegrityError):
        SessionStore(tmp_path)
    assert old_path.read_bytes() == before


def test_method_state_and_completed_evidence_roundtrip(case):
    method, expected = "sum", 20
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
    assert read_result(case[0].store.project_root, record.descriptor).primary["value"].tolist() == [
        expected
    ]
    assert record.descriptor.completed_checks


@pytest.mark.parametrize("route", ["ibis", "ibis_python"])
def test_spearman_shared_node_preserves_frozen_identity_and_parts(case, route):

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
    assert result.primary["value"].tolist() == pytest.approx([1.0])
    assert result.parts[0].table["pair_counts__complete_pair_count"].tolist() == [3]
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


def _finish(process, expected=0):
    output, error = process.communicate(timeout=30)
    assert process.returncode == expected, error
    return output.strip()


def test_missing_live_method_part_never_publishes(case, monkeypatch):
    import marivo.analysis.materialization.graph_publication as publication

    original = publication.execute_source_graph

    def inconsistent(*args):
        result = original(*args)
        return replace(result, parts=())

    monkeypatch.setattr(publication, "execute_source_graph", inconsistent)
    with pytest.raises(IntegrityError, match="required roles"):
        _execute(case)
    assert _counts(case[0].store) == (1, 0, 1, 0)


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


def test_storage_write_failure_removes_run_owned_staging(tmp_path, monkeypatch):
    import marivo.analysis.materialization.graph_storage as storage

    staging = tmp_path / "run" / "output" / "primary"

    def fail(*args, **kwargs):
        raise OSError("write denied")

    monkeypatch.setattr(storage.pq, "write_table", fail)
    with pytest.raises(MaterializationError, match="local Parquet write failed"):
        storage.write_table(
            tmp_path,
            staging,
            tmp_path / "artifact" / "primary",
            pa.table({"id": [1]}),
        )
    assert not staging.exists()


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
            self.metadata = inner.metadata

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

    if failure == "iteration":

        def corrupt_read(*args, **kwargs):
            raise pa.ArrowInvalid("corrupt pandas read")

        monkeypatch.setattr(storage.pd, "read_parquet", corrupt_read)
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


def test_dag_budget_rejects_before_source_open_or_run_admission(case, monkeypatch):
    from marivo.analysis.materialization import graph_snapshot

    monkeypatch.setattr(graph_snapshot, "MAX_NODES", 1)
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


def test_committed_reads_do_not_repeat_production_validation(case, monkeypatch):
    from marivo.analysis.materialization import graph_findings, graph_storage

    output = _execute(case)

    def forbidden(*args, **kwargs):
        raise AssertionError("committed read repeated production work")

    monkeypatch.setattr(graph_storage, "read_table", forbidden)
    monkeypatch.setattr(graph_findings, "extract", forbidden)
    result = read_result(case[0].store.project_root, output.descriptor)
    assert len(result.primary) == output.descriptor.primary_receipt.local.realized_row_count
    with case[0].store._read() as connection:
        findings, evidence = graph_findings.collection(
            case[0].store, connection, output.descriptor, output.artifact_ref
        )
    assert evidence.finding_count == len(findings)


def test_evidence_read_decodes_collection_once(case, monkeypatch):
    from marivo.analysis.errors import FindingNotFoundError
    from marivo.analysis.evidence import _dataset_types as t
    from marivo.analysis.materialization import graph_findings
    from marivo.analysis.materialization.graph_dataset import GraphDataset

    output = _execute(case)
    dataset = GraphDataset(case[0], output)
    collection = graph_findings.collection
    calls: list[sqlite3.Connection] = []

    def counted(
        store: SessionStore,
        connection: sqlite3.Connection,
        descriptor: Descriptor,
        artifact_ref: str,
        *,
        _validated: ValidatedDescriptor | None = None,
    ) -> tuple[tuple[t.Finding, ...], t.ArtifactDigest]:
        calls.append(connection)
        return collection(store, connection, descriptor, artifact_ref, _validated=_validated)

    monkeypatch.setattr(graph_findings, "collection", counted)
    assert dataset.evidence_digest().finding_count == 0
    assert len(calls) == 1
    calls.clear()
    page = dataset.findings()
    assert page.items == () and not page.has_more and page.next_cursor is None
    assert len(calls) == 1
    calls.clear()
    with pytest.raises(FindingNotFoundError):
        dataset.finding("missing-finding")
    assert len(calls) == 1


def test_missing_evidence_row_blocks_evidence_read(case):
    from marivo.analysis.materialization.graph_dataset import GraphDataset

    output = _execute(case)
    dataset = GraphDataset(case[0], output)
    with case[0].store._write() as connection:
        connection.execute(
            "DELETE FROM dataset_evidence WHERE artifact_ref=?", (output.artifact_ref,)
        )
    with pytest.raises(IntegrityError) as caught:
        dataset.evidence_digest()
    assert caught.value.received == "Artifact lacks atomic Evidence"


def test_finding_collection_rejects_invalid_stored_envelope(case):
    from marivo.analysis.materialization import graph_findings, graph_store

    output = _execute(case)
    store = case[0].store
    with store._connection() as connection:
        connection.execute("BEGIN")
        try:
            for column, value in (
                ("finding_count", 1),
                ("extractor_contract_versions_payload", '["foreign@v1"]'),
                ("extractor_contract_versions_payload", "[1]"),
                ("extractor_contract_versions_payload", "invalid"),
            ):
                connection.execute("SAVEPOINT invalid_evidence")
                connection.execute(
                    f"UPDATE dataset_evidence SET {column}=? WHERE artifact_ref=?",
                    (value, output.artifact_ref),
                )
                with pytest.raises(MaterializationError):
                    graph_findings.collection(
                        store, connection, output.descriptor, output.artifact_ref
                    )
                with pytest.raises(MaterializationError):
                    graph_store.artifact(store, connection, output.artifact_ref)
                connection.execute("ROLLBACK TO invalid_evidence")
                connection.execute("RELEASE invalid_evidence")
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
    from marivo.analysis.materialization.graph_exchange import to_arrow

    physical = to_arrow(recovered.primary, schema)
    assert physical.schema.equals(schema, check_metadata=True)
    assert column(physical, "cell_tag").to_pylist() == ["defined"] * 3
    assert descriptor.schema == "marivo.analysis.artifact_descriptor/v6"
    assert descriptor.method_state.schema == "marivo.analysis.method_state/v2"
    assert descriptor.method_state.contract_version == 4
    assert all(item.implementation_version == 7 for item in descriptor.method_bindings)
