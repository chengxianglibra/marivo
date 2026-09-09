"""Native Entity MAD screening and identity-free scalar publication proofs."""

from __future__ import annotations

import math
from collections.abc import Mapping

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir

from marivo.analysis.compiler.attribution import _close, _finite, _numeric
from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.compiler.nodes import CompiledValidation
from marivo.analysis.datasets.descriptors import (
    DatasetRowContract,
    _canonical_digest,
    _EntityFieldIdentity,
)
from marivo.analysis.operators.candidate_contracts import (
    CandidateDefinition,
    CandidateSearchSummary,
    CandidateSpecV1,
    EntityCandidateEvaluationSummary,
)

_REASON = "entity_mad_threshold_met"


def _identity(
    row: DatasetRowContract, table: ir.Table
) -> tuple[ir.StructValue, _EntityFieldIdentity]:
    fields = tuple(field for field in row.schema.columns if field.role_id == "entity_identity")
    if len(fields) != 1 or not isinstance(fields[0].identity, _EntityFieldIdentity):
        raise compilation_error("one exact Entity identity field", "invalid Candidate identity")
    field = fields[0]
    identity = field.identity
    assert isinstance(identity, _EntityFieldIdentity)
    value = table[field.name]
    if not isinstance(value, ir.StructValue) or tuple(value.type().names) != tuple(
        name for name, _ in identity.identity_signature
    ):
        raise compilation_error(
            "the declared ordered identity struct", "changed Candidate identity"
        )
    if any(
        value[name].type() != dt.dtype(kind)
        and not (kind == "decimal" and isinstance(value[name].type(), dt.Decimal))
        and not (
            kind == "timestamp"
            and isinstance(value[name].type(), dt.Timestamp)
            and value[name].type().timezone is None
        )
        for name, kind in identity.identity_signature
    ):
        raise compilation_error(
            "exact declared identity component types", "changed Candidate identity"
        )
    return value, identity


def entity_item_id(
    table: ir.Table, row: DatasetRowContract, definition: CandidateDefinition
) -> ir.StringValue:
    """Encode identity entirely in DuckDB using an independently versioned digest."""
    identity, metadata = _identity(row, table)
    normalized: dict[str, ir.Value] = {}
    for name, _ in metadata.identity_signature:
        component = identity[name]
        normalized[name] = (
            (component == 0).ifelse(ibis.literal(0).cast(component.type()), component)
            if isinstance(component, ir.FloatingValue)
            else component
        )
    encoded = ibis.struct(normalized).cast("json").cast("string")
    prefix = (
        "candidate_entity_item@v1:"
        + _canonical_digest(definition.identity_payload())
        + ":"
        + _canonical_digest(metadata.identity_signature)
        + ":"
    )
    result = ibis.literal("sha256:") + (ibis.literal(prefix) + encoded).hexdigest("sha256")
    if not isinstance(result, ir.StringValue):
        raise compilation_error("native SHA-256 identity encoding", "invalid digest expression")
    return result


def _check(name: str, bad: ir.Table) -> CompiledValidation:
    return CompiledValidation(
        name,
        bad.aggregate(violations=bad.count()),
        expected="at least three finite non-null observations, positive finite scale and unique Entity identities",
        repair="Repair duplicate or invalid identities and non-finite values, or select a nonconstant Metric population with at least three non-null values.",
    )


def _identity_checks(table: ir.Table, row: DatasetRowContract) -> tuple[CompiledValidation, ...]:
    identity, metadata = _identity(row, table)
    valid = identity.notnull()
    for name, _ in metadata.identity_signature:
        valid = valid & _finite(identity[name])
    counts = table.group_by(entity_identity=identity).aggregate(__count=table.count())
    return (
        _check("candidate.entity_identity_valid", table.filter(~valid.fill_null(False))),
        _check("candidate.entity_identity_unique", counts.filter(counts.__count > 1)),
    )


