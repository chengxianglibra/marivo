"""Delta quality, bounded Evidence, and deterministic Finding production."""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal, localcontext
from functools import partial
from typing import Literal

import pandas as pd
import pyarrow as pa

from marivo._compat import UTC
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.evidence import _dataset_types as t
from marivo.analysis.evidence._dataset_codec import _encode, finding_identity, finding_set_digest
from marivo.analysis.evidence._dataset_reads import CoordinateRule, FindingRegistration
from marivo.analysis.materialization.contracts import (
    ArtifactDescriptor,
    DeltaEvidenceSummary,
    canonical_json,
    invalid,
)
from marivo.analysis.operators.contracts import DeltaSemantics
from marivo.analysis.operators.row import compare_value
from marivo.analysis.refs import ArtifactRef


def delta_item_key(finding: t.Finding, *, key_field_ids: tuple[d.DatasetFieldId, ...]) -> str:
    """Canonical exact coordinate key; scalar Delta has the sole empty row key."""
    return canonical_json(
        [
            [item.field_id.value, _encode(item.value)]
            for item in finding.coordinates
            if item.field_id in key_field_ids
        ]
    )


def delta_finding_registration(descriptor: ArtifactDescriptor) -> FindingRegistration | None:
    """Recover extractor authority exclusively from the retained descriptor."""
    if descriptor.row_contract.shape_id.family_id != "delta":
        return None
    semantics = descriptor.row_contract.family_semantics
    if not isinstance(semantics, DeltaSemantics):
        raise invalid("missing exact Delta Finding semantics")
    contract = descriptor.dataset_materialization_contract
    fields = {item.field_id: item for item in descriptor.realized_schema.columns}
    coordinates: list[CoordinateRule] = []
    if descriptor.row_contract.shape_id.local_shape_id == "entity":
        return None
    for field_id in descriptor.row_contract.key_field_ids:
        field = fields[field_id]
        kind: Literal["comparison_ordinal", "dimension"] = (
            "comparison_ordinal" if field.name == "comparison_ordinal" else "dimension"
        )
        coordinates.append(CoordinateRule(field, kind))
    for name in (semantics.current_time_field_name, semantics.baseline_time_field_name):
        if name is not None:
            field = next(field for field in fields.values() if field.name == name)
            coordinates.append(CoordinateRule(field, "time"))
    return FindingRegistration(
        producer_id=contract.producer_id,
        extractor_contract_id=contract.finding_extractor_id,
        extractor_contract_version=str(contract.finding_extractor_version),
        shape_id=descriptor.row_contract.shape_id,
        finding_type="delta",
        subject=t.MetricFindingSubjectV1(metric=d._metric_identity_from_key(semantics.metric_ref)),
        coordinates=tuple(coordinates),
        source_artifact_refs=tuple(
            ArtifactRef(ref=ref)
            for ref in dict.fromkeys(
                ref
                for operand in descriptor.comparison_inputs
                for ref in operand.source_artifact_refs
            )
        ),
        source_fields=tuple(
            field.field_id
            for field in descriptor.realized_schema.columns
            if field.name in ("current_value", "baseline_value", "delta", "relative_delta")
        ),
        canonical_item_key=partial(
            delta_item_key, key_field_ids=descriptor.row_contract.key_field_ids
        ),
    )


def _scalar(value: object) -> t.Scalar:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, (str, int, float, bool, Decimal, date, datetime)):
        return value
    # Arrow-backed integer and boolean cells retain exact Python scalar identity.
    import numpy as np

    if isinstance(value, np.generic):
        return _scalar(value.item())
    raise invalid("unsupported exact Delta output scalar")


def _number(value: t.Scalar, numeric_type: str) -> t.Number:
    if not isinstance(value, (int, float, Decimal)) or isinstance(value, bool):
        raise invalid("Delta arithmetic requires a finite promoted number")
    if (isinstance(value, float) and not math.isfinite(value)) or (
        isinstance(value, Decimal) and not value.is_finite()
    ):
        raise invalid("non-finite Delta output number")
    if numeric_type == "int64" and (type(value) is not int or not -(2**63) <= value < 2**63):
        raise invalid("Delta integer promotion or overflow mismatch")
    if numeric_type == "float64" and type(value) is not float:
        raise invalid("Delta floating promotion mismatch")
    if numeric_type.startswith("decimal") and type(value) is not Decimal:
        raise invalid("Delta decimal promotion mismatch")
    if isinstance(value, Decimal):
        exponent = value.as_tuple().exponent
        if not isinstance(exponent, int):
            raise invalid("non-finite Delta decimal exponent")
        scale = max(0, -exponent)
        precision = max(len(value.as_tuple().digits) + max(exponent, 0), scale)
        if precision > 38 or scale > 38:
            raise invalid("Delta decimal exceeds its actual precision or scale bound")
    return value


