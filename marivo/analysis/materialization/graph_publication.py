"""Private graph Run coordination and atomic v8 publication under DatasetRuntime."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING
from uuid import uuid4

import pyarrow as pa

from marivo import _execution_log
from marivo.analysis.compiler.graph_lowering import SourceBinding, lower
from marivo.analysis.compiler.graph_plan import RouteChoice
from marivo.analysis.core.graph import MethodNode, Node, capture_graph
from marivo.analysis.core.rules import (
    CellDerive,
    DeviationFit,
    DeviationRead,
    DisplayRank,
    DisplayTable,
    PartsTransport,
)
from marivo.analysis.errors import AnalysisError, AnalysisRepair
from marivo.analysis.materialization import graph_store
from marivo.analysis.materialization.cell_arrow import binding_scope
from marivo.analysis.materialization.contracts import (
    ResourceRecord,
    RunFailure,
    RunFailurePhase,
    canonical_json,
)
from marivo.analysis.materialization.errors import MaterializationError, RecoveryPendingError
from marivo.analysis.materialization.execution_key import (
    FixedKeyInput,
    FixedPartKey,
    SourceKeyBinding,
    _fixed_input_occurrences,
    graph_fixed_execution_key,
    graph_source_execution_key,
)
from marivo.analysis.materialization.graph_exchange import (
    VerifiedFixedInput,
    from_arrow,
)
from marivo.analysis.materialization.graph_execution import _prepare_captured_graph
from marivo.analysis.materialization.graph_local_execution import (
    execute_verified_fixed,
    validate_fixed_schedule,
)
from marivo.analysis.materialization.graph_protocol import (
    DESCRIPTOR,
    SNAPSHOT,
    STATE,
    Continuation,
    Descriptor,
    FixedInputBinding,
    FixedRunInput,
    MethodBinding,
    MethodState,
    PartReceipt,
    PrimaryReceipt,
    RowContract,
    RowSetContract,
    SourceRunInput,
    decode,
    digest,
    encode,
    evidence_identity,
    fixed_signature,
    invalid,
    plan_digest,
    receipt_digest,
    schema_text,
    semantic_versions,
)
from marivo.analysis.materialization.graph_snapshot import _encode_document, graph_document
from marivo.analysis.materialization.graph_source_execution import execute_source_graph
from marivo.analysis.materialization.graph_storage import read_result, write_table
from marivo.analysis.materialization.reconciliation import reconcile_session
from marivo.analysis.materialization.resources import (
    clickhouse_control_reservation,
    discharge_resources,
    mysql_control_reservation,
    reserve_output,
)
from marivo.analysis.materialization.storage import _fsync_directory
from marivo.analysis.materialization.store import SessionStore, _new_run_ref, _rows, _text
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.analysis.methods.physical import (
    DecimalType,
    DurationType,
    NoTime,
    Qualified,
    ScalarType,
    matches_arrow_scalar,
)
from marivo.analysis.methods.registry import REGISTRY
from marivo.datasource.adapters import SourceSession
from marivo.introspection.live.model import LiveHelpTarget

if TYPE_CHECKING:
    from marivo.analysis.materialization.admission import DatasetRuntime

SourceFactory = Callable[
    [], AbstractContextManager[tuple[SourceSession, tuple[SourceBinding, ...]]]
]


@dataclass(slots=True)
class _InvocationRun:
    """Admission identity for one invocation, independent of Runtime history."""

    run_ref: str | None = None


def _reconcile_graph(
    store: SessionStore, session: str, event: Callable[[str], None], *, run_ref: str | None = None
) -> None:
    """Read original transactions before touching any recorded resource."""
    event("reconciliation")
    if store.session(session) is None:
        raise invalid("reconciliation Session is absent")
    if run_ref is not None:
        selected = store._graph_run(run_ref)
        if selected is None or selected.session_ref != session or selected.lifecycle == "succeeded":
            raise invalid(
                "reconciliation requires an exact incomplete or failed Run of this Session"
            )
    with store._read() as conn:
        refs = _rows(
            conn,
            "SELECT a.run_ref FROM analysis_action_runs a LEFT JOIN analysis_action_run_terminals t USING(run_ref) WHERE a.session_ref=? AND (t.run_ref IS NULL OR EXISTS (SELECT 1 FROM action_resource_journal j WHERE j.run_ref=a.run_ref)) ORDER BY a.admitted_at,a.run_ref",
            (session,),
        )
        resources = store._resources(conn, session)
        runs = tuple(graph_store.run(store, conn, _text(row, "run_ref")) for row in refs)
        if sum(r is not None and r.lifecycle == "incomplete" for r in runs) > 1:
            raise invalid("multiple incomplete graph Runs")
        for run in runs:
            if run is None:
                raise invalid("missing graph Run")
            matches = _rows(
                conn,
                "SELECT artifact_ref FROM dataset_artifacts WHERE session_ref=? AND execution_key_digest=?",
                (session, run.execution_key_digest),
            )
            if run.lifecycle == "succeeded":
                if (
                    run.output_artifact_ref is None
                    or graph_store.artifact(store, conn, run.output_artifact_ref) is None
                ):
                    raise invalid("success has no exact committed Artifact")
            elif matches:
                raise invalid("non-success Run has a conflicting Artifact")
    for run in runs:
        assert run is not None
        if run_ref is not None and run.run_ref != run_ref:
            continue
        owned = tuple(r for r in resources if r.run_ref == run.run_ref)
        if run.lifecycle == "succeeded":
            if owned:
                raise invalid("success retains graph resource obligations")
            continue
        resolved = discharge_resources(store, owned)
        if run.lifecycle == "incomplete":
            store.fail(
                run.run_ref,
                RunFailure(
                    phase="process_lost",
                    kind="process_lost",
                    safe_message="The graph Run has no committed publication.",
                    safe_location="analysis.graph",
                    expected="one atomic committed graph output",
                    received="unfinished original Run",
                    repair=AnalysisRepair(
                        kind="inspect",
                        action="Inspect the original Run after guarded reconciliation; no computation was resumed.",
                        help_target=LiveHelpTarget(surface="analysis", canonical_id="runtime.runs"),
                    ),
                ),
                resolved_resources=resolved,
            )
        else:
            for resource in resolved:
                store.discharge(resource)


def _read_artifact(store: SessionStore, ref: str) -> graph_store.GraphArtifact:
    with store._read() as conn:
        result = graph_store.artifact(store, conn, ref)
    if result is None:
        raise invalid("exact Artifact reference is absent")
    return result


def _execute(
    runtime: DatasetRuntime,
    root: Node,
    routes: tuple[RouteChoice, ...],
    *,
    invocation: _InvocationRun,
    source_bindings: tuple[SourceKeyBinding, ...] = (),
    source_factory: SourceFactory | None = None,
    source_schemas: tuple[pa.Schema, ...] = (),
) -> graph_store.GraphArtifact:
    store, session, event = runtime.store, runtime.session_ref, runtime._event
    # Admit the complete bounded definition closure before source I/O or cache lookup.
    document = graph_document(root)
    captured = capture_graph(root)
    frozen_graph = _encode_document(document)
    prepared = _prepare_captured_graph(captured, session_ref=session, routes=routes)
    plan = prepared.admitted
    if not isinstance(root, MethodNode):
        raise invalid("publication requires a qualified method root")
    # Qualify the persistent envelope before any I/O or Run allocation.
    state_kind = REGISTRY.lookup(root.method).semantics.persistent_state_kind
    if state_kind is None:
        raise invalid("method has no qualified v8 state codec")
    source_only = plan.classification.kind == "source"
    if source_only:
        if source_factory is None:
            raise invalid("source invocation requires its selected R1 source factory")
        if source_schemas and len(source_schemas) != len(source_bindings):
            raise invalid("preflight schemas differ from ordered source bindings")
        graph_source_execution_key(plan, source_bindings, "run_preflight")
    elif source_factory is not None or source_bindings or source_schemas:
        raise invalid("fixed invocation cannot carry source resources")
    fixed_lowered = None if source_only else lower(plan, bindings=())
    if fixed_lowered is not None:
        validate_fixed_schedule(fixed_lowered)
    with session_writer_guard(store.layout.lock_path(session), session_ref=session):
        reconcile_session(store, session, event=event)
        fixed: dict[str, VerifiedFixedInput] = {}
        inputs: list[FixedKeyInput] = []
        if not source_only:
            for leaf in _fixed_input_occurrences(root):
                record = _read_artifact(store, leaf.artifact.ref)
                descriptor = record.descriptor
                if (
                    record.session_ref != session
                    or replace(
                        fixed_signature(descriptor, _validated=record.validated),
                        node_id=leaf.identity,
                        key_domain_id=leaf.artifact.ref,
                    )
                    != leaf.signature
                    or descriptor.definition_fingerprint != leaf.definition_fingerprint
                    or not isinstance(leaf.value_type, (ScalarType, DecimalType, DurationType))
                ):
                    raise invalid("fixed leaf differs from exact committed signature or definition")
                from marivo.analysis.materialization.graph_protocol import schema_from

                schema = schema_from(descriptor.realized_schema)
                if "value" in schema.names and not matches_arrow_scalar(
                    schema.field("value").type, leaf.value_type
                ):
                    raise invalid("fixed physical value type differs before receipt read")
                if leaf.identity not in fixed:
                    result = read_result(
                        store.project_root, descriptor, _validated=record.validated
                    )
                    result = replace(
                        result,
                        contract=replace(
                            result.contract,
                            signature=leaf.signature,
                            input_binding=record.artifact_ref,
                            _frozen=None,
                        ),
                    )
                    fixed[leaf.identity] = VerifiedFixedInput(
                        record.artifact_ref, descriptor.primary_receipt.local, result
                    )
                inputs.append(
                    FixedKeyInput(
                        leaf,
                        session,
                        record.producing_run_ref,
                        receipt_digest(descriptor.primary_receipt),
                        tuple(
                            FixedPartKey(
                                p.role, p.contract_id, p.contract_version, receipt_digest(p)
                            )
                            for p in descriptor.parts
                        ),
                        descriptor.method_state.input_binding,
                        descriptor.method_state.contract_id,
                        descriptor.method_state.contract_version,
                        descriptor.continuation_snapshot_digest,
                    )
                )
            key = graph_fixed_execution_key(plan, tuple(inputs))
            with store._read() as conn:
                hits = _rows(
                    conn,
                    "SELECT artifact_ref FROM dataset_artifacts WHERE session_ref=? AND execution_key_digest=?",
                    (session, key),
                )
            if hits:
                hit = _read_artifact(store, _text(hits[0], "artifact_ref"))
                read_result(store.project_root, hit.descriptor, _validated=hit.validated)
                _execution_log.annotate(cache_hit=True)
                _execution_log.emit(
                    "execution.reused",
                    artifact_id=hit.artifact_ref,
                    producing_run_id=hit.producing_run_ref,
                )
                return hit
        run_ref = _new_run_ref()
        if source_only:
            key = graph_source_execution_key(plan, source_bindings, run_ref)
        selected = (
            SourceRunInput(
                "marivo.analysis.run_input/v1",
                "source",
                root.fingerprint,
                plan_digest(plan),
                tuple(
                    (
                        b.leaf.definition.fingerprint,
                        b.semantic_dependency_digest,
                        b.selected_binding_fingerprint,
                    )
                    for b in source_bindings
                ),
            )
            if source_only
            else FixedRunInput(
                "marivo.analysis.run_input/v1",
                "fixed",
                root.fingerprint,
                plan_digest(plan),
                tuple(
                    FixedInputBinding(
                        v.session_ref,
                        v.leaf.artifact.ref,
                        v.producing_run_ref,
                        v.primary_receipt_digest,
                        v.ordered_parts,
                        v.input_binding,
                        v.method_state_contract_id,
                        v.method_state_version,
                        v.snapshot_digest,
                    )
                    for v in inputs
                ),
            )
        )
        graph_store.admit(store, session, key, selected, run_ref)
        invocation.run_ref = run_ref
        runtime.last_run_ref = run_ref
        _execution_log.annotate(run_id=run_ref)
        nonce = uuid4().hex
        artifact_ref = f"artifact_{nonce}"
        committing = False
        phase: RunFailurePhase = "stage_execution"
        control_resource: ResourceRecord | None = None
        source_owner: SourceSession | None = None
        try:
            event("graph_admitted")
            if source_only:
                assert source_factory is not None
                with source_factory() as (source, bindings):
                    # The opened adapter must correspond to the admitted metadata.
                    if tuple(b.leaf for b in bindings) != tuple(b.leaf for b in source_bindings):
                        raise invalid("opened source leaf order differs from admission")
                    if any(
                        supplied.selected_binding_fingerprint
                        != digest(canonical_json(bound.source.source.to_dict()))
                        for supplied, bound in zip(source_bindings, bindings, strict=True)
                    ):
                        raise invalid(
                            "opened physical source differs from admitted source metadata"
                        )
                    if source_schemas and any(
                        not schema.equals(bound.source.facts.schema, check_metadata=True)
                        for schema, bound in zip(source_schemas, bindings, strict=True)
                    ):
                        raise invalid("opened physical schema differs from selected preflight")
                    from marivo.analysis.core.rules import OccurrencePrepare, PreparedObservation
                    from marivo.datasource.domain_snapshot import capture

                    uses_preparation = any(
                        isinstance(node, MethodNode)
                        and (
                            isinstance(node.parameters, OccurrencePrepare)
                            or (
                                isinstance(node.parameters, PreparedObservation)
                                and node.inputs[0].node.identity != node.inputs[1].node.identity
                            )
                        )
                        for node in captured.nodes
                    )
                    from marivo.analysis.materialization.execute_deadline import (
                        check,
                        guard,
                        remaining,
                    )

                    source._checkpoint = check
                    source._seconds_remaining = remaining
                    source_owner = source
                    if source.provider.name == "mysql":
                        control_resource = mysql_control_reservation(
                            run_ref, source.datasource.name
                        )
                        store.reserve(control_resource)
                    elif source.provider.name == "clickhouse":
                        control_resource = clickhouse_control_reservation(
                            run_ref, source.datasource.name
                        )
                        store.reserve(control_resource)
                    source._prepare_interrupt()
                    from marivo.datasource.interrupts import owned_sigint

                    with (
                        owned_sigint(source._request_interrupt)
                        if source.provider.name in {"mysql", "clickhouse", "sqlite"}
                        else nullcontext(),
                        capture(source, checkpoint=check, guard=guard)
                        if uses_preparation
                        else guard(source._request_interrupt) as authority,
                    ):
                        if uses_preparation:
                            source.domain_authority = authority
                            if source.provider.name == "sqlite":
                                from sqlite3 import Connection

                                from marivo.analysis.materialization.temporal_sql import (
                                    _initialize_sqlite_functions,
                                )

                                connection: object = getattr(source._backend, "con", None)
                                if not isinstance(connection, Connection):
                                    raise invalid(
                                        "SQLite capture has no native execution connection"
                                    )
                                _initialize_sqlite_functions(connection)
                            rebound: list[SourceBinding] = []
                            for source_binding in bindings:
                                selected_source = source.binding_for(source_binding.leaf.identity)
                                entity_relation = source_binding.entity_relation
                                if entity_relation is not None:
                                    entity_relation = (
                                        entity_relation.op()
                                        .replace(
                                            {
                                                source_binding.source.relation.op(): selected_source.relation.op()
                                            }
                                        )
                                        .to_expr()
                                    )
                                rebound.append(
                                    replace(
                                        source_binding,
                                        source=selected_source,
                                        entity_relation=entity_relation,
                                    )
                                )
                            bindings = tuple(rebound)
                        lowered = lower(plan, bindings=bindings)
                        with _execution_log.stage("analysis.source") as summary:
                            result = execute_source_graph(prepared, lowered, source)
                            summary.update(
                                row_count=result.primary.num_rows, arrow_bytes=result.primary.nbytes
                            )
            else:
                assert fixed_lowered is not None
                values = tuple(fixed[leaf.identity] for leaf in _fixed_input_occurrences(root))
                with _execution_log.stage("analysis.local") as summary:
                    result = execute_verified_fixed(prepared, fixed_lowered, values)
                    summary.update(
                        row_count=result.primary.num_rows, arrow_bytes=result.primary.nbytes
                    )
            result = from_arrow(
                result.primary,
                result.contract,
                parts=result.parts,
                completed_checks=result.completed_checks,
                method_state=result.method_state,
                validate=False,
            )
            if control_resource is not None:
                if source_owner is None or not source_owner._cancel_control_released:
                    raise RecoveryPendingError(
                        expected=f"confirmed owned {source_owner.provider.name if source_owner is not None else 'reader'} control connection release",
                        received="control release is unconfirmed",
                        repair="Inspect the original Run and control resource obligation before retrying.",
                        stage="source_cleanup",
                        run_ref=run_ref,
                    )
                store.discharge(control_resource)
                control_resource = None
            if result.contract.signature != root.signature or result.contract.method != root.method:
                raise invalid("execution output differs from admitted root")
            if result.contract.pending_checks != plan.checks:
                raise invalid("execution dropped or replaced admitted checks")
            state = MethodState(
                "marivo.analysis.method_state/v2",
                state_kind,
                f"marivo.analysis.state.{result.contract.state_kind}",
                4,
                root.method.name,
                1,
                result.contract.input_binding,
                tuple(p.role for p in result.parts),
            )
            state = decode(encode(state, STATE), STATE)
            phase = "storage_staging"
            with _execution_log.stage("analysis.storage_staging"):
                staging, final, resources = reserve_output(
                    store,
                    run_ref=run_ref,
                    session_ref=session,
                    artifact_ref=artifact_ref,
                    nonce=nonce,
                )
                keys = tuple(
                    (name, str(result.primary.schema.field(name).type))
                    for name in result.contract.key_fields
                )
                primary = PrimaryReceipt(
                    "marivo.analysis.receipt/v1",
                    "primary",
                    state.input_binding,
                    keys,
                    write_table(
                        store.project_root, staging / "primary", final / "primary", result.primary
                    ),
                )
                event("graph_primary_written")
                parts: list[PartReceipt] = []
                from marivo.analysis.materialization.cell_arrow import binding
                from marivo.analysis.methods.coordinate_state import layout as coordinate_layout

                for part in result.parts:
                    from marivo.analysis.materialization.graph_storage import payload_digest

                    local = write_table(
                        store.project_root,
                        staging / part.role,
                        final / part.role,
                        part.table,
                    )
                    parts.append(
                        PartReceipt(
                            "marivo.analysis.receipt/v2",
                            "part",
                            state.input_binding,
                            tuple(
                                (name, str(part.table.schema.field(name).type))
                                for name in result.contract.parts[len(parts)].key_fields
                            ),
                            local,
                            part.role,
                            f"marivo.analysis.part.{state.kind}.{part.role}",
                            state.contract_version,
                            state.contract_version,
                            payload_digest(store.project_root, staging / part.role, local),
                            coordinate_layout(result.contract.signature, part.role),
                            binding(part.table.schema),
                        )
                    )
                    event("graph_part_written")
            nodes = captured.nodes
            methods = tuple(
                MethodBinding(
                    item.key.method,
                    item.implementation.qualification.implementation_id,
                    item.implementation.contract_version,
                )
                for item in plan.physical_requirements
                if isinstance(item.implementation.qualification, Qualified)
            )
            snapshot = Continuation(
                "marivo.analysis.continuation/v5",
                frozen_graph,
                tuple(
                    dict.fromkeys(
                        c.entity_ref.path for n in nodes for c in n.signature.domain.instance_key
                    )
                ),
                tuple(
                    dict.fromkeys(c.field for n in nodes for c in n.signature.domain.instance_key)
                ),
                semantic_versions(root, _nodes=captured.nodes),
                tuple(n.method for n in nodes if isinstance(n, MethodNode)),
                state.input_binding,
            )
            frozen = encode(snapshot, SNAPSHOT)
            descriptor = Descriptor(
                "marivo.analysis.artifact_descriptor/v6",
                root.fingerprint,
                run_ref,
                key,
                root.signature,
                RowContract(
                    result.contract.key_fields,
                    result.contract.cell_reasons,
                    result.contract.column_reasons,
                ),
                RowSetContract(
                    "keyed"
                    if keys
                    else "optional_singleton"
                    if isinstance(
                        root.parameters,
                        (CellDerive, DisplayRank, DisplayTable, DeviationFit, DeviationRead),
                    )
                    or (
                        isinstance(root.parameters, PartsTransport)
                        and (
                            root.parameters.mode in ("where", "limit")
                            or root.parameters.display_view is not None
                        )
                    )
                    else "singleton",
                    "unordered",
                ),
                schema_text(result.primary.schema),
                digest(canonical_json(snapshot.semantic_versions)),
                methods,
                tuple(
                    evidence_identity(c.requirement, run_ref, c.result_digest)
                    for c in result.completed_checks
                ),
                primary,
                tuple(parts),
                state,
                frozen,
                digest(frozen),
                plan.physical_requirements[-1].key.shape.time
                if root.signature.domain.time_grid is not None
                else NoTime(),
            )
            descriptor = decode(encode(descriptor, DESCRIPTOR), DESCRIPTOR)
            _fsync_directory(staging)
            final.parent.mkdir(parents=True, exist_ok=True)
            os.rename(staging, final)
            _fsync_directory(final.parent)
            event("graph_files_published")
            with _execution_log.stage("analysis.receipt_verification"):
                read_result(store.project_root, descriptor)
            event("graph_receipts_verified")
            phase = "publication"
            from marivo.analysis.materialization.execute_deadline import check

            check()
            committing = True
            with _execution_log.stage("analysis.publication"):
                return graph_store.publish(store, artifact_ref, descriptor, resources, event)
        except BaseException as failure:
            if (
                control_resource is not None
                and source_owner is not None
                and source_owner._cancel_control_released
            ):
                store.discharge(control_resource)
                control_resource = None
            if committing:
                try:
                    original = store._graph_run(run_ref)
                    if original is None:
                        raise invalid("original Run unavailable")
                    if original.lifecycle == "succeeded":
                        saved = _read_artifact(store, artifact_ref)
                        if (
                            saved.producing_run_ref != run_ref
                            or saved.execution_key_digest != key
                            or saved.descriptor != descriptor
                        ):
                            raise invalid("commit read-back differs from original invocation")
                        read_result(
                            store.project_root, saved.descriptor, _validated=saved.validated
                        )
                        return saved
                    with store._read() as conn:
                        conflicts = _rows(
                            conn,
                            "SELECT artifact_ref FROM dataset_artifacts WHERE artifact_ref=? OR (session_ref=? AND execution_key_digest=?)",
                            (artifact_ref, session, key),
                        )
                    if conflicts:
                        raise invalid(
                            "unresolved original Run has conflicting publication metadata"
                        )
                except Exception as error:
                    raise RecoveryPendingError(
                        expected="a verified original publication outcome",
                        received="commit acknowledgement or read-back is unavailable",
                        repair="Preserve the original Run and resource journal; inspect and reconcile it without replaying computation.",
                        stage="graph_commit_unknown",
                        run_ref=run_ref,
                    ) from error
            try:
                current = store._graph_run(run_ref)
                if current is not None and current.lifecycle == "incomplete":
                    owned = tuple(r for r in store.resources(session) if r.run_ref == run_ref)
                    resolved = discharge_resources(store, owned)
                    store.fail(
                        run_ref,
                        RunFailure(
                            phase=phase,
                            kind="execution_failed",
                            safe_message="The graph action failed before publication.",
                            safe_location=f"analysis.graph.{phase}",
                            expected="a fully validated atomic graph publication",
                            received="execution, cancellation or publication failure",
                            repair=AnalysisRepair(
                                kind="inspect",
                                action="Inspect the original Run and its recorded obligations before a new invocation.",
                                help_target=LiveHelpTarget(
                                    surface="analysis", canonical_id="runtime.runs"
                                ),
                            ),
                        ),
                        resolved_resources=resolved,
                    )
            except BaseException as cleanup_error:
                raise failure from cleanup_error
            raise


def execute(
    runtime: DatasetRuntime,
    root: Node,
    routes: tuple[RouteChoice, ...],
    *,
    source_bindings: tuple[SourceKeyBinding, ...] = (),
    source_factory: SourceFactory | None = None,
    source_schemas: tuple[pa.Schema, ...] = (),
) -> graph_store.GraphArtifact:
    entered = time.monotonic()
    invocation = _InvocationRun()
    from marivo.analysis.materialization.execute_deadline import execution_budget

    inherited = _execution_log.snapshot(runtime.store.project_root).fields.get("operation_id")
    operation_id = inherited if isinstance(inherited, str) else f"op_{uuid4().hex}"
    with (
        execution_budget(start=entered),
        binding_scope(),
        _execution_log.scope(
            runtime.store.project_root,
            operation_id=operation_id,
            session_id=runtime.session_ref,
            definition_id=root.identity,
        ) as context,
    ):
        _execution_log.emit("execution.started", context=context)
        try:
            result = _execute(
                runtime,
                root,
                routes,
                invocation=invocation,
                source_bindings=source_bindings,
                source_factory=source_factory,
                source_schemas=source_schemas,
            )
        except BaseException as error:
            reported: BaseException = error
            if isinstance(error, AnalysisError):
                if error.run_ref is None:
                    error.run_ref = invocation.run_ref
            elif isinstance(error, Exception):
                source = source_factory is not None
                reported = MaterializationError(
                    expected="an available input satisfying the admitted graph contract",
                    received="source execution failed" if source else "graph execution failed",
                    repair=(
                        "Inspect the failed Run, correct its source or resource requirement, then execute again to create a new Run."
                        if source and invocation.run_ref is not None
                        else "Correct the source or resource requirement, then execute again; source execution creates a new Run."
                        if source
                        else "Correct the retained input or resource requirement, then execute the logical relation again."
                    ),
                    stage="graph_source" if source else "graph_execution",
                    run_ref=invocation.run_ref,
                    help_target="session.get_run"
                    if invocation.run_ref is not None
                    else "actions.execute",
                )
            _execution_log.emit(
                "execution.completed",
                context=context,
                severity="ERROR",
                state="failed",
                duration_ms=int((time.monotonic() - entered) * 1000),
                fields=_execution_log.error_fields(reported),
            )
            if reported is error:
                raise
            raise reported from error
        _execution_log.emit(
            "execution.completed",
            context=context,
            state="succeeded",
            duration_ms=int((time.monotonic() - entered) * 1000),
            artifact_id=result.artifact_ref,
            producing_run_id=result.producing_run_ref,
        )
        return result