def lower_entity_candidate(
    table: ir.Table, spec: CandidateSpecV1
) -> tuple[ir.Table, tuple[CompiledValidation, ...], ir.Table]:
    """Score one frozen Entity Metric relation without transferring any identity."""
    definition = spec.definition
    if definition.objective != "entity_outliers" or definition.method_id != "entity_mad@v1":
        raise compilation_error("entity_mad@v1", "unsupported Entity Candidate method")
    original = table[spec.metric_name]
    numeric = original.cast("float64")
    representable = _finite(numeric)
    if original.type().is_integer():
        representable = representable & (numeric.try_cast(original.type()) == original)
    elif original.type().is_decimal():
        representable = representable & (
            numeric.cast("string").try_cast(original.type()) == original
        )
    values = table.select("entity_identity", __value=numeric)
    totals = values.aggregate(
        input_row_count=values.count(),
        non_null_value_count=values.__value.count(),
        null_value_count=values.__value.isnull().cast("int64").sum().fill_null(0),
        center=values.__value.median(),
    )
    non_null = values.filter(values.__value.notnull())
    centered = non_null.cross_join(totals).mutate(
        __deviation=lambda t: t.__value - t.center,
    )
    centered = centered.mutate(__absolute=centered.__deviation.abs())
    scales = centered.aggregate(
        __mad=centered.__absolute.median(), __mean=centered.__absolute.mean()
    )
    stats = totals.cross_join(scales)
    stats = stats.mutate(
        scale=(stats.__mad > 0).ifelse(1.4826 * stats.__mad, stats.__mean),
        scale_method=(stats.__mad > 0).ifelse("mad", "mean_absolute_deviation"),
    )
    scored = non_null.cross_join(stats).mutate(
        signed_deviation=lambda t: t.__value - t.center,
    )
    scored = scored.mutate(score=scored.signed_deviation.abs() / scored.scale)
    scored = scored.mutate(item_id=entity_item_id(scored, spec.output_row, definition))
    identities = values.mutate(item_id=entity_item_id(values, spec.output_row, definition))
    id_counts = identities.group_by("item_id").aggregate(__count=identities.count())
    checks = (
        *_identity_checks(table, spec.input_row),
        _check("candidate.entity_input_finite", non_null.filter(~_finite(non_null.__value))),
        _check(
            "candidate.entity_input_representable",
            table.filter(original.notnull() & ~representable.fill_null(False)),
        ),
        _check(
            "candidate.entity_scale_valid",
            stats.filter(
                (stats.non_null_value_count < 3)
                | ~_finite(stats.center)
                | ~_finite(stats.scale)
                | (stats.scale <= 0)
            ),
        ),
        _check(
            "candidate.entity_scores_finite",
            scored.filter(~(_finite(scored.signed_deviation) & _finite(scored.score))),
        ),
        _check("candidate.entity_item_id_unique", id_counts.filter(id_counts.__count > 1)),
    )
    hits = scored.filter(scored.score >= definition.threshold)
    emitted = hits.order_by(ibis.desc("score"), "entity_identity", "item_id").limit(
        definition.limit
    )
    result = emitted.select(
        "item_id",
        "score",
        reason_codes=ibis.literal([_REASON], type="array<string>"),
        entity_identity=emitted.entity_identity,
        observed_value=emitted.__value,
        baseline_value=emitted.center,
        signed_deviation=emitted.signed_deviation,
        scale_method=emitted.scale_method,
        direction=(emitted.signed_deviation > 0).ifelse("high", "low"),
    )
    candidates = hits.aggregate(
        pre_limit_candidate_count=hits.count(),
        score_min=hits.score.min(),
        score_max=hits.score.max(),
    )
    final = emitted.aggregate(emitted_candidate_count=emitted.count())
    proof = (
        stats.select(
            "input_row_count",
            "non_null_value_count",
            "null_value_count",
            "center",
            "scale",
            "scale_method",
        )
        .cross_join(candidates)
        .cross_join(final)
    )
    return result, checks, proof


