"""Private J1 publication and exact Artifact recovery through the existing Store."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TypeAlias
from uuid import uuid4

import pyarrow as pa

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.handles import BoundedLineage, LogicalRootHandle
from marivo.analysis.materialization.contracts import (
    ArtifactDescriptor,
    ArtifactRecord,
    J1ArtifactExchange,
    MaterializationContract,
    PopulationAuthority,
    RunFailure,
    RunRecord,
    canonical_json,
    digest,
    finding_extractor,
    finding_policy,
    required_retained_contracts,
    run_failure_phase,
)
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.execution import ExchangeStreamBinding, ValidatedExchangeStream
from marivo.analysis.materialization.quality import QualitySummary
from marivo.analysis.materialization.reads import (
    open_receipt_batch_stream,
    part_schema,
    read_part_batches,
)
from marivo.analysis.materialization.resources import discharge_resources, reserve_output
from marivo.analysis.materialization.storage import (
    IndependentPartWrite,
    PartWriteSpec,
    write_local_dataset,
)
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.observation.contracts import producer_contract
from marivo.analysis.observation.dsl_j1 import (
    J1Context,
    J1Difference,
    J1Group,
    J1Members,
    J1Observed,
    J1Read,
    J1Statistic,
    j1_row_contracts,
)
from marivo.analysis.operators.dsl_j1_contracts import j1_numeric_method
from marivo.analysis.operators.dsl_j1_values import J1ExecutionResult
from marivo.semantic.validator import normalize_target_entity

J1Node: TypeAlias = J1Members | J1Read | J1Group | J1Observed | J1Statistic | J1Difference
_STATE = (
    ("value.sum", "dsl.j1.value_sum", "state_sum"),
    ("value.non_null_count", "dsl.j1.non_null_count", "non_null_count"),
    ("value.row_count", "dsl.j1.row_count", "row_count"),
)
_CURRENT = (
    ("current_sum", "dsl.j1.current_sum", "current_sum"),
    ("current_count", "dsl.j1.current_count", "current_count"),
)
_CELL_REASONS = (
    ("null", ("source_null", "empty_contribution")),
    ("undefined", ("empty_mean",)),
)


def _error(received: str) -> MaterializationError:
    return MaterializationError(
        expected="one exact complete J1 Artifact and admitted Run",
        received=received,
        repair="Select the original J1 definition, input binding and complete Artifact.",
        stage="exchange",
    )


def _context(node: J1Node) -> J1Context:
    if isinstance(node, (J1Read, J1Group)):
        return node.members_input.context
    return node.context


def _meaning(node: J1Node) -> tuple[d.AnalysisDomain, d.QuantityState | None]:
    if isinstance(node, J1Read):
        return node.members_input.domain, None
    if isinstance(node, (J1Statistic, J1Difference)):
        return node.domain, node.quantity
    if isinstance(node, J1Observed):
        return node.domain, node.quantity
    return node.domain, None


def _identity(node: J1Node) -> tuple[str, tuple[tuple[str, str], ...], str]:
    root = node.root
    while root.inputs:
        ancestor = root.inputs[0].root
        if not isinstance(ancestor, LogicalRootHandle):
            raise _error("J1 origin is not a logical member definition")
        root = ancestor
    if (
        type(root.parameters) is not tuple
        or not root.parameters
        or not isinstance(root.parameters[0], str)
    ):
        raise _error("J1 origin has no Entity binding")
    entity_path = root.parameters[0]
    entity = normalize_target_entity(_context(node).registry, entity_path)
    return entity_path, entity.identity_signature, root.definition_fingerprint


def _batches(table: pa.Table) -> tuple[pa.RecordBatch, ...]:
    batches = tuple(table.to_batches())
    if batches:
        return batches
    return (
        pa.RecordBatch.from_arrays(
            [pa.array([], type=field.type) for field in table.schema], schema=table.schema
        ),
    )


@dataclass(slots=True)
class _TableStream:
    schema: pa.Schema
    batches: tuple[pa.RecordBatch, ...]
    closed: bool = False

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        yield from self.batches

    def close(self) -> None:
        self.closed = True


def _validated_primary(node: J1Node, result: J1ExecutionResult) -> pa.Table:
    row, rows = j1_row_contracts(_context(node), node.root)
    names = tuple(field.name for field in row.schema.columns)
    selected = result.primary.select(names)
    primary = selected.cast(
        pa.schema(
            [
                pa.field(field.name, selected.schema.field(field.name).type, field.nullable)
                for field in row.schema.columns
            ]
        ),
        safe=True,
    )
    kind = "value" if "cell_tag" in names else "relation"
    binding = ExchangeStreamBinding(
        kind, row, rows, primary.schema, _CELL_REASONS if kind == "value" else ()
    )
    stream = ValidatedExchangeStream(_TableStream(primary.schema, _batches(primary)), binding)
    try:
        table = pa.Table.from_batches(tuple(stream), schema=primary.schema)
        if not stream.completed:
            raise _error("J1 output validation was not exhausted")
        return table
    finally:
        stream.close()


def _part_specs(result: J1ExecutionResult) -> tuple[PartWriteSpec, ...]:
    names = set(result.primary.column_names)
    keys = tuple(name for name in ("member", "group") if name in names)
    chosen = _STATE if "state_sum" in names else _CURRENT
    return tuple(
        PartWriteSpec(role, contract, 1, (*keys, column))
        for role, contract, column in chosen
        if column in names
    )


def _producer(result: J1ExecutionResult) -> str:
    if result.root.operator_id == "dsl.j1.compare":
        return "dsl.j1.compare"
    names = set(result.primary.column_names)
    if "state_sum" in names:
        return (
            "dsl.j1.observe_coordinates"
            if result.parts
            else (
                "dsl.j1.observe"
                if result.root.operator_id == "dsl.j1.observe"
                else "dsl.j1.derived_state"
            )
        )
    if "current_sum" in names or "current_count" in names:
        return (
            "dsl.j1.current_mean"
            if {"current_sum", "current_count"} <= names
            else ("dsl.j1.current_sum" if "current_sum" in names else "dsl.j1.current_count")
        )
    return "dsl.j1.value" if "value" in names else "dsl.j1.relation"


def _materialization(row: d.DatasetRowContract, producer_id: str) -> MaterializationContract:
    registration = producer_contract(producer_id)
    return MaterializationContract(
        registration.producer_id,
        1,
        row.shape_id,
        registration.quality_id,
        1,
        registration.evidence_id,
        1,
        finding_extractor(row, producer_id),
        1,
        (registration.validation_id,),
        required_retained_contracts(row, registration.retained_contract_ids),
        finding_policy(row, producer_id),
    )


def _fail_pending(store: SessionStore, run: RunRecord) -> None:
    current = store.run(run.run_ref)
    if current is None or current.lifecycle != "incomplete":
        return
    resources = tuple(
        resource for resource in store.resources(run.session_ref) if resource.run_ref == run.run_ref
    )
    resolved = discharge_resources(store, resources)
    if len(resolved) == len(resources):
        store.fail(
            run.run_ref,
            RunFailure(
                phase=run_failure_phase("publication", "publication"),
                kind="execution_failed",
                safe_message="The J1 action failed before publication.",
                safe_location="dataset.publication",
                expected="a complete validated J1 Artifact",
                received="publication failed",
                repair=None,
            ),
            resolved_resources=resolved,
        )


def _publish_j1_artifact(
    store: SessionStore,
    run: RunRecord,
    node: J1Node,
    result: J1ExecutionResult,
    *,
    input_binding: str,
    member_binding: str | None = None,
    event: Callable[[str], None] = lambda _name: None,
) -> ArtifactRecord:
    """Perform one J1 publication under a caller-owned Run and writer guard."""
    row, rows = j1_row_contracts(_context(node), node.root)
    selected_run = store.run(run.run_ref)
    if (
        selected_run != run
        or run.session_ref != _context(node).session_id
        or result.root is not node.root
        or run.lifecycle != "incomplete"
        or run.dataset_input.definition_fingerprint != node.root.definition_fingerprint
        or run.dataset_input.row_contract_fingerprint != d._row_contract_fingerprint(row)
        or run.dataset_input.row_set_contract_fingerprint != d._row_set_contract_fingerprint(rows)
        or not input_binding
        or (member_binding is not None and not member_binding)
    ):
        raise _error("Run, J1 definition or input binding differs")
    primary = _validated_primary(node, result)
    if isinstance(node, J1Observed) and not {"complete_coverage", "contribution_partition"} <= set(
        result.completed_checks
    ):
        raise _error("J1 source obligations are incomplete")
    if isinstance(node, J1Difference):
        method = j1_numeric_method(node.root)
        if method is None or not set(method.contract.required_checks) <= set(
            result.completed_checks
        ):
            raise _error("J1 comparison obligations are incomplete")
    parts = _part_specs(result)
    coordinate = tuple(IndependentPartWrite(role, _batches(table)) for role, table in result.parts)
    nonce = uuid4().hex
    artifact_ref = "artifact_" + nonce
    staging, final, outputs = reserve_output(
        store,
        run_ref=run.run_ref,
        session_ref=run.session_ref,
        artifact_ref=artifact_ref,
        nonce=nonce,
    )
    try:
        written = write_local_dataset(
            project_root=store.project_root,
            staging_path=staging,
            final_path=final,
            batches=_batches(result.primary),
            row_contract=row,
            row_set_contract=rows,
            parts=parts,
            independent_parts=coordinate,
            source_key_validation=True,
            event=event,
        )
        domain, quantity = _meaning(node)
        method = j1_numeric_method(node.root)
        entity_path, signature, membership_definition = _identity(node)
        exchange = J1ArtifactExchange(
            "value" if "cell_tag" in primary.column_names else "relation",
            node.root.operator_id,
            canonical_json(d._descriptor_payload(domain)),
            None if quantity is None else canonical_json(d._descriptor_payload(quantity)),
            node.root.operator_id if method is None else method.contract.method_id,
            1 if method is None else method.contract.version,
            input_binding,
            result.completed_checks,
            hashlib.sha256(primary.schema.serialize().to_pybytes()).hexdigest(),
            written.primary_receipt.identity_digest,
            tuple(
                (
                    item.role,
                    item.contract_id,
                    item.contract_version,
                    item.storage_receipt.schema_fingerprint,
                    item.storage_receipt.identity_digest,
                )
                for item in written.retained_parts
            ),
            member_binding,
        )
        descriptor = ArtifactDescriptor(
            definition_fingerprint=node.root.definition_fingerprint,
            row_contract=row,
            row_set_contract=rows,
            realized_schema=written.realized_schema,
            bounded_lineage=BoundedLineage((node.root.operator_id,), 0),
            semantic_dependency_digest=digest(
                (node.root.definition_fingerprint, input_binding)
                if member_binding is None
                else (node.root.definition_fingerprint, input_binding, member_binding)
            ),
            population_authority=PopulationAuthority(membership_definition, entity_path, signature),
            sampling_execution=None,
            operator_implementation_versions=((node.root.operator_id, 1),),
            dataset_materialization_contract=_materialization(row, _producer(result)),
            storage_receipt=written.primary_receipt,
            retained_parts=written.retained_parts,
            quality_summary=QualitySummary(
                sample_size=written.realized_row_count,
                evaluated_check_count=len(result.completed_checks) + 1,
                failed_check_count=0,
                warning_check_count=0,
            ),
            j1_exchange=exchange,
        )
        return store.publish(
            run.run_ref,
            artifact_ref,
            descriptor,
            resolved_resources=outputs,
            event=event,
        )
    except BaseException:
        _fail_pending(store, run)
        raise


def publish_j1_artifact(
    store: SessionStore,
    run: RunRecord,
    node: J1Node,
    result: J1ExecutionResult,
    *,
    input_binding: str,
    member_binding: str | None = None,
    event: Callable[[str], None] = lambda _name: None,
) -> ArtifactRecord:
    """Publish a completed J1 stage under its caller-owned admitted Run and writer guard."""
    try:
        return _publish_j1_artifact(
            store,
            run,
            node,
            result,
            input_binding=input_binding,
            member_binding=member_binding,
            event=event,
        )
    except BaseException:
        _fail_pending(store, run)
        raise


def load_j1_artifact(
    store: SessionStore,
    session_ref: str,
    artifact_ref: str,
    node: J1Node,
    *,
    input_binding: str,
) -> J1ExecutionResult:
    """Recover exact J1 rows and all state without consulting a source backend."""
    record = store.artifact(artifact_ref)
    if record is None or record.session_ref != session_ref:
        raise _error("selected Artifact is missing or owned by another Session")
    descriptor = record.descriptor
    exchange = descriptor.j1_exchange
    row, rows = j1_row_contracts(_context(node), node.root)
    domain, quantity = _meaning(node)
    method = j1_numeric_method(node.root)
    if (
        exchange is None
        or descriptor.definition_fingerprint != node.root.definition_fingerprint
        or descriptor.row_contract != row
        or descriptor.row_set_contract != rows
        or exchange.domain != canonical_json(d._descriptor_payload(domain))
        or exchange.quantity
        != (None if quantity is None else canonical_json(d._descriptor_payload(quantity)))
        or exchange.method_id
        != (node.root.operator_id if method is None else method.contract.method_id)
        or exchange.method_version != (1 if method is None else method.contract.version)
        or exchange.input_binding != input_binding
    ):
        raise _error("selected J1 definition, method or input binding differs")
    source = open_receipt_batch_stream(store.project_root, descriptor.storage_receipt, row, rows)
    if (
        hashlib.sha256(source.schema.serialize().to_pybytes()).hexdigest()
        != exchange.primary_schema_fingerprint
    ):
        source.close()
        raise _error("selected primary schema differs")
    try:
        binding = ExchangeStreamBinding(
            exchange.kind,
            row,
            rows,
            source.schema,
            _CELL_REASONS if exchange.kind == "value" else (),
        )
        stream = ValidatedExchangeStream(source, binding)
    except BaseException:
        source.close()
        raise
    try:
        primary = pa.Table.from_batches(tuple(stream), schema=stream.schema)
        if not stream.completed:
            raise _error("primary receipt checks were not completed")
    finally:
        stream.close()
    keyed_parts: dict[str, pa.Table] = {}
    coordinates: tuple[tuple[str, pa.Table], ...] = ()
    endpoints: list[tuple[str, pa.Table]] = []
    for part in descriptor.retained_parts:
        schema = part_schema(store.project_root, part)
        reader = read_part_batches(store.project_root, part, expected_schema=schema)
        try:
            table = pa.Table.from_batches(tuple(reader), schema=schema)
        finally:
            reader.close()
        if part.role == "coordinate":
            coordinates = (("coordinate", table),)
        elif part.role in ("current_endpoint", "baseline_endpoint"):
            endpoints.append((part.role, table))
        else:
            keyed_parts[part.role] = table
    wide = primary
    keys = tuple(field.name for field in row.schema.columns if field.field_id in row.key_field_ids)
    primary_keys = [tuple(item[name] for name in keys) for item in primary.to_pylist()]
    for table in keyed_parts.values():
        column = next(name for name in table.column_names if name not in keys)
        positions = {
            tuple(item[name] for name in keys): index
            for index, item in enumerate(table.to_pylist())
        }
        if len(positions) != table.num_rows or set(positions) != set(primary_keys):
            raise _error("retained state keys differ from primary keys")
        aligned = table.take(pa.array([positions[key] for key in primary_keys], type=pa.int64()))
        wide = wide.append_column(aligned.schema.field(column), aligned[column])
    return J1ExecutionResult(node.root, wide, (*coordinates, *endpoints), exchange.completed_checks)
