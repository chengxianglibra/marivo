"""Native exact driver-axis concentration and identity-free publication proofs."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from datetime import datetime

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.attribution import (
    _close,
    _finite,
    _group,
    _numeric,
    _value,
    exact_partition_fold,
    prepare_exact_partition,
)
from marivo.analysis.compiler.driver_numeric import exact_float_sum
from marivo.analysis.compiler.entity_candidate import _identity
from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.compiler.nodes import CompiledValidation
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.operators.candidate_contracts import CandidateSearchSummary
from marivo.analysis.operators.driver_contracts import (
    DriverCandidateDefinition,
    DriverCandidateEvaluationSummary,
    DriverCandidateSpecV1,
)

_REASON = "axis_concentration"


def _check(name: str, invalid: ir.Table) -> CompiledValidation:
    return CompiledValidation(
        "candidate.driver_" + name,
        invalid.aggregate(violations=invalid.count()),
        expected="complete finite additive partitions with unique typed coordinates and digests",
        repair="Restore exact retained side state or rebuild the selected additive Delta from its logical comparison.",
    )


def _float_text_builtin(pattern: str, value: float) -> str:
    raise NotImplementedError("native printf signature only")


def _hex_builtin(value: str) -> str:
    raise NotImplementedError("native hex signature only")


def _epoch_builtin(value: datetime) -> int:
    raise NotImplementedError("native epoch_us signature only")


_float_text: Callable[[str, ir.Value], ir.StringValue] = ibis.udf.scalar.builtin(
    _float_text_builtin, name="printf"
)
_hex: Callable[[ir.Value], ir.StringValue] = ibis.udf.scalar.builtin(_hex_builtin, name="hex")
_epoch: Callable[[ir.Value], ir.IntegerValue] = ibis.udf.scalar.builtin(
    _epoch_builtin, name="epoch_us"
)


def _coordinate(value: ir.Value) -> ir.StringValue:
    if isinstance(value, ir.StructValue):
        parts = [_coordinate(value[name]) for name in value.type().names]
        encoded = ibis.literal("S[") + ibis.literal("|").join(parts) + "]"
        return value.isnull().ifelse("N", encoded)
    if isinstance(value, ir.FloatingValue):
        canonical = _float_text("%a", (value == 0).ifelse(0.0, value))
    elif isinstance(value, ir.DecimalValue):
        text = value.cast("string")
        canonical = (value == 0).ifelse(
            "0", text.re_replace(r"(\.\d*?)0+$", r"\1").re_replace(r"\.$", "")
        )
    elif isinstance(value, ir.TimestampValue):
        canonical = _epoch(value).cast("string")
    else:
        canonical = value.cast("string")
    return value.isnull().ifelse("N", ibis.literal("V") + _hex(canonical))


def driver_item_id(
    table: ir.Table, row: d.DatasetRowContract, definition: DriverCandidateDefinition
) -> ir.StringValue:
    """Encode exact typed coordinates using the same canonical bytes as local rows."""
    fields = tuple(field for field in row.schema.columns if field.field_id in row.key_field_ids)
    signature = tuple((field.field_id.value, field.logical_type_id) for field in fields)
    encoded = ibis.literal("|").join([_coordinate(table[field.name]) for field in fields])
    prefix = (
        "candidate_driver_item@v1:"
        + d._canonical_digest(definition.identity_payload())
        + ":"
        + d._canonical_digest(signature)
        + ":"
    )
    result = ibis.literal("sha256:") + (ibis.literal(prefix) + encoded).hexdigest("sha256")
    if not isinstance(result, ir.StringValue):
        raise compilation_error("native SHA-256 coordinate encoding", "invalid digest expression")
    return result


def _coordinate_checks(
    table: ir.Table, row: d.DatasetRowContract
) -> tuple[CompiledValidation, ...]:
    keys = tuple(field.name for field in row.schema.columns if field.field_id in row.key_field_ids)
    counts = _group(table, keys, {"__count": table.count()})
    checks = [_check("coordinates_unique", counts.filter(counts.__count > 1))]
    for index, field in enumerate(row.schema.columns):
        if field.role_id != "entity_identity":
            if field.field_id in row.key_field_ids:
                column = table[field.name]
                valid = _finite(column)
                if field.nullable:
                    valid = valid | column.isnull()
                checks.append(
                    _check(f"coordinate_{index}_valid", table.filter(~valid.fill_null(False)))
                )
            continue
        identity, metadata = _identity(row, table)
        valid = identity.notnull()
        for name, _ in metadata.identity_signature:
            valid = valid & _finite(identity[name])
        checks.append(_check("identity_valid", table.filter(~valid.fill_null(False))))
    return tuple(checks)


def _driver_sum(value: ir.NumericValue) -> ir.NumericValue:
    return exact_float_sum(value) if isinstance(value, ir.FloatingValue) else value.sum()


def lower_driver_candidate(
    table: ir.Table,
    spec: DriverCandidateSpecV1,
    *,
    original: ir.Table | None = None,
) -> tuple[ir.Table, tuple[CompiledValidation, ...], ir.Table]:
    """Fold each axis independently and score complete frozen scope partitions."""
    definition = spec.definition
    if definition.method_id != "axis_concentration@v1":
        raise compilation_error("axis_concentration@v1", "unsupported driver Candidate method")
    prepared = prepare_exact_partition(
        table,
        spec,
        method="additive_difference@v1",
        empty_scope_allowed=True,
        exact_floating=True,
        original=original,
    )
    table, scopes, times = prepared.table, prepared.scopes, prepared.times
    authorities = prepared.authorities
    checks = [*prepared.checks, *_coordinate_checks(table, spec.input_row)]
    current, baseline = table.current_value, table.baseline_value
    if current.type().is_integer():
        current, baseline = current.cast("decimal(38,0)"), baseline.cast("decimal(38,0)")
    primary_valid = (
        table.delta.identical_to(_numeric(current) - _numeric(baseline))
        & (table.calculation_status == "ok")
        & table.coordinate_presence.isin(("matched", "current_only", "baseline_only"))
    )
    checks.append(_check("delta_valid", table.filter(~primary_valid.fill_null(False))))
    outputs: list[ir.Table] = []
    for field, axis_ref in zip(spec.axis_fields, definition.search_space, strict=True):
        axis = field.name
        folded = exact_partition_fold(
            table, authorities, (*scopes, axis), times, exact_floating=True
        )
        current = _numeric(_value(folded, authorities["current"], "current"))
        baseline = _numeric(_value(folded, authorities["baseline"], "baseline"))
        members = folded.select(
            *scopes,
            axis,
            *times,
            __absolute=(current - baseline).abs(),
        )
        checks.append(_check("contribution_finite", members.filter(~_finite(members.__absolute))))
        totals = _group(
            members,
            scopes,
            {"axis_cardinality": members.count(), "__total": _driver_sum(members.__absolute)},
        )
        # With no scope keys, an empty input still yields one global aggregate
        # row with count zero. Remove it before validating the absolute total.
        totals = totals.filter(totals.axis_cardinality > 0)
        checks.append(_check("absolute_total_finite", totals.filter(~_finite(totals.__total))))
        positive = totals.filter(totals.__total > 0).view()
        joined = members.join(
            positive,
            [members[name].identical_to(positive[name]) for name in scopes],
            how="inner" if scopes else "cross",
        )
        ranked = joined.select(*members.columns, positive.axis_cardinality, positive.__total)
        ordering = [ranked.__absolute.desc(), ranked[axis].asc(nulls_first=False)]
        groups = [ranked[name] for name in scopes]
        ranked = ranked.mutate(
            __cumulative=_driver_sum(ranked.__absolute).over(
                ibis.window(group_by=groups, order_by=ordering, preceding=None, following=0)
            ),
            __position=(
                ibis.row_number().over(ibis.window(group_by=groups, order_by=ordering)) + 1
            ),
        )
        # Comparing complementary mass retains exact Decimal/integer boundaries.
        reached = ranked.filter(ranked.__cumulative >= ranked.__total - ranked.__cumulative)
        reached = reached.mutate(
            __selected=ibis.row_number().over(
                ibis.window(
                    group_by=[reached[name] for name in scopes], order_by=reached.__position
                )
            )
        ).filter(lambda t: t.__selected == 0)
        outputs.append(
            reached.select(
                *scopes,
                *times,
                axis_ref=ibis.literal(axis_ref),
                axis_cardinality=reached.axis_cardinality.cast("int64"),
                concentration_member_count=reached.__position.cast("int64"),
                concentration_share=(reached.__cumulative / reached.__total).cast("float64"),
                score=(1.0 / (reached.__position + reached.axis_cardinality / 1000.0)).cast(
                    "float64"
                ),
                reason_codes=ibis.literal([_REASON], type="array<string>"),
            )
        )
    candidates = ibis.union(*outputs) if len(outputs) > 1 else outputs[0]
    candidates = candidates.mutate(item_id=driver_item_id(candidates, spec.output_row, definition))
    checks.extend(_coordinate_checks(candidates, spec.output_row))
    digest_counts = candidates.group_by("item_id").aggregate(__count=candidates.count())
    checks.append(_check("item_id_unique", digest_counts.filter(digest_counts.__count > 1)))
    checks.append(
        _check(
            "scores_finite",
            candidates.filter(
                ~(_finite(candidates.score) & _finite(candidates.concentration_share))
            ),
        )
    )
    emitted = candidates.order_by(ibis.desc("score"), *scopes, "axis_ref", "item_id").limit(
        definition.limit
    )
    result = emitted.select(*(field.name for field in spec.output_row.schema.columns))
    scope_input = table if original is None else original
    scope_rows = _group(scope_input, scopes, {"__count": scope_input.count()})
    if not scopes:
        scope_rows = scope_rows.filter(scope_rows.__count > 0)
    totals_proof = table.aggregate(input_row_count=table.count()).cross_join(
        scope_rows.aggregate(scope_count=scope_rows.count())
    )
    proof = totals_proof.cross_join(
        candidates.aggregate(
            pre_limit_candidate_count=candidates.count(),
            score_min=candidates.score.min(),
            score_max=candidates.score.max(),
        )
    ).cross_join(emitted.aggregate(emitted_candidate_count=emitted.count()))
    proof = proof.mutate(
        searched_axis_count=proof.scope_count * len(spec.axis_fields),
        evaluated_axis_count=proof.scope_count * len(spec.axis_fields),
        zero_contribution_axis_count=proof.scope_count * len(spec.axis_fields)
        - proof.pre_limit_candidate_count,
    )
    checks.append(_check("evaluated", proof.filter(proof.evaluated_axis_count < 1)))
    return result, tuple(checks), proof


def driver_candidate_output_proof(
    table: ir.Table,
    row: d.DatasetRowContract,
    definition: DriverCandidateDefinition,
    evaluation: DriverCandidateEvaluationSummary | None = None,
) -> ir.Table:
    """Validate retained rows in the engine and disclose only a scalar violation count."""
    valid = (
        _finite(table.score)
        & _finite(table.concentration_share)
        & (table.axis_cardinality >= 1)
        & (table.concentration_member_count >= 1)
        & (table.concentration_member_count <= table.axis_cardinality)
        & (table.concentration_share >= 0.5)
        & (table.concentration_share <= 1.0)
        & table.axis_ref.isin(definition.search_space)
        & (table.reason_codes == ibis.literal([_REASON], type="array<string>"))
        & (table.item_id == driver_item_id(table, row, definition))
        & _close(
            table.score, 1.0 / (table.concentration_member_count + table.axis_cardinality / 1000.0)
        )
    )
    if evaluation is not None and evaluation.score_range is not None:
        low = ibis.literal(str(evaluation.score_range[0])).cast("float64")
        high = ibis.literal(str(evaluation.score_range[1])).cast("float64")
        valid = (
            valid
            & ((table.score >= low) | _close(table.score, low))
            & ((table.score <= high) | _close(table.score, high))
        )
    checks = [
        *_coordinate_checks(table, row),
        _check("rows_valid", table.filter(~valid.fill_null(False))),
    ]
    if evaluation is not None:
        count = table.aggregate(__count=table.count())
        checks.append(
            _check(
                "retained_count", count.filter(count.__count > evaluation.emitted_candidate_count)
            )
        )
    counts = table.group_by("item_id").aggregate(__count=table.count())
    checks.append(_check("ids_unique", counts.filter(counts.__count > 1)))
    combined = ibis.union(*(check.expression for check in checks))
    return combined.aggregate(violations=combined.violations.sum())


def decode_driver_candidate_proof(
    values: Mapping[str, object], definition: DriverCandidateDefinition
) -> CandidateSearchSummary:
    """Decode a closed aggregate record without moving scope identities into Python."""
    count_names = (
        "input_row_count",
        "scope_count",
        "searched_axis_count",
        "evaluated_axis_count",
        "zero_contribution_axis_count",
        "pre_limit_candidate_count",
        "emitted_candidate_count",
    )
    if set(values) != {*count_names, "score_min", "score_max"}:
        raise compilation_error("exact driver evaluation proof fields", "invalid scalar proof")
    counts: dict[str, int] = {}
    for name in count_names:
        value = values[name]
        if type(value) is not int or value < 0:
            raise compilation_error("non-negative driver evaluation counts", "invalid scalar proof")
        counts[name] = value
    before, emitted = counts["pre_limit_candidate_count"], counts["emitted_candidate_count"]
    score_range: tuple[float, float] | None = None
    if before:
        low, high = values["score_min"], values["score_max"]
        if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
            raise compilation_error("finite driver score extrema", "invalid scalar proof")
        score_range = (float(low), float(high))
        if (
            not all(math.isfinite(item) for item in score_range)
            or not 0 < score_range[0] <= score_range[1] <= 1
        ):
            raise compilation_error("ordered positive driver score extrema", "invalid scalar proof")
    if (
        counts["searched_axis_count"] != counts["scope_count"] * len(definition.search_space)
        or counts["evaluated_axis_count"] != counts["searched_axis_count"]
        or before + counts["zero_contribution_axis_count"] != counts["evaluated_axis_count"]
        or emitted != min(before, definition.limit)
        or (not before and (values["score_min"] is not None or values["score_max"] is not None))
    ):
        raise compilation_error(
            "consistent complete driver evaluation counts", "invalid scalar proof"
        )
    return CandidateSearchSummary(
        definition,
        DriverCandidateEvaluationSummary(
            input_row_count=counts["input_row_count"],
            scope_count=counts["scope_count"],
            searched_axis_count=counts["searched_axis_count"],
            evaluated_axis_count=counts["evaluated_axis_count"],
            zero_contribution_axis_count=counts["zero_contribution_axis_count"],
            pre_limit_candidate_count=before,
            emitted_candidate_count=emitted,
            score_range=score_range,
            reason_counts=((_REASON, before),),
        ),
    )