def _difference(current: t.Number, baseline: t.Number) -> t.Number:
    if isinstance(current, Decimal) and isinstance(baseline, Decimal):
        return current - baseline
    if type(current) is int and type(baseline) is int:
        return current - baseline
    if type(current) is float and type(baseline) is float:
        return current - baseline
    raise invalid("Delta values do not share one lossless promoted type")


def _magnitude(value: t.Number) -> t.Number:
    if isinstance(value, Decimal):
        return value.copy_abs()
    return -value if value < 0 else value


def _ratio(delta: t.Number, baseline: t.Number) -> float | None:
    try:
        projected_delta = float(delta)
        projected_baseline = float(baseline)
        if (
            not math.isfinite(projected_delta)
            or not math.isfinite(projected_baseline)
            or projected_baseline == 0
        ):
            return None
        value = projected_delta / abs(projected_baseline)
    except (OverflowError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _rows(
    rows: pd.DataFrame | Iterable[pa.RecordBatch],
    expected: tuple[str, ...],
    ignored: frozenset[str],
) -> Iterator[dict[str, t.Scalar]]:
    if isinstance(rows, pd.DataFrame):
        if tuple(rows.columns) != expected:
            raise invalid("Delta publication schema differs from its exact finalized schema")
        for values in rows.itertuples(index=False, name=None):
            yield {
                name: None if name in ignored else _scalar(value)
                for name, value in zip(expected, values, strict=True)
            }
        return
    for batch in rows:
        if not isinstance(batch, pa.RecordBatch):
            raise invalid("Delta publication requires complete Arrow record batches")
        if tuple(batch.schema.names) != expected:
            raise invalid("Delta publication batch differs from its exact finalized schema")
        columns = [
            [None] * batch.num_rows if name in ignored else column.to_pylist()
            for name, column in zip(expected, batch.columns, strict=True)
        ]
        for values in zip(*columns, strict=True):
            yield {name: _scalar(value) for name, value in zip(expected, values, strict=True)}


def _compare_findings(left: t.Finding, right: t.Finding, keys: tuple[d.DatasetFieldId, ...]) -> int:
    if not isinstance(left.value, t.DeltaFindingValueV1) or not isinstance(
        right.value, t.DeltaFindingValueV1
    ):
        raise invalid("invalid Delta Finding candidate")
    magnitude = compare_value(_magnitude(right.value.delta), _magnitude(left.value.delta))
    if magnitude:
        return magnitude
    return compare_value(
        tuple(item.value for item in left.coordinates if item.field_id in keys),
        tuple(item.value for item in right.coordinates if item.field_id in keys),
    )


def build_delta_publication(
    descriptor: ArtifactDescriptor,
    rows: pd.DataFrame | Iterable[pa.RecordBatch],
    *,
    artifact_ref: str,
    session_ref: str,
) -> tuple[ArtifactDescriptor, tuple[t.Finding, ...]]:
    """Validate complete final rows and prepare one atomic bounded publication."""
    semantics = descriptor.row_contract.family_semantics
    if not isinstance(semantics, DeltaSemantics):
        raise invalid("Delta publication requires exact Delta semantics")
    expected = tuple(field.name for field in descriptor.realized_schema.columns)
    ignored = frozenset(
        field.name
        for field in descriptor.realized_schema.columns
        if field.identity.kind == "entity_identity"
    )
    presence = dict.fromkeys(("matched", "current_only", "baseline_only"), 0)
    calculation = dict.fromkeys(("ok", "null_input", "missing_side"), 0)
    relative = dict.fromkeys(("ok", "baseline_zero", "delta_unavailable"), 0)
    registration = delta_finding_registration(descriptor)
    candidates: list[t.Finding] = []
    eligible_count = 0
    row_count = 0
    for row in _rows(rows, expected, ignored):
        row_count += 1
        coordinate_presence = row["coordinate_presence"]
        status = row["calculation_status"]
        relative_status = row["relative_delta_status"]
        if (
            coordinate_presence not in presence
            or status not in calculation
            or relative_status not in relative
        ):
            raise invalid("unregistered Delta output status")
        if (
            type(coordinate_presence) is not str
            or type(status) is not str
            or type(relative_status) is not str
        ):
            raise invalid("invalid Delta output status type")
        presence[coordinate_presence] += 1
        calculation[status] += 1
        relative[relative_status] += 1
        for name in ("current_value", "baseline_value", "delta"):
            if row[name] is not None:
                _number(row[name], semantics.numeric_type)
        if status != "ok":
            if (
                row["delta"] is not None
                or row["relative_delta"] is not None
                or relative_status != "delta_unavailable"
            ):
                raise invalid("unavailable Delta arithmetic status mismatch")
            if status == "missing_side" and coordinate_presence == "matched":
                raise invalid("matched Delta cannot be missing one side")
            if status == "missing_side" and semantics.exact_empty_zero:
                raise invalid("exact empty-set Delta cannot report a missing side")
            if status == "missing_side" and (
                (coordinate_presence == "current_only" and row["baseline_value"] is not None)
                or (coordinate_presence == "baseline_only" and row["current_value"] is not None)
            ):
                raise invalid("missing-side Delta contains a value for its absent side")
            if (
                status == "null_input"
                and coordinate_presence != "matched"
                and not semantics.exact_empty_zero
            ):
                raise invalid("one-sided Delta must report its missing side before null inputs")
            if (
                status == "null_input"
                and row["current_value"] is not None
                and row["baseline_value"] is not None
            ):
                raise invalid("null-input Delta has two available values")
            continue
        current = _number(row["current_value"], semantics.numeric_type)
        baseline = _number(row["baseline_value"], semantics.numeric_type)
        delta = _number(row["delta"], semantics.numeric_type)
        if coordinate_presence != "matched" and (
            not semantics.exact_empty_zero
            or (coordinate_presence == "current_only" and baseline != 0)
            or (coordinate_presence == "baseline_only" and current != 0)
        ):
            raise invalid("one-sided Delta violates its exact empty-set zero policy")
        with localcontext() as context:
            context.prec = 100
            if _difference(current, baseline) != delta:
                raise invalid("Delta output violates its exact subtraction equation")
        ratio = row["relative_delta"]
        relative_value: t.RelativeDeltaV1
        if relative_status == "ok":
            if baseline == 0:
                raise invalid("defined relative Delta has a zero baseline")
            ratio_number = _number(ratio, "float64")
            expected_ratio = _ratio(delta, baseline)
            if expected_ratio is None or ratio_number != expected_ratio:
                raise invalid("relative Delta output violates its float64 formula")
            relative_value = t.DefinedFindingRatioV1(value=ratio_number)
        else:
            if ratio is not None or (baseline == 0) != (relative_status == "baseline_zero"):
                raise invalid("undefined relative Delta reason mismatch")
            if relative_status == "delta_unavailable" and _ratio(delta, baseline) is not None:
                raise invalid("available relative Delta falsely reported unavailable")
            relative_value = t.UndefinedRelativeDeltaV1(
                reason="baseline_zero"
                if relative_status == "baseline_zero"
                else "delta_unavailable"
            )
        if registration is None:
            continue
        coordinates = tuple(
            t.FindingCoordinateV1(
                field_id=rule.field.field_id,
                identity=rule.field.identity,
                value=row[rule.field.name],
            )
            for rule in registration.coordinates
        )
        item = t.Finding(
            finding_id="pending",
            artifact_ref=ArtifactRef(ref=artifact_ref),
            session_id=session_ref,
            finding_type="delta",
            epistemic_kind="algebraic",
            subject=registration.subject,
            coordinates=coordinates,
            canonical_item_key="pending",
            value=t.DeltaFindingValueV1(
                coordinate_presence="matched"
                if coordinate_presence == "matched"
                else "current_only"
                if coordinate_presence == "current_only"
                else "baseline_only",
                current_value=current,
                baseline_value=baseline,
                delta=delta,
                relative_delta=relative_value,
            ),
            derivation=t.FindingDerivationV1(
                producer_id=registration.producer_id,
                extractor_contract_id=registration.extractor_contract_id,
                extractor_contract_version=registration.extractor_contract_version,
                source_artifact_refs=registration.source_artifact_refs,
                source_fields=registration.source_fields,
            ),
            committed_at=datetime(1970, 1, 1, tzinfo=UTC),
        )
        item = replace(item, canonical_item_key=registration.canonical_item_key(item))
        item = replace(item, finding_id=finding_identity(item))
        eligible_count += 1
        low, high = 0, len(candidates)
        while low < high:
            middle = (low + high) // 2
            if (
                _compare_findings(item, candidates[middle], descriptor.row_contract.key_field_ids)
                < 0
            ):
                high = middle
            else:
                low = middle + 1
        candidates.insert(low, item)
        if len(candidates) > 1000:
            candidates.pop()
    if row_count != descriptor.storage_receipt.realized_row_count:
        raise invalid("incomplete final Delta publication rows")
    findings = tuple(candidates[:1000])
    evidence = DeltaEvidenceSummary(
        tuple(presence.items()),
        tuple(calculation.items()),
        tuple(relative.items()),
        presence["matched"],
        presence["current_only"] + presence["baseline_only"],
        "lossless_signed:" + semantics.numeric_type + "@v1",
        semantics.approximation_class != "exact",
        eligible_count,
        len(findings),
        eligible_count > len(findings),
        finding_set_digest(findings),
    )
    return replace(descriptor, delta_evidence=evidence), findings
