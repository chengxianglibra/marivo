"""Closed comparison authority and Delta row contracts, without source execution."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from marivo.analysis.datasets.base import Dataset, MaterializedDataset
from marivo.analysis.datasets.descriptors import (
    _CORE_TOKEN,
    DatasetFamilyRowSemantics,
    DatasetRowContract,
    DatasetRowSetContract,
    _canonical_digest,
    _descriptor_payload,
)
from marivo.analysis.datasets.handles import CanonicalValue, LogicalRootHandle, _LogicalNodePayload
from marivo.analysis.operators.errors import comparison_error

DELTA_SHAPES = ("entity", "scalar", "dimension", "time", "dimension-time")


@dataclass(frozen=True, slots=True)
class WindowBucketAlignment:
    """Pair complete observation buckets by their ordinal within each series."""

    kind: Literal["window_bucket"] = field(default="window_bucket", init=False)


def window_bucket() -> WindowBucketAlignment:
    """Return the sole exact Metric comparison alignment; no arguments.

    Returns: Immutable window-bucket policy. Example: ``current.compare(old, alignment=window_bucket())``.
    Constraints: Corresponding time series must contain equal bucket counts.
    """
    return WindowBucketAlignment()


DEFAULT_ALIGNMENT = window_bucket()


class ComparisonBasisV1(BaseModel):
    """Persisted compatibility facts separate from current public row semantics."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    membership_digest: str
    selection_digests: tuple[str, ...] = ()
    sampling_definition: tuple[str, ...] = ()
    observation_scope: tuple[str, str] | None = None
    reference_axis: str | None = None

    def to_json(self) -> str:
        return self.model_dump_json()


def decode_comparison_basis(payload: str) -> ComparisonBasisV1:
    if len(payload.encode("utf-8")) > 1_048_576:
        raise comparison_error("bounded canonical comparison authority", "oversized authority")
    try:
        result = ComparisonBasisV1.model_validate_json(payload)
    except (ValueError, ValidationError):
        raise comparison_error(
            "closed complete comparison authority", "invalid authority encoding"
        ) from None
    hashes = (*result.selection_digests, *result.sampling_definition)
    if re.fullmatch(r"ds_[a-f0-9]{64}", result.membership_digest) is None or any(
        re.fullmatch(r"[a-f0-9]{64}", value) is None for value in hashes
    ):
        raise comparison_error("exact comparison authority digests", "invalid authority digest")
    if result.observation_scope is not None:
        from marivo._temporal import _new_time_scope

        try:
            _new_time_scope(start=result.observation_scope[0], end=result.observation_scope[1])
        except (ValueError, TypeError):
            raise comparison_error(
                "valid bounded comparison observation window", "invalid scope authority"
            ) from None
    if result.to_json() != payload or not result.membership_digest:
        raise comparison_error("canonical complete comparison authority", "invalid authority")
    return result


def comparison_basis(dataset: Dataset) -> str:
    """Freeze compatibility from construction authority, never display lineage."""
    from marivo.analysis.observation.contracts import (
        MetricPayload,
        PopulationPayload,
        RetainedRowsPayload,
        owner_of,
    )

    if isinstance(dataset, MaterializedDataset):
        snapshot = owner_of(dataset).comparison_basis_snapshot
        if snapshot is None:
            raise comparison_error(
                "preloaded committed comparison authority", "missing comparison snapshot"
            )
        decode_comparison_basis(snapshot)
        return snapshot
    root = dataset._root
    if not isinstance(root, LogicalRootHandle):
        raise comparison_error("logical or selected retained authority", "unknown authority")
    payload = root.payload
    if isinstance(payload, PopulationPayload):
        inherited = (
            decode_comparison_basis(comparison_basis(dataset._inputs[0]))
            if dataset._inputs
            else None
        )
        policies = () if inherited is None else inherited.sampling_definition
        if payload.sampling is not None:
            policies = (*policies, _canonical_digest(payload.sampling.identity_payload))
        return ComparisonBasisV1(
            membership_digest=dataset.definition_fingerprint, sampling_definition=policies
        ).to_json()
    if root.operator_id == "session.observe" and isinstance(payload, MetricPayload):
        population = dataset._inputs[0]
        population_basis = decode_comparison_basis(comparison_basis(population))
        definition = payload.definition
        scope = definition.time_scope
        result = ComparisonBasisV1(
            membership_digest=population.definition_fingerprint,
            sampling_definition=population_basis.sampling_definition,
            observation_scope=None
            if scope is None
            else (scope.start.isoformat(), scope.end.isoformat()),
            reference_axis=None
            if definition.reference_axis is None
            else definition.reference_axis.ref.path,
        )
    elif dataset._inputs:
        result = decode_comparison_basis(comparison_basis(dataset._inputs[0]))
    else:
        raise comparison_error(
            "Metric construction compatibility snapshot", "missing observation authority"
        )
    if isinstance(payload, (MetricPayload, RetainedRowsPayload)):
        selection: CanonicalValue = None
        if payload.predicate is not None:
            selection = ("where", payload.predicate.identity_payload())
        elif payload.limit_count is not None:
            selection = (
                "limit",
                payload.limit_count,
                _descriptor_payload(dataset.row_set_contract.ordering),
            )
        if selection is not None:
            result = result.model_copy(
                update={
                    "selection_digests": (*result.selection_digests, _canonical_digest(selection))
                }
            )
    return result.to_json()


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class DeltaSemantics(DatasetFamilyRowSemantics, _token=_CORE_TOKEN):
    """Complete one-Metric Delta meaning independent of live semantic catalogs."""

    metric_ref: str
    metric_unit: str | None
    numeric_type: str
    exact_empty_zero: bool
    current_time_field_name: str | None
    baseline_time_field_name: str | None
    kind: Literal["delta/metric@v1"] = field(default="delta/metric@v1", init=False)


@dataclass(frozen=True, slots=True, repr=False)
class CompareSpecV1:
    current_row: DatasetRowContract
    current_rows: DatasetRowSetContract
    baseline_row: DatasetRowContract
    baseline_rows: DatasetRowSetContract
    output_row: DatasetRowContract
    output_rows: DatasetRowSetContract
    current_metric_name: str
    baseline_metric_name: str
    promoted_type: str
    exact_empty_zero: bool
    current_basis: str
    baseline_basis: str

    def identity_payload(self) -> CanonicalValue:
        return (
            "compare/metric@v1",
            _descriptor_payload(self.current_row),
            _descriptor_payload(self.current_rows),
            _descriptor_payload(self.baseline_row),
            _descriptor_payload(self.baseline_rows),
            _descriptor_payload(self.output_row),
            _descriptor_payload(self.output_rows),
            self.current_metric_name,
            self.baseline_metric_name,
            self.promoted_type,
            self.exact_empty_zero,
            self.current_basis,
            self.baseline_basis,
        )


@dataclass(frozen=True, slots=True, repr=False, eq=False, kw_only=True)
class ComparePayload(_LogicalNodePayload, _token=_CORE_TOKEN):
    spec: CompareSpecV1

    @property
    def identity_payload(self) -> CanonicalValue:
        return self.spec.identity_payload()
