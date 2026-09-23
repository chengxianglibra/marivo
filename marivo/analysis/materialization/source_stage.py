"""Private first-route execution from pure definition to committed Dataset authority."""

from __future__ import annotations

import sys
from collections.abc import Iterable, Iterator, Mapping
from contextlib import ExitStack
from typing import TYPE_CHECKING

import ibis.expr.types as ir
import pandas as pd
import pyarrow as pa

from marivo.analysis.compiler.nodes import (
    CompiledDataset,
    RetainedPartSpec,
)
from marivo.analysis.compiler.placement import (
    SourceStep,
)
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.datasets.descriptors import DatasetRowContract
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.domains.lifecycle_reducers import (
    REDUCER_TYPES,
)
from marivo.analysis.materialization import dataset_publication
from marivo.analysis.materialization.attribution_publication import AttributionSourceSummary
from marivo.analysis.materialization.contracts import (
    ArtifactRecord,
    StorageReceipt,
)
from marivo.analysis.materialization.duckdb_statements import attribution_summary_sql
from marivo.analysis.materialization.errors import (
    MaterializationError,
)
from marivo.analysis.materialization.errors import _execution_error as _error
from marivo.analysis.materialization.execution import ExecutionAdapter
from marivo.analysis.materialization.execution_state import ExecutionProgress
from marivo.analysis.materialization.source_preparation import prepared_source
from marivo.analysis.materialization.storage import (
    DatasetWriteResult,
    IndependentPartWrite,
    PartWriteSpec,
)
from marivo.analysis.operators.candidate_contracts import (
    CandidateDefinition,
    EntityCandidateEvaluationSummary,
)
from marivo.analysis.operators.driver_contracts import (
    DriverCandidateDefinition,
    DriverCandidateEvaluationSummary,
)

if TYPE_CHECKING:
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.materialization.dataset_execution import ExecutionEvidence


def attribution_source_summary(
    self: DatasetRuntime, backend: ExecutionAdapter, table: ir.Table, row: DatasetRowContract
) -> AttributionSourceSummary:
    """Reduce complete Attribution proof inside its engine; return only global facts."""
    from marivo.analysis.operators.attribution_contracts import AttributionSemantics

    semantics = row.family_semantics
    if not isinstance(semantics, AttributionSemantics):
        raise _error("output_validation")
    names = {field.field_id: field.name for field in row.schema.columns}

    sql = attribution_summary_sql(
        backend.compile(table), names, semantics.scope_field_ids, row.key_field_ids
    )
    result: object = backend.submit(
        backend.statement(sql, role="attribution.source_summary", inputs=(backend.prepare(table),))
    ).fetchone()
    if not isinstance(result, tuple) or len(result) != 6:
        raise _error("output_validation")
    scopes, resolutions, ok, zero, error, digest = result
    if (
        not all(type(value) is int and value >= 0 for value in (scopes, resolutions, ok, zero))
        or not isinstance(error, (int, float))
        or not isinstance(digest, str)
    ):
        raise _error("output_validation")
    return AttributionSourceSummary(
        scopes, resolutions, (("ok", ok), ("zero_total_delta", zero)), float(error), digest
    )


def batches(
    self: DatasetRuntime,
    backend: ExecutionAdapter,
    expression: ir.Table,
    batch_rows: int,
    *,
    role: str = "primary",
) -> Iterator[pa.RecordBatch]:
    reader = backend.batches(expression, role=role, chunk_size=batch_rows)
    seen = False
    try:
        for batch in reader:
            self._event("transfer")
            seen = True
            self.statistics.transferred_rows += batch.num_rows
            self.statistics.transferred_bytes += batch.nbytes
            yield batch
        if not seen:
            yield pa.RecordBatch.from_arrays(
                [pa.array([], type=field.type) for field in reader.schema], schema=reader.schema
            )
    finally:
        failed = sys.exc_info()[0] is not None
        try:
            reader.close()
        except BaseException:
            if not failed:
                raise


