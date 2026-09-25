"""Private first-route execution from pure definition to committed Dataset authority."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from marivo.analysis.datasets.base import MaterializedDataset
from marivo.analysis.evidence import _dataset_reads
from marivo.analysis.evidence._dataset_types import (
    ArtifactDigest,
    ArtifactRevalidation,
    Finding,
    FindingPage,
)
from marivo.analysis.materialization import recovery
from marivo.analysis.materialization.contracts import (
    ArtifactRecord,
)
from marivo.analysis.materialization.errors import _execution_error as _error
from marivo.analysis.materialization.reads import (
    read_preview,
    read_primary,
)
from marivo.analysis.materialization.storage import (
    ReadPolicy,
)
from marivo.analysis.operators.association_contracts import (
    selection_description,
)
from marivo.analysis.operators.candidate_contracts import (
    EntityCandidateEvaluationSummary,
)
from marivo.analysis.operators.driver_contracts import (
    DriverCandidateEvaluationSummary,
)
from marivo.analysis.refs import ArtifactRef

if TYPE_CHECKING:
    from marivo.analysis.materialization.admission import DatasetRuntime

_PREVIEW_MAX_OUTPUT_BYTES = 8192


def revalidate(self: DatasetRuntime, reference: str | ArtifactRef) -> ArtifactRevalidation:
    """Explicitly inspect metadata, all committed storage and complete Evidence."""
    from marivo.analysis.materialization.inspection import revalidate

    return revalidate(self.store, reference)


def recover(self: DatasetRuntime, record: ArtifactRecord) -> MaterializedDataset:
    return recovery.recover_dataset(
        record,
        session_ref=self.session_ref,
        store_id=self.store.store_id,
        action_port=self,
        source_context=self._source_context,
    )


def selected(self: DatasetRuntime, dataset: MaterializedDataset) -> ArtifactRecord:
    if (
        dataset._owner.store_id != self.store.store_id
        or dataset._owner.session_id != self.session_ref
    ):
        raise _error("authority_resolution")
    record = self.store.artifact(dataset.state.artifact_ref.ref)
    if record is None or record.descriptor.definition_fingerprint != dataset.definition_fingerprint:
        raise _error("presentation")
    return record


def show(
    self: DatasetRuntime,
    dataset: MaterializedDataset,
    *,
    max_output_bytes: int | None = None,
    policy: ReadPolicy,
) -> None:
    if max_output_bytes is not None and (type(max_output_bytes) is not int or max_output_bytes < 1):
        raise _error("presentation")
    record = self._selected(dataset)
    table = read_preview(
        project_root=self.store.project_root,
        receipt=record.descriptor.storage_receipt,
        row_contract=dataset.row_contract,
        row_set_contract=dataset.row_set_contract,
        policy=policy,
    )
    limit = min(
        _PREVIEW_MAX_OUTPUT_BYTES,
        _PREVIEW_MAX_OUTPUT_BYTES if max_output_bytes is None else max_output_bytes,
    )
    header = f"<{type(dataset).__name__} ref={record.artifact_ref} rows={record.descriptor.storage_receipt.realized_row_count}>"
    lines = [
        header,
        f"Preview: {table.num_rows} of {record.descriptor.storage_receipt.realized_row_count} rows (maximum {policy.preview_rows})",
        " | ".join(table.column_names),
    ]
    from marivo.analysis.observation.distribution_contracts import distribution_part_authorities
    from marivo.analysis.observation.fold_contracts import decode_fold_authority

    quantiles = tuple(
        item.distribution.quantile
        for _, item in distribution_part_authorities(dataset.row_contract)
        if item.distribution is not None
    )
    if record.descriptor.attribution_fold_authority is not None:
        quantiles = tuple(
            item.distribution.quantile
            for payload in record.descriptor.attribution_fold_authority
            for item in decode_fold_authority(payload).metrics
            if item.distribution is not None
        )
    lines[1:1] = [
        f"Percentile: method={quantile.method}; q={quantile.q}; "
        + (
            "semantic approximation; error_bound=unknown"
            if quantile.method == "duckdb_tdigest@v1"
            else "exact linear interpolation"
        )
        for quantile in dict.fromkeys(quantiles)
    ]
    if record.descriptor.candidate_evidence is not None:
        from marivo.analysis.operators.discovery import _contract_facts as candidate_facts

        candidate_evidence = record.descriptor.candidate_evidence
        evaluation = candidate_evidence.evaluation
        lines.extend(f"Discovery {name}: {value}" for name, value in candidate_facts(dataset))
        if isinstance(evaluation, EntityCandidateEvaluationSummary):
            lines.append(
                f"Evaluation: input_rows={evaluation.input_row_count}; non_null_values={evaluation.non_null_value_count}; null_values={evaluation.null_value_count}; center={evaluation.center}; scale={evaluation.scale}; scale_method={evaluation.scale_method}"
            )
        elif isinstance(evaluation, DriverCandidateEvaluationSummary):
            lines.append(
                f"Evaluation: input_rows={evaluation.input_row_count}; scopes={evaluation.scope_count}; evaluated_axes={evaluation.evaluated_axis_count}/{evaluation.searched_axis_count}; zero_contribution_axes={evaluation.zero_contribution_axis_count}"
            )
        else:
            lines.append(
                f"Evaluation: input_rows={evaluation.input_row_count}; evaluated_series={evaluation.evaluated_series_count}/{evaluation.series_count}; evaluated_units={evaluation.evaluated_unit_count}/{evaluation.searched_unit_count}"
            )
        lines.append(
            f"Candidates: qualifying={evaluation.pre_limit_candidate_count}; discovery_output={evaluation.emitted_candidate_count}; current_rows={candidate_evidence.row_count}"
        )
    if record.descriptor.forecast_evidence is not None:
        from marivo.analysis.operators.forecast import _contract_facts

        forecast_evidence = record.descriptor.forecast_evidence
        lines.extend(f"Forecast {name}: {value}" for name, value in _contract_facts(dataset))
        lines.append(
            f"Training: series={forecast_evidence.training.series_count}; periods={forecast_evidence.training.training_row_count}; residual_df={forecast_evidence.training.residual_df}; zero_residual_series={forecast_evidence.training.zero_residual_series_count}"
        )
    if record.descriptor.association_evidence is not None:
        from marivo.analysis.operators.association_contracts import AssociationSemantics

        meaning = dataset.row_contract.family_semantics
        evidence = record.descriptor.association_evidence
        if isinstance(meaning, AssociationSemantics):
            lines[1:1] = [
                f"Association: method={meaning.method}; observation_unit={meaning.input_shape}",
                f"Approximation: {', '.join(dict.fromkeys(meaning.approximations))}",
                f"Search: pairs={evidence.searched_pair_count}; lags={evidence.searched_lag_count}; series={evidence.searched_series_count}; candidates={evidence.original_candidate_count}",
                f"Lag scope: first={meaning.lag_offsets[0]}; last={meaning.lag_offsets[-1]}",
                f"Complete pairs: {evidence.complete_pair_range}; null loss: {evidence.null_pair_range}",
                f"Selection: {selection_description()}; rule={evidence.selection_rule_id}",
                f"Findings: eligible={evidence.eligible_finding_count}; emitted={evidence.emitted_finding_count}; truncated={evidence.finding_truncated}",
                "Descriptive and exploratory; no significance or causal claim. Positive lag describes coordinate order only.",
            ]
    identities = {
        field.name
        for field in dataset.schema.columns
        if field.role_id in ("entity_identity", "member")
    }
    duration_fields = tuple(
        field.name for field in dataset.schema.columns if field.logical_type_id == "duration"
    )
    if duration_fields:
        lines.insert(1, "Durations (microseconds): " + ", ".join(duration_fields))
    for index in range(table.num_rows):
        cells = []
        for name in table.column_names:
            value: object = table[name][index].as_py()
            cells.append("<identity>" if name in identities else repr(value)[:256])
        lines.append(" | ".join(cells))
    output = "\n".join(lines).encode("utf-8")
    print(output[: max(0, limit - 1)].decode("utf-8", errors="ignore"))


def to_pandas(
    self: DatasetRuntime, dataset: MaterializedDataset, *, policy: ReadPolicy
) -> pd.DataFrame:
    record = self._selected(dataset)
    return read_primary(
        project_root=self.store.project_root,
        receipt=record.descriptor.storage_receipt,
        row_contract=dataset.row_contract,
        row_set_contract=dataset.row_set_contract,
        policy=policy,
    )


def evidence_digest(self: DatasetRuntime, dataset: MaterializedDataset) -> ArtifactDigest:
    return _dataset_reads.evidence_digest(self._selected(dataset))


def findings(
    self: DatasetRuntime, dataset: MaterializedDataset, *, limit: int, cursor: str | None
) -> FindingPage:
    self._validate_reader_owner(dataset)
    with self.store._read() as conn:
        record = self.store._artifact(conn, dataset.state.artifact_ref.ref)
        if record is None:
            raise _error("presentation")
        return _dataset_reads.findings(conn, record, limit=limit, cursor=cursor)


def finding(self: DatasetRuntime, dataset: MaterializedDataset, finding_id: str) -> Finding:
    self._validate_reader_owner(dataset)
    with self.store._read() as conn:
        record = self.store._artifact(conn, dataset.state.artifact_ref.ref)
        if record is None:
            raise _error("presentation")
        return _dataset_reads.finding(conn, record, finding_id)


def validate_reader_owner(self: DatasetRuntime, dataset: MaterializedDataset) -> None:
    if (
        dataset._owner.store_id != self.store.store_id
        or dataset._owner.session_id != self.session_ref
    ):
        raise _error("authority_resolution")
