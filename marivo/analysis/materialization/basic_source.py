"""Basic Analysis source reads through a bound datasource session."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import closing
from typing import TYPE_CHECKING, Literal

import ibis.expr.types as ir
import pyarrow as pa

from marivo.analysis.compiler import compile_dataset
from marivo.analysis.compiler.nodes import (
    CompiledRelationFence,
    RetainedPartSpec,
    RetainedRelationSpec,
)
from marivo.analysis.compiler.normalize import required_source_dependencies
from marivo.analysis.compiler.placement import SourceBinding, SourceStep
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.materialization import contracts as codec
from marivo.analysis.materialization import dataset_publication
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.resources import backend_reservation
from marivo.analysis.materialization.storage import DatasetWriteResult, PartWriteSpec
from marivo.analysis.materialization.submissions import Submission
from marivo.analysis.operators.registry import implementation
from marivo.datasource.adapters import (
    PhysicalRequirement,
    QualifiedSource,
    SourceSession,
    provider_for,
    provider_names,
)
from marivo.datasource.errors import DatasourceError
from marivo.datasource.ir import TableSourceIR

if TYPE_CHECKING:
    from marivo.analysis.compiler.nodes import CompiledDataset
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.materialization.contracts import StorageReceipt


def _unqualified(received: str) -> MaterializationError:
    return MaterializationError(
        expected="one basic table source qualified for an Ibis Analysis read",
        received=received,
        repair="Use an admitted basic source or wait for the named method migration.",
        stage="source_admission",
    )


def _read_batches(
    runtime: DatasetRuntime,
    session: SourceSession,
    source: QualifiedSource,
    expression: ir.Table,
    *,
    purpose: str,
) -> Generator[pa.RecordBatch, None, None]:
    read = session.compile(
        source,
        expression,
        purpose=purpose,
        expected_schema=expression.schema().to_pyarrow(),
    )
    stream = session.batches(read, chunk_size=1024)
    observed = Submission("source", purpose, read.sql)
    runtime._observe_submission(observed)
    try:
        for batch in stream:
            runtime._event("transfer")
            runtime.statistics.transferred_rows += batch.num_rows
            runtime.statistics.transferred_bytes += batch.nbytes
            yield batch
    except BaseException as error:
        observed.fail(error)
        raise
    finally:
        try:
            stream.close()
        except BaseException as error:
            observed.fail(error)
            raise
        finally:
            if observed.state == "submitted":
                observed.state = (
                    "succeeded" if session.submissions[-1].state == "succeeded" else "failed"
                )


def _restore_single_identity(
    batches: Generator[pa.RecordBatch, None, None], *, key_name: str
) -> Generator[pa.RecordBatch, None, None]:
    for batch in batches:
        index = batch.schema.get_field_index("entity_identity")
        if index < 0:
            raise _unqualified("missing entity_identity output")
        columns = list(batch.columns)
        columns[index] = pa.StructArray.from_arrays([columns[index]], names=[key_name])
        yield pa.RecordBatch.from_arrays(columns, names=batch.schema.names)


def execute_basic_source(
    runtime: DatasetRuntime,
    dataset: LogicalDataset,
    step: SourceStep,
    run_ref: str,
) -> tuple[str, DatasetWriteResult[StorageReceipt], CompiledDataset]:
    """Execute a one-table basic Population or sum/count Metric read."""
    if not isinstance(step.binding, SourceBinding) or step.binding.adapter not in provider_names():
        raise _unqualified("unsupported basic source backend")
    owner = step.binding.owner
    datasource = owner.semantic_registry.datasources[step.binding.datasource_id]
    dependencies = required_source_dependencies(dataset, registry=owner.semantic_registry)
    if len(dependencies.entries) != 1:
        raise _unqualified(f"{len(dependencies.entries)} physical source dependencies")
    dependency = dependencies.entries[0]
    entity = dependency.entity
    source = entity.source
    if not isinstance(source, TableSourceIR):
        raise _unqualified(type(source).__name__)
    reservation = backend_reservation(run_ref, step.binding.datasource_id)
    runtime.store.reserve(reservation)
    runtime._event("resource_create")
    runtime._event("profile_resolution")
    try:
        session = provider_for(step.binding.adapter).open(datasource)
    except DatasourceError as error:
        raise MaterializationError(
            expected="an open basic source session for the selected backend",
            received=f"{type(error).__name__} during selected datasource open",
            repair="Correct the selected datasource connection and retry the action.",
            stage="source_admission",
            run_ref=run_ref,
        ) from None
    try:
        source_identity = codec.digest((entity.ref.path, dataset.definition_fingerprint))
        bound = session.bind(source, source_identity=source_identity)
        method = implementation(dataset)
        operations: frozenset[Literal["scan", "filter", "project", "group", "count"]] = (
            frozenset({"scan", "filter", "project", "group", "count"})
            if dataset.kind == "metric"
            else frozenset({"scan", "filter", "project"})
            if method.operator_id == "population.where"
            else frozenset({"scan", "project"})
        )
        qualified = session.qualify(
            bound,
            PhysicalRequirement(method.operator_id, method.version, operations),
        )
        relation = bound.relation
        columns = dependency.columns
        for column in columns:
            if column.physical not in relation.columns:
                raise _unqualified(f"missing physical column {column.physical!r}")
        table = relation.select(
            *(relation[column.physical].name(column.logical) for column in columns)
        )
        recipe = compile_dataset(
            dataset,
            {entity.ref.path: table},
            dependencies=dependencies,
            source_owner=owner,
            scalar_single_identity=(
                step.binding.adapter == "mysql" and len(entity.primary_key) == 1
            ),
        )
        if any(isinstance(part, RetainedRelationSpec) for part in recipe.retained_parts) or any(
            isinstance(item, CompiledRelationFence) for item in recipe.preparations
        ):
            raise _unqualified("source preparation or retained parts outside the basic route")
        for check in recipe.validations:
            with closing(
                _read_batches(
                    runtime,
                    session,
                    qualified,
                    check.expression,
                    purpose="validation_batch",
                )
            ) as checked_batches:
                rows = pa.Table.from_batches(
                    checked_batches, schema=check.expression.schema().to_pyarrow()
                ).to_pylist()
            if len(rows) != 1 or rows[0].get("violations") != 0:
                raise MaterializationError(
                    expected=check.expected or "zero source validation violations",
                    received=f"source validation failed: {check.name}",
                    repair=check.repair or "Correct the named source validation.",
                    stage="output_validation",
                    run_ref=run_ref,
                )
        with closing(
            _read_batches(runtime, session, qualified, recipe.expression, purpose="primary")
        ) as incoming:
            output = (
                _restore_single_identity(incoming, key_name=entity.primary_key[0])
                if (
                    dataset.kind == "population"
                    and step.binding.adapter == "mysql"
                    and len(entity.primary_key) == 1
                )
                else incoming
            )
            parts = tuple(
                PartWriteSpec(part.role, part.contract_id, part.contract_version, part.column_names)
                for part in recipe.retained_parts
                if isinstance(part, RetainedPartSpec)
            )
            artifact_ref, storage = dataset_publication.write_output(
                runtime,
                dataset,
                output,
                run_ref,
                parts=parts,
                source_key_validation=True,
            )
        return artifact_ref, storage, recipe
    except DatasourceError as error:
        runtime._event("cancel_" + session.interrupt())
        raise MaterializationError(
            expected=error.expected or "an exact qualified Ibis source read",
            received=error.received or type(error).__name__,
            repair="Inspect the selected physical source and retry this basic Analysis read.",
            stage="source_transfer",
            run_ref=run_ref,
        ) from None
    except BaseException:
        runtime._event("cancel_" + session.interrupt())
        raise
    finally:
        session.close()