def _decode_scalar_masks(
    batches: Iterable[pa.RecordBatch], arity: int, run_ref: str
) -> Iterator[pa.RecordBatch]:
    """Restore exact fixed-width source mask bits at the public storage boundary."""
    for batch in batches:
        arrays = []
        fields = []
        for schema_field, column in zip(batch.schema, batch.columns, strict=True):
            if schema_field.name not in {"active_axis_mask", "other_mask"}:
                arrays.append(column)
                fields.append(schema_field)
                continue
            values = column.to_pylist()
            if any(
                not isinstance(value, str)
                or len(value) != arity
                or any(bit not in "01" for bit in value)
                for value in values
            ):
                raise MaterializationError(
                    expected=f"exact {arity}-bit source Attribution mask",
                    received=f"invalid {schema_field.name} value",
                    repair="Correct the source mask lowering before publication.",
                    stage="storage_staging",
                    run_ref=run_ref,
                )
            arrays.append(
                pa.array(
                    [[bit == "1" for bit in value] for value in values], type=pa.list_(pa.bool_())
                )
            )
            fields.append(schema_field.with_type(pa.list_(pa.bool_())))
        yield pa.RecordBatch.from_arrays(arrays, schema=pa.schema(fields))


def prepare_sources(
    self: DatasetRuntime,
    source_steps: tuple[SourceStep, ...],
    records: Mapping[str, ArtifactRecord],
    run_ref: str,
    evidence: ExecutionEvidence,
    source_contexts: ExitStack,
) -> dict[int, tuple[ExecutionAdapter, CompiledDataset, dict[str, ir.Table]]]:
    prepared: dict[int, tuple[ExecutionAdapter, CompiledDataset, dict[str, ir.Table]]] = {}
    for source_boundary in source_steps:
        boundary_validations: list[tuple[str, int]] = []
        prepared[source_boundary.output] = source_contexts.enter_context(
            prepared_source(
                self,
                run_ref,
                source_boundary,
                records,
                boundary_validations,
            )
        )
        proof_backend, proof_recipe, _ = prepared[source_boundary.output]
        if proof_recipe.lifecycle_coverage is not None:
            from marivo.analysis.materialization.lifecycle_publication import (
                native_summary,
            )

            evidence.lifecycle_summary = native_summary(
                proof_backend,
                proof_recipe,
                source_boundary.dataset.row_contract,
            )
        if proof_recipe.lifecycle_reducer_coverage is not None and (
            isinstance(source_boundary.dataset.row_contract.family_semantics, REDUCER_TYPES)
            or proof_recipe.lifecycle_selection_payload is not None
        ):
            from marivo.analysis.materialization.lifecycle_reducer_publication import (
                native_summary as continuation_summary,
            )

            evidence.lifecycle_summary = continuation_summary(
                proof_backend,
                proof_recipe,
                source_boundary.dataset.row_contract,
                filtered=isinstance(source_boundary.dataset._root, LogicalRootHandle)
                and source_boundary.dataset._root.operator_id == "lifecycle.where",
            )
        if proof_recipe.event_proof is not None:
            from marivo.analysis.materialization.event_codec import (
                summary_from_proof,
            )

            if proof_recipe.event_coverage is None:
                raise _error("output_validation", run_ref)
            if proof_backend.engine == "postgres":
                from marivo.analysis.materialization.postgres_execution import (
                    PostgresExecutionAdapter,
                )

                if not isinstance(proof_backend, PostgresExecutionAdapter):
                    raise _error("implementation_registration", run_ref)
                checked_event = proof_backend.event_bundle_proof()
            elif proof_backend.engine == "clickhouse":
                from marivo.analysis.materialization.clickhouse_execution import (
                    ClickHouseExecutionAdapter,
                )

                if not isinstance(proof_backend, ClickHouseExecutionAdapter):
                    raise _error("implementation_registration", run_ref)
                checked_event = proof_backend.event_bundle_proof()
            else:
                checked_event = proof_backend.read_table(
                    proof_backend.prepare(proof_recipe.event_proof, role="event.journey_summary")
                )
            if checked_event.num_rows != 1:
                raise _error("output_validation", run_ref)
            evidence.event_summary = summary_from_proof(
                checked_event.to_pylist()[0], proof_recipe.event_coverage
            )
            boundary_validations.append(("event.journey_output", 0))
        if proof_recipe.event_reducer_proof is not None:
            from marivo.analysis.materialization.event_reducer_codec import (
                summary_from_proof as reducer_summary,
            )

            if proof_recipe.event_reducer_coverage is None:
                raise _error("output_validation", run_ref)
            checked_reducer = proof_backend.read_table(
                proof_backend.prepare(
                    proof_recipe.event_reducer_proof,
                    role="event.reducer_summary",
                )
            )
            if checked_reducer.num_rows != 1:
                raise _error("output_validation", run_ref)
            evidence.event_summary = reducer_summary(
                str(source_boundary.dataset.row_contract.shape_id),
                checked_reducer.to_pylist()[0],
                proof_recipe.event_reducer_coverage,
            )
            boundary_validations.append(("event.reducer_output", 0))
        if proof_recipe.selection_proof is not None:
            from marivo.analysis.materialization.event_reducer_codec import (
                selection_summary_from_proof,
            )

            if (
                proof_recipe.selection_coverage is None
                or proof_recipe.selection_payload is None
                or proof_recipe.selection_input_definition is None
            ):
                raise _error("output_validation", run_ref)
            checked_selection = proof_backend.read_table(
                proof_backend.prepare(proof_recipe.selection_proof, role="event.selection_summary")
            )
            if checked_selection.num_rows != 1:
                raise _error("output_validation", run_ref)
            evidence.selection_summary = selection_summary_from_proof(
                checked_selection.to_pylist()[0],
                proof_recipe.selection_coverage,
                journey=proof_recipe.selection_payload.journey,
                step=proof_recipe.selection_payload.selection.step,
                input_definition=proof_recipe.selection_input_definition,
            )
            boundary_validations.append(("event.selection_output", 0))
        if proof_recipe.candidate_proof is not None:
            from marivo.analysis.compiler.entity_candidate import (
                decode_candidate_proof,
            )

            if proof_recipe.candidate_definition is None:
                raise _error("implementation_registration", run_ref)
            scalar_proof = proof_backend.read_table(
                proof_backend.prepare(
                    proof_recipe.candidate_proof,
                    role="candidate.driver_summary"
                    if isinstance(
                        proof_recipe.candidate_definition,
                        DriverCandidateDefinition,
                    )
                    else "candidate.entity_summary",
                )
            )
            if scalar_proof.num_rows != 1:
                raise _error("output_validation", run_ref)
            if isinstance(proof_recipe.candidate_definition, DriverCandidateDefinition):
                from marivo.analysis.compiler.driver_candidate import (
                    decode_driver_candidate_proof,
                )

                evidence.candidate_summary = decode_driver_candidate_proof(
                    scalar_proof.to_pylist()[0],
                    proof_recipe.candidate_definition,
                )
            else:
                evidence.candidate_summary = decode_candidate_proof(
                    scalar_proof.to_pylist()[0],
                    proof_recipe.candidate_definition,
                )
        if proof_recipe.association_proof is not None:
            from marivo.analysis.operators.association_values import (
                summarize_search,
            )

            proof_table = proof_backend.read_table(
                proof_backend.prepare(
                    proof_recipe.association_proof,
                    role="association.search_summary",
                )
            )
            evidence.association_summary = summarize_search(
                proof_table.to_pandas(types_mapper=pd.ArrowDtype),
                source_boundary.dataset.row_contract,
            )
        if (
            proof_recipe.attribution_proof is not None
            and source_boundary.dataset.kind == "attribution"
            and (
                proof_backend.engine == "duckdb"
                or any(
                    field.role_id == "entity_identity"
                    for field in source_boundary.dataset.row_contract.schema.columns
                )
            )
        ):
            evidence.attribution_summary = attribution_source_summary(
                self,
                proof_backend,
                proof_recipe.attribution_proof,
                source_boundary.dataset.row_contract,
            )
        evidence.validations.extend(
            (
                f"source.{source_boundary.output}.{name}" if len(source_steps) > 1 else name,
                value,
            )
            for name, value in boundary_validations
        )
    return prepared


