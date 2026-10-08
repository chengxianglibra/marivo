"""Native Event retention witness selection through public Ibis expressions."""

from functools import reduce
from operator import and_

import ibis
import ibis.expr.types as ir

from marivo.analysis.anchors import ElapsedWindow
from marivo.analysis.compiler.graph_lowering import (
    IntegrityCheck,
    LoweredCheck,
    LoweredRelation,
    RelationLayout,
)
from marivo.analysis.compiler.graph_plan import SourceMethodStage
from marivo.analysis.core.domain_captures import fail
from marivo.analysis.core.model import InstanceRetentionPart, require_part
from marivo.analysis.core.rules import AnchorRetention


def lower(
    stage: SourceMethodStage, inputs: tuple[LoweredRelation, ...], checks: list[LoweredCheck]
) -> tuple[ir.Table, RelationLayout, tuple[str, ...]]:
    params = stage.node.parameters
    part = require_part(stage.node.signature, "retention")
    assert isinstance(params, AnchorRetention) and isinstance(part, InstanceRetentionPart)
    if not isinstance(params.window, ElapsedWindow) or part.anchors.journey is not None:
        fail(
            "physical_qualification",
            "native retention needs Event elapsed inputs",
            stage="lowering",
        )
    span = params.window.duration
    if span.unit == "ns" and span.ticks % 1000:
        fail(
            "window_precision",
            "deadline is not representable in captured microseconds",
            stage="lowering",
        )
    micros = span.ticks * {"s": 1000000, "ms": 1000, "us": 1, "ns": 1}[span.unit]
    if span.unit == "ns":
        micros //= 1000
    anchor, returns = inputs
    keys = tuple(k.column for k in anchor.layout.keys)
    return_keys = tuple(k.column for k in returns.layout.keys)
    width = len(part.returning.events[0].subject.primary_key)
    a = anchor.expression.mutate(
        anchor__deadline=anchor.expression.anchor__started_at + ibis.interval(microseconds=micros)
    ).view()
    c = returns.expression.view()
    joined = a.inner_join(
        c, [a[f"subject__key_{i}"] == c[f"subject__key_{i}"] for i in range(width)]
    )
    same = ibis.literal(False)
    if any(event.ref == part.returning.events[0].ref for event in part.anchors.preparation.events):
        same = (a[keys[width]] == c[return_keys[0]]) & reduce(
            and_,
            (a[keys[width + 1 + i]] == c[key] for i, key in enumerate(return_keys[1:])),
            ibis.literal(True),
        )
    inside = (
        (c.occurrences__occurred_at >= a.anchor__started_at)
        & (c.occurrences__occurred_at < a.anchor__deadline)
        & ~same
    )
    order = part.anchors.preparation.order
    before, after = ibis.literal(False), ibis.literal(False)
    invalid = ibis.literal(False)
    if (
        order is not None
        and part.returning.order is not None
        and order.definition == part.returning.order.definition
    ):

        def ordinal(event: ir.Value, integer: ir.Value, label: ir.Value) -> ir.Value:
            if not order.definition.sequences:
                return ibis.null().cast("int64")
            return event.cases(
                *(
                    (
                        item.event_ref,
                        integer
                        if item.order == "integer"
                        else label.cases(
                            *((name, i) for i, name in enumerate(item.order)),
                            else_=ibis.null().cast("int64"),
                        ),
                    )
                    for item in order.definition.sequences
                ),
                else_=ibis.null().cast("int64"),
            )

        left = ordinal(a[keys[width]], a.anchor__sequence_int, a.anchor__sequence_enum)
        right = ordinal(
            c[return_keys[0]],
            c.occurrences__sequence_int
            if "occurrences__sequence_int" in c.columns
            else ibis.null().cast("int64"),
            c.occurrences__sequence_enum
            if "occurrences__sequence_enum" in c.columns
            else ibis.null().cast("string"),
        )
        invalid = left.notnull() & right.notnull() & (left == right)
        before, after = (left < right).fill_null(False), (left > right).fill_null(False)
        edges = {(edge.before_event, edge.after_event) for edge in order.definition.conflicts}
        while True:
            expanded = edges | {(x, w) for x, y in edges for z, w in edges if y == z}
            if expanded == edges:
                break
            edges = expanded
        event = part.returning.events[0].ref.path
        before |= a[keys[width]].isin(tuple(x for x, y in edges if y == event))
        after |= a[keys[width]].isin(tuple(y for x, y in edges if x == event))
    tied = c.occurrences__occurred_at == a.anchor__started_at
    source_ids = tuple(dict.fromkeys((*anchor.source_ids, *returns.source_ids)))
    checks.append(
        IntegrityCheck(
            stage.output,
            "r7.business_order: distinct simultaneous return requires exact captured order",
            joined.filter(inside & tied & (invalid | (before == after))),
            source_ids,
        )
    )
    selected = joined.filter(inside & (~tied | before))
    use = ibis.struct(
        {
            **{f"identity_{i}": c[key] for i, key in enumerate(return_keys[1:])},
            "instant": c.occurrences__occurred_at,
            "sequence_int": c.occurrences__sequence_int
            if "occurrences__sequence_int" in c.columns
            else ibis.null().cast("int64"),
            "sequence_enum": c.occurrences__sequence_enum
            if "occurrences__sequence_enum" in c.columns
            else ibis.null().cast("string"),
        }
    )
    matches = selected.group_by(*(a[key] for key in keys)).aggregate(retention__uses=use.collect())
    combined = a.left_join(matches, keys)
    table = combined.select(
        *(a[name] for name in a.columns),
        retention__uses=matches.retention__uses.fill_null(
            ibis.literal([], type=matches.retention__uses.type())
        ),
    )
    return (
        table,
        RelationLayout(
            anchor.layout.keys, None, (), tuple(name for name in table.columns if name not in keys)
        ),
        source_ids,
    )
