"""Complete same-source outer alignment over known Frame coordinate carriers."""

from dataclasses import dataclass

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.query_program import FrameCombineProgram
from marivo.analysis.compiler.source_predicates import string_bytes
from marivo.analysis.model.data_schema import DataSchema
from marivo.analysis.model.data_types import DataColumn
from marivo.datasource.spi import SourceSession

ALIGN_ORDER = ("__frame_align_side", "__frame_align_position")
_PRESENT = "__frame_alignment_present"
_POSITION = "__frame_alignment_position"


@dataclass(frozen=True, slots=True)
class AlignmentInput:
    expression: ir.Table
    schema: DataSchema
    order: tuple[str, ...]
    nulls_first: bool

    def ranked(self, owner: SourceSession) -> ir.Table:
        source = self.expression
        ordered = tuple(
            (
                string_bytes(source[key], owner.provider.name)
                if source[key].type().is_string()
                else source[key]
            ).asc(nulls_first=self.nulls_first)
            for key in self.order
        )
        number, _ = owner.provider.dialect.row_number()
        position = number.over(ibis.window(order_by=ordered))
        marked = source.select(
            *(column.slot_id for column in self.schema.columns),
            **{_PRESENT: ibis.literal(True), _POSITION: position},
        )
        nullable = owner.provider.dialect.join_value
        if nullable is None:
            return marked
        return marked.select(**{name: nullable(marked[name]) for name in marked.columns})


def _aligned_keys(program: FrameCombineProgram) -> tuple[tuple[DataColumn, DataColumn], ...]:
    index = dict(program.operation.pairs)
    return tuple(
        (left, right)
        for i, group in enumerate(program.left_schema.axes)
        for left, right in zip(
            group.columns, program.right_schema.axes[index[i]].columns, strict=True
        )
    )


def _key_equality(
    left: ir.Value, right: ir.Value, provider: str, *, nullable: bool
) -> tuple[ir.BooleanValue, ...]:
    # Ordinary equality on a separate validity bit and its exact payload is
    # hash-joinable where FULL JOIN rejects IS NOT DISTINCT FROM.
    validity: tuple[ir.BooleanValue, ...] = ()
    if nullable:
        validity = (left.isnull() == right.isnull(),)
        fill = "" if left.type().is_string() else 0
        left, right = left.fill_null(fill), right.fill_null(fill)
    if left.type().is_string():
        left, right = string_bytes(left, provider), string_bytes(right, provider)
    equality = left == right
    assert isinstance(equality, ir.BooleanValue)
    return (*validity, equality)


def _both_values(source: ir.Table, schema: DataSchema, present: ir.Value) -> tuple[ir.Value, ...]:
    return tuple(
        present.ifelse(source[column.slot_id], ibis.null().cast(source[column.slot_id].type()))
        for column in schema.values
    )


def lower_align(
    left_input: AlignmentInput,
    right_input: AlignmentInput,
    program: FrameCombineProgram,
    owner: SourceSession,
) -> ir.Table | str:
    """Join complete known keys and preserve left-first order and missing-side values."""
    if not owner.provider.dialect.supports_full_outer_join:
        return "this provider has no native full outer Frame join"
    output = program.output_schema
    if program.operation.match is not None or not output.key_fields:
        return "closed correspondence or scalar outer alignment retains the checked local kernel"
    key_pairs = _aligned_keys(program)
    left = left_input.ranked(owner)
    right = right_input.ranked(owner)
    if left.equals(right):
        right = right.view()
    joined = left.outer_join(
        right,
        [
            term
            for a, b in key_pairs
            for term in _key_equality(
                left[a.slot_id],
                right[b.slot_id],
                owner.provider.name,
                nullable=a.nullable or b.nullable,
            )
        ],
    )
    left_present = left[_PRESENT].fill_null(False)
    right_present = right[_PRESENT].fill_null(False)
    keys = tuple(left_present.ifelse(left[a.slot_id], right[b.slot_id]) for a, b in key_pairs)
    values = (
        *_both_values(left, left_input.schema, left_present),
        *_both_values(right, right_input.schema, right_present),
    )
    presence = (
        left_present,
        right_present,
        *(left[c.slot_id].fill_null(False) for c in left_input.schema.presence),
        *(right[c.slot_id].fill_null(False) for c in right_input.schema.presence),
    )
    return joined.select(
        **dict(
            zip(
                (column.slot_id for column in output.columns),
                (*keys, *values, *presence),
                strict=True,
            )
        ),
        **{
            ALIGN_ORDER[0]: left_present.ifelse(0, 1),
            ALIGN_ORDER[1]: left_present.ifelse(left[_POSITION], right[_POSITION]),
        },
    )
