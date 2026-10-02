"""Private first-route execution from pure definition to committed Dataset authority."""

from __future__ import annotations

import sys
from collections.abc import Iterator, Mapping
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
from marivo.analysis.materialization import dataset_publication
from marivo.analysis.materialization.contracts import (
    ArtifactRecord,
    StorageReceipt,
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
        _collect_event_proofs(
            proof_backend, proof_recipe, source_boundary, evidence, boundary_validations, run_ref
        )
        _collect_search_proofs(proof_backend, proof_recipe, source_boundary, evidence, run_ref)
        evidence.validations.extend(
            (
                f"source.{source_boundary.output}.{name}" if len(source_steps) > 1 else name,
                value,
            )
            for name, value in boundary_validations
        )
    return prepared


def _collect_event_proofs(
    proof_backend: ExecutionAdapter,
    proof_recipe: CompiledDataset,
    source_boundary: SourceStep,
    evidence: ExecutionEvidence,
    boundary_validations: list[tuple[str, int]],
    run_ref: str,
) -> None:
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


def _collect_search_proofs(
    proof_backend: ExecutionAdapter,
    proof_recipe: CompiledDataset,
    source_boundary: SourceStep,
    evidence: ExecutionEvidence,
    run_ref: str,
) -> None:
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


def _validate_candidate_output(
    dataset: LogicalDataset,
    backend: ExecutionAdapter,
    recipe: CompiledDataset,
    run_ref: str,
    evidence: ExecutionEvidence,
) -> None:
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
        checked = backend.read_table(backend.prepare(output_proof, role="candidate.entity_output"))
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
        checked_driver = backend.read_table(
            backend.prepare(driver_proof, role="candidate.driver_output")
        )
        if (
            checked_driver.column_names != ["violations"]
            or checked_driver.num_rows != 1
            or checked_driver["violations"][0].as_py() != 0
        ):
            raise _error("output_validation", run_ref)
        evidence.validations.append(("candidate.driver_output", 0))


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
    _validate_candidate_output(dataset, current_backend, recipe, run_ref, evidence)
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
