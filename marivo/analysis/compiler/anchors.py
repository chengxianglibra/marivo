"""Public-Ibis Anchor lowering and bounded contribution preparation."""

from __future__ import annotations

from functools import reduce
from operator import and_

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir

from marivo.analysis.anchors import ElapsedWindow
from marivo.analysis.compiler.domain_preparation import lower_candidates
from marivo.analysis.compiler.graph_lowering import (
    IntegrityCheck,
    LoweredCheck,
    LoweredRelation,
    RelationLayout,
    SourceBinding,
    _linear_finish,
    _ratio_finish,
    canonical_layout,
)
from marivo.analysis.compiler.graph_plan import SourceMethodStage
from marivo.analysis.core.domain_captures import fail
from marivo.analysis.core.model import (
    AnchorDomainPart,
    AnchorObservationPart,
    OriginalStatePart,
    SubjectPart,
    require_part,
)
from marivo.analysis.core.rules import AnchorBind, AnchorObserve, ObserveCount


def bind(
    stage: SourceMethodStage, source: LoweredRelation
) -> tuple[ir.Table, RelationLayout, tuple[str, ...]]:
    params = stage.node.parameters
    assert isinstance(params, AnchorBind)
    table = source.expression
    subjects = tuple(name for name in table.columns if name.startswith("subject__key_"))
    originals = tuple(key.column for key in source.layout.keys)
    selected = table.filter(
        (
            table.occurrences__occurred_at
            >= ibis.literal(params.during_start).cast(table.occurrences__occurred_at.type())
        )
        & (
            table.occurrences__occurred_at
            < ibis.literal(params.during_end).cast(table.occurrences__occurred_at.type())
        )
    )
    fields = {f"key_{i}": selected[name] for i, name in enumerate((*subjects, *originals))}
    fields.update({name: selected[name] for name in subjects})
    fields.update(
        anchor__started_at=selected.occurrences__occurred_at,
        anchor__sequence_int=selected.occurrences__sequence_int,
        anchor__sequence_enum=selected.occurrences__sequence_enum,
    )
    return (
        selected.select(**fields),
        canonical_layout(stage.node.signature, has_value=False),
        source.source_ids,
    )


def component_names(part: OriginalStatePart, index: int, count: int) -> tuple[str, str]:
    if part.method_version == "ratio@v1":
        prefix = ("numerator", "denominator")[index]
        return prefix + "_sum", prefix + "_non_null_count"
    if part.method_version == "linear@v1":
        return part.components[2 * index], part.components[2 * index + 1]
    if count != 1:
        fail(
            "anchor_metric", "component state differs from its closed composition", stage="lowering"
        )
    return ("count", "count") if part.components == ("count",) else ("sum", "non_null_count")


def _columns(domain: AnchorDomainPart, root: str) -> tuple[tuple[str, str], ...]:
    event = next((e for e in domain.preparation.events if e.source.ref.path == root), None)
    columns = (
        ()
        if event is None
        else tuple(
            (f"anchor_key_{i}", field.source_column) for i, field in enumerate(event.identity)
        )
    )
    order = domain.preparation.order
    if order is not None:
        fields = {field.source_column for field in order.fields if field.entity_ref.path == root}
        if len(fields) == 1:
            columns = (*columns, ("sequence", next(iter(fields))))
    return columns