def entity_candidate_output_proof(
    table: ir.Table,
    row: DatasetRowContract,
    definition: CandidateDefinition,
    evaluation: EntityCandidateEvaluationSummary | None = None,
) -> ir.Table:
    """Assert retained row integrity in the engine and return one identity-free count."""
    valid = (
        _finite(table.score)
        & (table.score >= definition.threshold)
        & _finite(table.observed_value)
        & _finite(table.baseline_value)
        & _finite(table.signed_deviation)
        & (table.item_id == entity_item_id(table, row, definition))
        & (table.reason_codes == ibis.literal([_REASON], type="array<string>"))
        & table.scale_method.isin(("mad", "mean_absolute_deviation"))
        & (table.direction == (table.signed_deviation > 0).ifelse("high", "low"))
        & _close(table.signed_deviation, table.observed_value - table.baseline_value)
    )
    if evaluation is not None:
        valid = (
            valid
            & (table.baseline_value == evaluation.center)
            & (table.scale_method == evaluation.scale_method)
            & _close(table.score, _numeric(table.signed_deviation).abs() / evaluation.scale)
        )
    checks = (
        *_identity_checks(table, row),
        _check("candidate.entity_rows_valid", table.filter(~valid.fill_null(False))),
    )
    counts = table.group_by("item_id").aggregate(__count=table.count())
    checks = (*checks, _check("candidate.entity_ids_unique", counts.filter(counts.__count > 1)))
    counts_relation = ibis.union(*(check.expression for check in checks))
    return counts_relation.aggregate(violations=counts_relation.violations.sum())


def decode_candidate_proof(
    values: Mapping[str, object], definition: CandidateDefinition
) -> CandidateSearchSummary:
    """Decode exactly one bounded scalar record; no identity value enters Python."""
    names = {
        "input_row_count",
        "non_null_value_count",
        "null_value_count",
        "center",
        "scale",
        "scale_method",
        "pre_limit_candidate_count",
        "emitted_candidate_count",
        "score_min",
        "score_max",
    }
    if set(values) != names:
        raise compilation_error("exact Entity evaluation proof fields", "invalid scalar proof")

    def count(name: str) -> int:
        value = values[name]
        if type(value) is not int or value < 0:
            raise compilation_error("non-negative scalar proof count", "invalid scalar proof")
        return value

    def finite(name: str) -> float:
        value = values[name]
        if type(value) not in (int, float):
            raise compilation_error("finite scalar proof value", "invalid scalar proof")
        assert isinstance(value, (int, float))
        result = float(value)
        if not math.isfinite(result):
            raise compilation_error("finite scalar proof value", "invalid scalar proof")
        return result

    input_count, complete, nulls = (
        count("input_row_count"),
        count("non_null_value_count"),
        count("null_value_count"),
    )
    before, emitted = count("pre_limit_candidate_count"), count("emitted_candidate_count")
    center, scale = finite("center"), finite("scale")
    method = values["scale_method"]
    if (
        complete < 3
        or input_count != complete + nulls
        or scale <= 0
        or method not in ("mad", "mean_absolute_deviation")
        or before > complete
        or emitted != min(before, definition.limit)
        or (emitted == 0 and (values["score_min"] is not None or values["score_max"] is not None))
    ):
        raise compilation_error(
            "consistent Entity evaluation counts and scale", "invalid scalar proof"
        )
    score_range = (finite("score_min"), finite("score_max")) if emitted else None
    if score_range is not None and not definition.threshold <= score_range[0] <= score_range[1]:
        raise compilation_error(
            "ordered finite emitted scores meeting threshold", "invalid scalar proof"
        )
    return CandidateSearchSummary(
        definition,
        EntityCandidateEvaluationSummary(
            input_row_count=input_count,
            non_null_value_count=complete,
            null_value_count=nulls,
            center=center,
            scale=scale,
            scale_method="mad" if method == "mad" else "mean_absolute_deviation",
            pre_limit_candidate_count=before,
            emitted_candidate_count=emitted,
            score_range=score_range,
            reason_counts=((_REASON, before),),
        ),
    )
