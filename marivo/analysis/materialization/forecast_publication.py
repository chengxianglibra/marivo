"""Atomic Forecast quality, nominal prediction Findings and cold proof checks."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import replace
from datetime import date, datetime
from functools import cmp_to_key
from statistics import NormalDist

import pandas as pd
import pyarrow as pa

from marivo._compat import UTC
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.evidence import _dataset_types as t
from marivo.analysis.evidence._dataset_codec import _encode, finding_identity, finding_set_digest
from marivo.analysis.evidence._dataset_reads import CoordinateRule, FindingRegistration
from marivo.analysis.materialization.comparison_publication import _scalar
from marivo.analysis.materialization.contracts import (
    FINDING_CAP,
    ArtifactDescriptor,
    _int,
    canonical_json,
    invalid,
)
from marivo.analysis.materialization.forecast_codec import ForecastEvidenceSummary
from marivo.analysis.observation.fold_contracts import (
    BUILTIN_GRAIN_MONTHS,
    BUILTIN_GRAIN_SECONDS,
    decode_fold_authority,
)
from marivo.analysis.operators.forecast_contracts import ForecastSemantics, ForecastTrainingSummary
from marivo.analysis.operators.forecast_values import certified_coordinates
from marivo.analysis.operators.row_values import compare_value
from marivo.analysis.refs import ArtifactRef


def _semantics(descriptor: ArtifactDescriptor) -> ForecastSemantics:
    result = descriptor.row_contract.family_semantics
    if not isinstance(result, ForecastSemantics):
        raise invalid("missing Forecast model authority")
    return result


def validate_training(s: ForecastSemantics, training: ForecastTrainingSummary) -> None:
    n = training.training_row_count
    lag = s.season_length or 1 if s.model_id == "seasonal_naive@v1" else 1
    if (
        n < s.minimum_history
        or training.residual_count != n - lag
        or training.residual_df != (n - 2 if s.model_id == "drift@v1" else n - lag)
        or training.series_count < 1
        or not 0 <= training.zero_residual_series_count <= training.series_count
    ):
        raise invalid("Forecast training contradicts model degrees of freedom")
    lo, hi = training.variance_range
    if (
        not all(math.isfinite(v) for v in (lo, hi))
        or not 0 <= lo <= hi
        or (lo == 0) != (training.zero_residual_series_count > 0)
        or (hi == 0) != (training.zero_residual_series_count == training.series_count)
    ):
        raise invalid("Forecast zero-innovation or variance summary mismatch")
    authority = decode_fold_authority(s.fold_authority)
    grain, snapshot = authority.time_grain(), authority.temporal_snapshot()
    if grain is None:
        raise invalid("missing Forecast time grain")
    try:
        start, end = pd.Timestamp(training.history_start), pd.Timestamp(training.history_end)
        # Bounded cold validation proves count and endpoints without retaining history values.
        if grain.kind == "semantic" and grain.level != "day":
            if snapshot is None:
                raise invalid("missing certified Forecast calendar")
            matching = sorted(
                (
                    p
                    for p in snapshot.periods
                    if p.level_name == grain.level and start <= pd.Timestamp(p.start_date) <= end
                ),
                key=lambda p: p.start_date,
            )
            valid = (
                len(matching) == n
                and pd.Timestamp(matching[0].start_date) == start
                and pd.Timestamp(matching[-1].start_date) == end
            )
        elif grain.kind == "builtin" and grain.unit in BUILTIN_GRAIN_MONTHS:
            width = BUILTIN_GRAIN_MONTHS[str(grain.unit)] * (grain.count or 1)
            valid = (end.year - start.year) * 12 + end.month - start.month == width * (n - 1)
        else:
            width_seconds = (
                BUILTIN_GRAIN_SECONDS[str(grain.unit)] * (grain.count or 1)
                if grain.kind == "builtin"
                else 86400
            )
            valid = (end - start).total_seconds() == width_seconds * (n - 1)
        if not valid:
            raise invalid("Forecast training length contradicts its history coordinates")
        future = certified_coordinates((end,), s)
        if tuple(v.isoformat() for v in future) != training.future_coordinates:
            raise invalid("Forecast future coordinates contradict certified continuation")
        certified_coordinates((start,), replace(s, _token=d._CORE_TOKEN, horizon=1))
    except (ValueError, OverflowError, TypeError) as error:
        raise invalid("invalid retained Forecast time coordinates") from error


def validate_descriptor(descriptor: ArtifactDescriptor) -> None:
    s = _semantics(descriptor)
    evidence = descriptor.forecast_evidence
    if evidence is None or evidence.row_count != descriptor.storage_receipt.realized_row_count:
        raise invalid("missing or inconsistent Forecast Evidence")
    validate_training(s, evidence.training)
    complete = evidence.training.series_count * s.horizon
    if evidence.row_count > complete or (
        descriptor.dataset_materialization_contract.producer_id == "metric.forecast"
        and evidence.row_count != complete
    ):
        raise invalid("incomplete original Forecast horizon")
    if (
        any(part.contract_id != "population_sampling_state" for part in descriptor.retained_parts)
        or descriptor.comparison_inputs
    ):
        raise invalid("unregistered Forecast retained inputs")


def forecast_key(finding: t.Finding) -> str:
    if not isinstance(finding.value, t.ForecastPointFindingValueV1):
        raise invalid("invalid Forecast Finding type")
    return canonical_json(
        [[_encode(c.value) for c in finding.coordinates], finding.value.horizon_ordinal]
    )


def finding_registration(descriptor: ArtifactDescriptor) -> FindingRegistration:
    s = _semantics(descriptor)
    contract = descriptor.dataset_materialization_contract
    return FindingRegistration(
        producer_id=contract.producer_id,
        extractor_contract_id=contract.finding_extractor_id,
        extractor_contract_version=str(contract.finding_extractor_version),
        shape_id=descriptor.row_contract.shape_id,
        finding_type="forecast_point",
        subject=t.MetricFindingSubjectV1(
            metric=d._metric_identity_from_key(s.metric_key.removeprefix("metric:"))
        ),
        coordinates=tuple(
            CoordinateRule(f, "time" if f.role_id == "time_dimension" else "dimension")
            for f in descriptor.realized_schema.columns
            if f.role_id in ("dimension", "time_dimension")
        ),
        source_artifact_refs=(),
        source_fields=tuple(
            f.field_id
            for f in descriptor.realized_schema.columns
            if f.name in ("forecast_value", "interval_lower", "interval_upper")
        ),
        canonical_item_key=forecast_key,
    )


def validate_finding(finding: t.Finding, descriptor: ArtifactDescriptor) -> None:
    s = _semantics(descriptor)
    value = finding.value
    evidence = descriptor.forecast_evidence
    if not isinstance(value, t.ForecastPointFindingValueV1) or evidence is None:
        raise invalid("missing Forecast Finding authority")
    training = evidence.training
    if (
        value.model != s.model_id
        or value.interval_level != s.interval_level
        or value.interval_method != s.interval_method
        or value.training_row_count != training.training_row_count
        or not 1 <= value.horizon_ordinal <= s.horizon
    ):
        raise invalid("Forecast Finding model or horizon contradicts Artifact")
    time_fields = {
        f.field_id for f in descriptor.realized_schema.columns if f.role_id == "time_dimension"
    }
    time = next((c.value for c in finding.coordinates if c.field_id in time_fields), None)
    if (
        time is None
        or not isinstance(time, (date, datetime))
        or pd.Timestamp(time).isoformat() != training.future_coordinates[value.horizon_ordinal - 1]
    ):
        raise invalid("Forecast Finding future coordinate contradicts horizon ordinal")
    point, lo, hi = (
        float(value.forecast_value),
        float(value.interval_lower),
        float(value.interval_upper),
    )
    tolerance = max(math.ulp(point), math.ulp(lo), math.ulp(hi), 1e-12) * 4
    if not all(math.isfinite(v) for v in (point, lo, hi)) or not math.isclose(
        point - lo, hi - point, rel_tol=1e-10, abs_tol=tolerance
    ):
        raise invalid("Forecast interval is not finite and symmetric")
    h = value.horizon_ordinal
    factor = (
        (h - 1) // (s.season_length or 1) + 1
        if s.model_id == "seasonal_naive@v1"
        else h * (1 + h / (training.training_row_count - 1))
        if s.model_id == "drift@v1"
        else h
    )
    z = -NormalDist().inv_cdf((1 - s.interval_level) / 2)
    low_margin, high_margin = (z * math.sqrt(v * factor) for v in training.variance_range)
    margin = hi - point
    tolerance = max(math.ulp(point), math.ulp(hi), 1e-12) * 4
    if margin < low_margin - tolerance or margin > high_margin + tolerance:
        raise invalid("Forecast interval contradicts model horizon variance")


def build_forecast_publication(
    descriptor: ArtifactDescriptor,
    batches: Iterable[pa.RecordBatch],
    *,
    artifact_ref: str,
    session_ref: str,
    training: ForecastTrainingSummary | None,
) -> tuple[ArtifactDescriptor, tuple[t.Finding, ...]]:
    s = _semantics(descriptor)
    if training is None:
        raise invalid("missing original Forecast training proof")
    validate_training(s, training)
    registration = finding_registration(descriptor)
    count = descriptor.storage_receipt.realized_row_count
    provisional = replace(
        descriptor,
        forecast_evidence=ForecastEvidenceSummary(
            count, min(FINDING_CAP, count), finding_set_digest(()), training
        ),
    )
    validate_descriptor(provisional)
    candidates: list[t.Finding] = []
    keys: set[str] = set()
    series: dict[str, set[int]] = {}
    for batch in batches:
        for raw in batch.to_pylist():
            row = {str(k): _scalar(v) for k, v in raw.items()}
            item = t.Finding(
                finding_id="pending",
                artifact_ref=ArtifactRef(ref=artifact_ref),
                session_id=session_ref,
                finding_type="forecast_point",
                epistemic_kind="predicted",
                subject=registration.subject,
                coordinates=tuple(
                    t.FindingCoordinateV1(
                        field_id=r.field.field_id,
                        identity=r.field.identity,
                        value=row[r.field.name],
                    )
                    for r in registration.coordinates
                ),
                canonical_item_key="pending",
                value=t.ForecastPointFindingValueV1(
                    model=s.model_id,
                    interval_level=s.interval_level,
                    horizon_ordinal=_int(row["horizon_ordinal"], minimum=1),
                    forecast_value=_float_value(row["forecast_value"]),
                    interval_lower=_float_value(row["interval_lower"]),
                    interval_upper=_float_value(row["interval_upper"]),
                    training_row_count=_int(row["training_row_count"], minimum=2),
                ),
                derivation=t.FindingDerivationV1(
                    producer_id=registration.producer_id,
                    extractor_contract_id=registration.extractor_contract_id,
                    extractor_contract_version=registration.extractor_contract_version,
                    source_artifact_refs=(),
                    source_fields=registration.source_fields,
                ),
                committed_at=datetime(1970, 1, 1, tzinfo=UTC),
            )
            item = replace(item, canonical_item_key=forecast_key(item))
            item = replace(item, finding_id=finding_identity(item))
            validate_finding(item, provisional)
            if item.canonical_item_key in keys:
                raise invalid("duplicate Forecast coordinate")
            keys.add(item.canonical_item_key)
            series_key = canonical_json(
                [
                    _encode(row[r.field.name])
                    for r in registration.coordinates
                    if r.kind == "dimension"
                ]
            )
            series.setdefault(series_key, set()).add(_int(row["horizon_ordinal"], minimum=1))
            candidates.append(item)
            if len(candidates) >= 2 * FINDING_CAP:
                candidates.sort(key=cmp_to_key(_finding_order))
                del candidates[FINDING_CAP:]
    if len(keys) != count:
        raise invalid("incomplete Forecast publication scan")
    if descriptor.dataset_materialization_contract.producer_id == "metric.forecast" and (
        len(series) != training.series_count
        or any(h != set(range(1, s.horizon + 1)) for h in series.values())
    ):
        raise invalid("incomplete per-series Forecast horizon")
    # Typed Dimension order is independent of a later ranked row order.
    candidates.sort(key=cmp_to_key(_finding_order))
    findings = tuple(candidates[:FINDING_CAP])
    return replace(
        descriptor,
        forecast_evidence=ForecastEvidenceSummary(
            count, len(findings), finding_set_digest(findings), training
        ),
    ), findings


def _float_value(value: t.Scalar) -> float:
    if type(value) is not float or not math.isfinite(value):
        raise invalid("invalid Forecast generated float")
    return value


def _finding_order(left: t.Finding, right: t.Finding) -> int:
    for a, b in zip(left.coordinates, right.coordinates, strict=True):
        compared = compare_value(a.value, b.value)
        if compared:
            return compared
    return 0