def observe(
    stage: SourceMethodStage,
    inputs: tuple[LoweredRelation, ...],
    bindings: tuple[SourceBinding, ...],
    relations: tuple[LoweredRelation, ...],
    checks: list[LoweredCheck],
) -> tuple[ir.Table, RelationLayout, tuple[str, ...]]:
    params = stage.node.parameters
    assert isinstance(params, AnchorObserve)
    declaration = require_part(stage.node.signature, "anchor")
    assert isinstance(declaration, AnchorObservationPart)
    state = require_part(stage.node.signature, "original_state")
    assert isinstance(state, OriginalStatePart)
    domain = declaration.domain
    candidates: list[ir.Table] = []
    component_ids: list[tuple[str, ...]] = []
    ids: tuple[str, ...] = () if stage.operation == "prepare" else inputs[0].source_ids
    members = inputs[0] if stage.operation == "prepare" else inputs[1]
    for observation in params.observations:
        candidate, _, source_ids = lower_candidates(
            stage,
            members,
            bindings,
            relations,
            checks,
            captured_columns=_columns(domain, observation.contribution.path),
            observation=observation,
        )
        ids = tuple(dict.fromkeys((*ids, *source_ids)))
        candidates.append(candidate)
        component_ids.append(source_ids)
    if stage.operation == "prepare":
        # Separate typed lists avoid coercing Decimal/Duration and preserve the
        # complete candidate identity even when component roots differ.
        bundles = [
            candidate.aggregate(
                **{
                    f"uses_{i}": ibis.struct(
                        {name: candidate[name] for name in candidate.columns}
                    ).collect()
                }
            )
            for i, candidate in enumerate(candidates)
        ]
        table = reduce(lambda a, b: a.cross_join(b), bundles)
        return table, RelationLayout((), None, (), tuple(table.columns)), ids
    if not isinstance(params.window, ElapsedWindow) or domain.journey is not None:
        fail(
            "physical_qualification",
            "native observation requires Event-origin elapsed windows",
            stage="lowering",
        )
    span = params.window.duration
    if span.unit == "ns" and span.ticks % 1000:
        fail(
            "window_precision",
            "elapsed deadline cannot be represented in captured microseconds",
            stage="lowering",
        )
    micros = span.ticks * {"s": 1000000, "ms": 1000, "us": 1, "ns": 1}[span.unit]
    if span.unit == "ns":
        micros //= 1000
    anchors = inputs[0].expression.mutate(
        anchor__deadline=inputs[0].expression.anchor__started_at
        + ibis.interval(microseconds=micros)
    )
    keys = tuple(key.column for key in inputs[0].layout.keys)
    subject = require_part(stage.node.signature, "subject")
    assert isinstance(subject, SubjectPart)
    width = len(subject.subject_key)
    result = anchors
    for i, (observation, candidate) in enumerate(zip(params.observations, candidates, strict=True)):
        a, c = anchors.view(), candidate.view()
        joined = a.inner_join(c, [a[f"subject__key_{j}"] == c[f"member_{j}"] for j in range(width)])
        same = (
            reduce(
                and_,
                (
                    a[keys[width + 1 + j]] == c[name]
                    for j, name in enumerate(c.columns)
                    if name.startswith("anchor_key_")
                ),
                ibis.literal(True),
            )
            if any(name.startswith("anchor_key_") for name in c.columns)
            else ibis.literal(False)
        )
        same = same & a[keys[width]].isin(
            tuple(
                event.ref.path
                for event in domain.preparation.events
                if event.source.ref.path == observation.contribution.path
            )
        )
        tied = (c.event_time == a.anchor__started_at) & ~same
        if "sequence" in c.columns:
            seq = c.sequence
            if seq.type().is_integer():
                left = a.anchor__sequence_int
                unknown = seq.isnull() | left.isnull() | (seq == left)
                later = seq > left
            else:
                order = domain.preparation.order
                assert order is not None
                scale = next(
                    item.order for item in order.definition.sequences if item.order != "integer"
                )
                assert isinstance(scale, tuple)
                right_rank = seq.cases(
                    *((label, index) for index, label in enumerate(scale)), else_=-1
                )
                left_rank = a.anchor__sequence_enum.cases(
                    *((label, index) for index, label in enumerate(scale)), else_=-1
                )
                unknown = (right_rank < 0) | (left_rank < 0) | (right_rank == left_rank)
                later = right_rank > left_rank
        else:
            unknown, later = ibis.literal(True), ibis.literal(False)
        checks.append(
            IntegrityCheck(
                stage.output,
                "r7.business_order: distinct same-instant component requires captured order",
                joined.filter(tied & unknown),
                tuple(dict.fromkeys((*inputs[0].source_ids, *component_ids[i]))),
            )
        )
        selected = joined.filter(
            (c.event_time >= a.anchor__started_at)
            & (c.event_time < a.anchor__deadline)
            & ~same
            & ((c.event_time > a.anchor__started_at) | later)
        )
        magnitude, support = component_names(state, i, len(candidates))
        dtype = (
            dt.dtype(declaration.component_types[i])
            if not declaration.component_types[i].startswith("interval(")
            else dt.int64
        )
        raw = c.amount
        total = raw.cast("decimal(38,0)").sum().cast(dtype) if dtype.is_integer() else raw.sum()
        measures: dict[str, ir.Value] = {
            "original_state__" + magnitude: selected.count()
            if isinstance(observation, ObserveCount)
            else total.fill_null(ibis.literal(0, type=dtype)),
            "anchor__uses_" + str(i): ibis.struct({name: c[name] for name in c.columns}).collect(),
        }
        if support != magnitude:
            measures["original_state__" + support] = raw.count()
        absolute = (
            magnitude.removesuffix("sum") + "absolute_sum"
            if magnitude != "count"
            else "absolute_sum"
        )
        if absolute in state.components:
            measures["original_state__" + absolute] = raw.abs().sum().fill_null(0.0)
        aggregate = selected.group_by(*(a[key] for key in keys)).aggregate(**measures)
        r = result.view()
        joined_result = r.left_join(aggregate, keys)
        result = joined_result.select(
            *(r[name] for name in r.columns),
            **{
                name: aggregate[name].fill_null(ibis.literal([], type=aggregate[name].type()))
                if name.startswith("anchor__uses_")
                else aggregate[name].fill_null(0)
                for name in measures
            },
        )
    layout = canonical_layout(stage.node.signature, has_value=True)
    if state.method_version == "ratio@v1":
        return _ratio_finish(result, layout, stage.node.signature), layout, ids
    if state.method_version == "linear@v1":
        return _linear_finish(result, layout, stage.node.signature), layout, ids
    magnitude, support = component_names(state, 0, 1)
    defined = (result["original_state__" + support] > 0) | ibis.literal(
        isinstance(params.observations[0], ObserveCount) or state.method_version == "sum_zero@v1"
    )
    return (
        result.mutate(
            value=defined.ifelse(
                result["original_state__" + magnitude],
                ibis.null().cast(result["original_state__" + magnitude].type()),
            ),
            cell_tag=defined.ifelse("defined", "null"),
            cell_reason=defined.ifelse(ibis.null().cast("string"), "empty_contribution"),
            coverage__complete=ibis.literal(True),
        ).select(*layout.columns),
        layout,
        ids,
    )