def execute_source_only(
    self: DatasetRuntime,
    dataset: LogicalDataset,
    source_step: SourceStep | None,
    prepared: Mapping[int, tuple[ExecutionAdapter, CompiledDataset, dict[str, ir.Table]]],
    run_ref: str,
    evidence: ExecutionEvidence,
    progress: ExecutionProgress,
) -> tuple[str, DatasetWriteResult[StorageReceipt]]:
    if source_step is None:
        raise _error("execution_boundary", run_ref)
    current_backend, recipe, _tables = prepared[source_step.output]
    if (
        dataset.kind == "candidate"
        and dataset.row_contract.shape_id.local_shape_id == "entity-outlier"
    ):
        from marivo.analysis.compiler.entity_candidate import (
            entity_candidate_output_proof,
        )

        if (
            evidence.candidate_summary is None
            or not isinstance(
                evidence.candidate_summary.evaluation,
                EntityCandidateEvaluationSummary,
            )
            or not isinstance(evidence.candidate_summary.definition, CandidateDefinition)
        ):
            raise _error("output_validation", run_ref)
        output_proof = entity_candidate_output_proof(
            recipe.expression,
            dataset.row_contract,
            evidence.candidate_summary.definition,
            evaluation=evidence.candidate_summary.evaluation,
        )
        checked = current_backend.read_table(
            current_backend.prepare(output_proof, role="candidate.entity_output")
        )
        if (
            checked.column_names != ["violations"]
            or checked.num_rows != 1
            or checked["violations"][0].as_py() != 0
        ):
            raise _error("output_validation", run_ref)
        evidence.validations.append(("candidate.entity_output", 0))
    if (
        dataset.kind == "candidate"
        and dataset.row_contract.shape_id.local_shape_id == "driver-axis"
    ):
        from marivo.analysis.compiler.driver_candidate import (
            driver_candidate_output_proof,
        )

        if (
            evidence.candidate_summary is None
            or not isinstance(evidence.candidate_summary.definition, DriverCandidateDefinition)
            or not isinstance(
                evidence.candidate_summary.evaluation,
                DriverCandidateEvaluationSummary,
            )
        ):
            raise _error("output_validation", run_ref)
        driver_proof = driver_candidate_output_proof(
            recipe.expression,
            dataset.row_contract,
            evidence.candidate_summary.definition,
            evaluation=evidence.candidate_summary.evaluation,
        )
        checked_driver = current_backend.read_table(
            current_backend.prepare(driver_proof, role="candidate.driver_output")
        )
        if (
            checked_driver.column_names != ["violations"]
            or checked_driver.num_rows != 1
            or checked_driver["violations"][0].as_py() != 0
        ):
            raise _error("output_validation", run_ref)
        evidence.validations.append(("candidate.driver_output", 0))
    from marivo.analysis.compiler.nodes import RetainedRelationSpec
    from marivo.analysis.materialization.retained import (
        validate_source_private_relation,
    )

    for part in recipe.retained_parts:
        if isinstance(part, RetainedRelationSpec):
            validate_source_private_relation(
                current_backend,
                part.expression,
                recipe.expression.select(recipe.primary_columns),
                dataset.row_contract,
                part.role,
            )
    independent_parts = tuple(
        IndependentPartWrite(
            part.role,
            batches(
                self,
                current_backend,
                part.expression,
                1024,
                role="part." + part.role,
            ),
        )
        for part in recipe.retained_parts
        if isinstance(part, RetainedRelationSpec)
    )
    incoming = batches(
        self,
        current_backend,
        recipe.expression,
        1024,
    )
    if dataset.kind == "attribution" and source_step.binding.adapter in {
        "sqlite",
        "mysql",
    }:
        from marivo.analysis.operators.attribution_contracts import (
            AttributionSemantics,
        )

        semantics = dataset.row_contract.family_semantics
        if not isinstance(semantics, AttributionSemantics):
            raise _error("implementation_registration", run_ref)
        incoming = _decode_scalar_masks(incoming, len(semantics.axis_field_ids), run_ref)
    output_parts = tuple(
        PartWriteSpec(
            part.role,
            part.contract_id,
            part.contract_version,
            part.column_names,
        )
        for part in recipe.retained_parts
        if isinstance(part, RetainedPartSpec)
    )
    progress.phase = "storage_staging"
    artifact_ref, storage = dataset_publication.write_output(
        self,
        dataset,
        incoming,
        run_ref,
        parts=output_parts,
        independent_parts=independent_parts,
        source_key_validation=True,
    )
    return artifact_ref, storage
