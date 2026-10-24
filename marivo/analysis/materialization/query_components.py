"""Checked role-specific source results and ephemeral original inputs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

import pandas as pd

from marivo.analysis.compiler.attribute_inputs import (
    AttributeLeafInput,
    AttributeNativeFormula,
    AttributeRootLayout,
)
from marivo.analysis.compiler.graph_plan import read_reduction, require_reduction
from marivo.analysis.compiler.original import (
    ComponentLayout,
)
from marivo.analysis.core.data_types import DataColumn, DurationDataType, ScalarDataType
from marivo.analysis.core.data_validation import normalize_stage_frame
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import source_columns

if TYPE_CHECKING:
    from marivo.analysis.core.attribute import (
        AttributeFailureContext,
        AttributeMatrixSource,
        AttributeWitness,
    )
    from marivo.analysis.materialization.graph_exchange import CheckReceipt, StageOutput


@dataclass(frozen=True, slots=True, eq=False)
class ComponentFrame:
    """One source occurrence's complete actual inputs, confined to an invocation."""

    layout: ComponentLayout
    table: pd.DataFrame
    domain: pd.DataFrame | None = None

    def __post_init__(self) -> None:
        expected = tuple(column.slot_id for column in self.layout.raw_columns)
        if tuple(self.table.columns) != expected:
            raise _invalid(f"original columns {tuple(self.table.columns)!r}; expected {expected!r}")
        identity = [column.slot_id for column, _ in self.layout.identity]
        if self.table.loc[:, identity].isna().any().any():
            raise _invalid("missing contribution identity in original inputs")
        if self.table.duplicated([*self.layout.keys, *identity]).any():
            raise _invalid("duplicate complete contribution identity in one fine coordinate")
        if self.domain is not None and (
            tuple(self.domain.columns) != self.layout.keys
            or (self.layout.keys and self.domain.duplicated(list(self.layout.keys)).any())
        ):
            raise _invalid("original coordinate support differs from the complete fine keys")

    @property
    def coordinates(self) -> pd.DataFrame:
        return (
            self.domain
            if self.domain is not None
            else self.table.loc[:, list(self.layout.keys)].drop_duplicates()
        )


def _invalid(received: str) -> MaterializationError:
    return MaterializationError(
        expected="complete invocation-local original inputs from one checked source statement",
        received=received,
        repair="Retain the original complete contribution identities and fine coordinate mapping.",
        stage="query_components",
        help_target="analysis_frame.compute",
    )


def complete_components(
    layout: ComponentLayout, frame: pd.DataFrame
) -> tuple[pd.DataFrame, ComponentFrame]:
    """Separate one actual source batch into its fine result and sufficient input."""
    names = [column.slot_id for column in layout.columns]
    missing = {*names, layout.present} - set(frame.columns)
    if missing:
        raise _invalid(f"source batch missing columns {tuple(sorted(missing))!r}")
    raw_names = {
        *(column.slot_id for column, _ in layout.identity),
        *(column.slot_id for column in layout.raw_columns if column.slot_id not in layout.keys),
        layout.present,
    }
    summary = [name for name in frame.columns if name not in raw_names]
    fine = frame.loc[:, summary].drop_duplicates().reset_index(drop=True)
    if layout.keys and fine.duplicated(list(layout.keys)).any():
        raise _invalid("different completed fine values for one coordinate")
    if not layout.keys and len(fine) != 1:
        raise _invalid("non-singleton completed scalar input")
    raw = frame.loc[
        frame[layout.present].astype("boolean").fillna(False),
        [column.slot_id for column in layout.raw_columns],
    ].copy()
    for column in layout.raw_columns:
        if isinstance(column.dtype, DurationDataType):
            # Source preparation represents exact Duration values as ticks.
            # Keep ticks here; the ordinary fine-result boundary owns Duration
            # conversion, and the original reducer rounds only its final value.
            raw[column.slot_id] = raw[column.slot_id].astype("int64[pyarrow]")
    actual = {
        column.slot_id: column
        for column in source_columns(
            raw,
            layout.raw_columns,
            (*layout.keys, *(column.slot_id for column, _ in layout.identity)),
        )
    }
    payload = layout.payload
    from marivo.analysis.compiler.original import AggregatePayload, WeightedPayload

    if isinstance(payload, AggregatePayload):
        payload = replace(payload, value=actual[payload.value.slot_id])
    elif isinstance(payload, WeightedPayload):
        payload = replace(
            payload, value=actual[payload.value.slot_id], weight=actual[payload.weight.slot_id]
        )
    layout = replace(
        layout,
        columns=source_columns(fine, layout.columns),
        identity=tuple((actual[column.slot_id], name) for column, name in layout.identity),
        payload=payload,
    )
    raw = normalize_stage_frame(
        raw,
        tuple(
            replace(column, dtype=ScalarDataType("int64"))
            if isinstance(column.dtype, DurationDataType)
            else column
            for column in layout.raw_columns
        ),
        (*layout.keys, *(column.slot_id for column, _ in layout.identity)),
    )
    domain_columns = tuple(column for column in layout.columns if column.slot_id in layout.keys)
    domain = normalize_stage_frame(
        fine.loc[:, list(layout.keys)].drop_duplicates().reset_index(drop=True),
        domain_columns,
        layout.keys,
    )
    return fine, ComponentFrame(
        layout,
        raw.reset_index(drop=True),
        domain,
    )


@dataclass(frozen=True, slots=True, eq=False)
class AttributeRootInputs:
    """One independently checked parent or fine role and its own source receipt."""

    layout: AttributeRootLayout
    stage_id: str
    parent: pd.DataFrame
    fine: pd.DataFrame
    receipts: tuple[CheckReceipt, ...]
    parent_state: tuple[Literal["present", "known_empty"], ...] = ()


def attribute_witness_order(rows: pd.DataFrame, columns: tuple[DataColumn, ...]) -> tuple[int, ...]:
    """Order only failed aggregate candidates by their native typed coordinates."""
    if not columns:
        return tuple(range(len(rows)))
    ordered = rows.reset_index(drop=True).sort_values(
        [column.slot_id for column in columns], kind="stable", na_position="first"
    )
    return tuple(int(index) for index in ordered.index)


def attribute_failure_context(
    layout: AttributeRootLayout,
    rows: pd.DataFrame,
    leaves: tuple[AttributeLeafInput, ...],
    columns: tuple[DataColumn, ...],
    witness: AttributeWitness,
    *,
    fine: bool = False,
) -> AttributeFailureContext:
    """Retain a real failing source row's typed use, full parent and window."""
    from marivo.analysis.core.attribute import (
        AttributeFailureContext,
        AttributeFineFiber,
        AttributeParentFiber,
        AttributeWindowFiber,
    )
    from marivo.analysis.core.frozen_calendar import frozen_key_rows

    available = {column.slot_id: column for column in columns}
    parents = tuple(available[name] for name in layout.parent_keys)
    parent_key = frozen_key_rows(rows.iloc[:1], parents)[0]
    context = layout.parent_context
    if context is None:
        from marivo.analysis.compiler.attribute_inputs import input_failure

        input_failure(
            "missing_component_proof",
            "the checked original-parent source association is unavailable",
        )
    matched = tuple(original for source, original in context.parent_mapping if source == parent_key)
    if len(matched) != 1:
        from marivo.analysis.compiler.attribute_inputs import input_failure

        input_failure(
            "missing_component_proof",
            "the complete source parent does not resolve to one checked original parent",
        )
    parent_key = matched[0]
    parents = context.parent_columns
    base_fiber: AttributeParentFiber | AttributeFineFiber = AttributeParentFiber(
        parent_key, parents
    )
    if fine:
        children = tuple(
            column
            for column in columns
            if column.slot_id in layout.keys and column.slot_id not in layout.parent_keys
        )
        base_fiber = AttributeFineFiber(
            parent_key, parents, frozen_key_rows(rows.iloc[:1], children)[0], children
        )
    fiber: AttributeParentFiber | AttributeFineFiber | AttributeWindowFiber = base_fiber
    binding = leaves[0].request.binding
    if binding.window_key is not None:
        assert binding.period is not None and binding.time is not None and binding.calendars
        from marivo._definition.types import CalendarCoordinate

        calendar = binding.calendars[0][1]
        assert isinstance(calendar, CalendarCoordinate)
        fiber = AttributeWindowFiber(
            base_fiber, binding.window_key, binding.period, binding.time, calendar
        )
    return AttributeFailureContext(tuple(leaf.use for leaf in leaves), fiber, witness)


def complete_attribute_inputs(
    layout: AttributeRootLayout,
    stage_id: str,
    frame: pd.DataFrame,
    originals: dict[tuple[int, str], pd.DataFrame],
) -> tuple[pd.DataFrame, AttributeRootInputs]:
    """Finish aggregate inputs and consume bounded backend failure witnesses."""
    from marivo.analysis.compiler.attribute_inputs import input_failure
    from marivo.analysis.core.frozen_calendar import frozen_key_rows
    from marivo.analysis.materialization.graph_exchange import CheckReceipt

    columns = {column.slot_id: column for column in layout.columns}
    identity = tuple(column for column, _ in layout.identity)
    parent_columns = tuple(columns[name] for name in layout.parent_keys)
    full_columns = tuple(columns[name] for name in layout.keys)
    from marivo.analysis.core.attribute import AttributeCoverageWitness, AttributePartitionWitness

    uncovered = (
        frame.loc[frame[layout.kind] == 6] if layout.population is not None else frame.iloc[:0]
    )
    if not uncovered.empty:
        from marivo.analysis.core.attribute import AttributeAggregateWitness

        actual = normalize_stage_frame(
            uncovered.loc[:, [c.slot_id for c in full_columns]], full_columns, ()
        )
        leaves = tuple(
            leaf for leaf in layout.leaves if leaf.fiber == uncovered[layout.fiber].iloc[0]
        )
        coverage_witness = AttributeAggregateWitness(
            "fine", frozen_key_rows(actual.iloc[:1], full_columns)[0], full_columns
        )
        input_failure(
            "undefined_input",
            "output Population coverage: missing complete coordinate",
            context=attribute_failure_context(
                layout, actual, leaves, full_columns, coverage_witness, fine=True
            ),
        )
    for fiber_number in dict.fromkeys(leaf.fiber for leaf in layout.leaves):
        leaves = tuple(leaf for leaf in layout.leaves if leaf.fiber == fiber_number)
        local = frame.loc[frame[layout.fiber] == fiber_number]
        for kind in (2, 3, 5):
            failed = local.loc[local[layout.kind] == kind].reset_index(drop=True)
            if failed.empty:
                continue
            witness_columns: tuple[DataColumn, ...]
            uses: tuple[AttributeLeafInput, ...]
            if kind == 5:
                leaf_index = int(failed["__attribute_leaf"].iloc[0])
                failed = failed.loc[failed["__attribute_leaf"] == leaf_index].reset_index(drop=True)
                leaf = leaves[leaf_index]
                assert leaf.distinct is not None
                witness_columns = (leaf.distinct,)
                uses = (leaf,)
            else:
                witness_columns = identity
                uses = leaves
            context_columns = (*parent_columns, *witness_columns)
            normalized = normalize_stage_frame(
                failed.loc[:, [c.slot_id for c in context_columns]], context_columns, ()
            )
            key = frozen_key_rows(normalized.iloc[:1], witness_columns)[0]
            witness: AttributeWitness
            if kind == 2:
                witness = AttributeCoverageWitness(key)
            else:
                groups = normalize_stage_frame(
                    failed.loc[:, [c.slot_id for c in full_columns]], full_columns, ()
                )
                witness = AttributePartitionWitness(
                    "distinct" if kind == 5 else "root",
                    key,
                    tuple(frozen_key_rows(groups, full_columns)),
                )
            input_failure(
                "undefined_input" if kind == 2 else "overlapping_groups",
                f"{stage_id}: missing classification"
                if kind == 2
                else f"{stage_id}: classification crosses joint groups",
                context=attribute_failure_context(
                    layout, normalized, uses, context_columns, witness
                ),
            )
    receipts: tuple[CheckReceipt, ...] = (
        (CheckReceipt("attribute.membership_partition", stage_id),)
        if layout.scope == "fine" and layout.partition
        else ()
    )
    if layout.population is not None:
        receipts = (*receipts, CheckReceipt("attribute.population_coverage", stage_id))
    values = tuple(
        column
        for column in layout.columns
        if column.slot_id not in layout.keys
        and not column.slot_id.startswith(("numeric_check.", "predicate_check."))
        and column.slot_id not in ("__numeric_present", "__sample_present")
    )
    parent_names = [*layout.parent_keys, *(column.slot_id for column in values)]
    fine_names = [column.slot_id for column in layout.columns]
    parent = (
        frame.loc[frame[layout.kind] == 1, parent_names].drop_duplicates().reset_index(drop=True)
    )
    fine = frame.loc[frame[layout.kind] == 0, fine_names].drop_duplicates().reset_index(drop=True)
    kind, target, keys = (
        (1, parent, parent_columns) if layout.scope == "parent" else (0, fine, full_columns)
    )
    key_frame = normalize_stage_frame(target.loc[:, [c.slot_id for c in keys]], keys, ())
    target_key_rows = frozen_key_rows(key_frame, keys)
    for leaf in layout.leaves:
        reduction = require_reduction(leaf.read)
        slot = reduction.value_slot
        completed = originals[(kind, slot)]
        completed_keys = frozen_key_rows(completed, keys)
        by_key = {key: index for index, key in enumerate(completed_keys)}
        active = tuple(
            i
            for i in range(len(target))
            if reduction.window_key is None
            or target[reduction.calendars[0][0]].iloc[i] == reduction.window_key
        )
        target_keys = tuple(target_key_rows[i] for i in active)
        if len(by_key) != len(completed_keys) or by_key.keys() != set(target_keys):
            raise _invalid("finished original values differ from their complete aggregate keys")
        restored = pd.Series([pd.NA] * len(target), dtype=completed[slot].dtype)
        for index, key in zip(active, target_keys, strict=True):
            restored.iloc[index] = completed[slot].iloc[by_key[key]]
        target[slot] = restored
    actual_columns = {
        column.slot_id: column
        for column in source_columns(
            parent if layout.scope == "parent" else fine, layout.columns, layout.keys
        )
    }
    # Native formula values cross this boundary before a conservative planned
    # Decimal scale can truncate their actual_columns source carrier.
    layout = replace(
        layout,
        columns=tuple(actual_columns[column.slot_id] for column in layout.columns),
        native_formulas=tuple(
            replace(native, column=actual_columns[native.column.slot_id])
            for native in layout.native_formulas
        ),
    )
    columns.update(actual_columns)
    if layout.scope == "parent":
        parent = normalize_stage_frame(
            parent, tuple(columns[name] for name in parent.columns), layout.parent_keys
        )
        fine = parent.iloc[:0].copy()
    else:
        fine = normalize_stage_frame(fine, layout.columns, layout.keys)
        parent = fine.iloc[:0].loc[:, parent_names].copy()
    if layout.parent_keys and parent.duplicated(list(layout.parent_keys)).any():
        input_failure("missing_component_proof", f"{stage_id}: duplicate complete parent totals")
    if layout.keys and fine.duplicated(list(layout.keys)).any():
        input_failure("missing_component_proof", f"{stage_id}: duplicate complete fine inputs")
    receipts = (
        *receipts,
        *(
            (CheckReceipt("attribute.distinct_value_partition", stage_id),)
            if layout.scope == "fine" and any(leaf.distinct is not None for leaf in layout.leaves)
            else ()
        ),
    )
    states: tuple[Literal["present", "known_empty"], ...] = tuple(
        "present"
        if any(
            pd.notna(row[leaf.support.slot_id]) and row[leaf.support.slot_id] > 0
            for leaf in layout.leaves
        )
        else "known_empty"
        for _, row in parent.iterrows()
    )
    return (parent if layout.scope == "parent" else fine), AttributeRootInputs(
        layout, stage_id, parent, fine, receipts, states
    )


def complete_attribute_parent_domain(
    root: AttributeRootInputs, projection: StageOutput
) -> AttributeRootInputs:
    """Retain every selected parent, distinguishing checked emptiness from absence."""
    from marivo._definition.expressions import EntityCount, SourceAggregate
    from marivo.analysis.core.frozen_calendar import frozen_key_rows

    if root.layout.scope != "parent":
        return root
    columns = {column.slot_id: column for column in root.layout.columns}
    parent_columns = tuple(columns[name] for name in root.layout.parent_keys)
    calendar_slots = {
        slot
        for leaf in root.layout.leaves
        if read_reduction(leaf.read) is not None
        for slot, _ in require_reduction(leaf.read).calendars
    }
    projection_order = (
        *(key for key in root.layout.parent_keys if key not in calendar_slots),
        *(key for key in root.layout.parent_keys if key in calendar_slots),
    )
    selected = projection.table.loc[:, list(projection.keys)].copy()
    selected.columns = list(projection_order)
    selected = selected.loc[:, list(root.layout.parent_keys)]
    expected = tuple(frozen_key_rows(selected, parent_columns))
    actual = tuple(frozen_key_rows(root.parent, parent_columns))
    if len(set(actual)) != len(actual):
        raise _invalid("duplicate complete Attribute parent totals")
    if not root.layout.parent_keys and expected and not actual:
        from marivo.analysis.compiler.attribute_inputs import input_failure

        input_failure("missing_component_proof", "missing selected scalar parent aggregate")
    by_key = {key: index for index, key in enumerate(actual)}
    records: list[dict[str, object]] = []
    states: list[Literal["present", "known_empty"]] = []
    for index, key in enumerate(expected):
        if key in by_key:
            position = by_key[key]
            records.append(
                {str(name): root.parent[name].iloc[position] for name in root.parent.columns}
            )
            states.append(root.parent_state[position])
            continue
        record: dict[str, object] = {
            name: selected[name].iloc[index] for name in root.layout.parent_keys
        }
        for leaf in root.layout.leaves:
            reduction = read_reduction(leaf.read)
            assert reduction is not None
            node = reduction.node
            zero = isinstance(node, EntityCount) or (
                isinstance(node, SourceAggregate)
                and (node.reducer in ("sum", "count", "count_distinct"))
            )
            record[reduction.value_slot] = 0 if zero else None
            record[leaf.support.slot_id] = 0
            record[leaf.effective.slot_id] = 0
            record.update({column.slot_id: 0 for _, column in leaf.roles})
        for native in root.layout.native_formulas:
            record[native.column.slot_id] = _empty_native_value(root.layout, native, record)
        records.append(record)
        states.append("known_empty")
    result = pd.DataFrame(
        {
            name: pd.Series([record[name] for record in records], dtype=root.parent[name].dtype)
            for name in root.parent.columns
        }
    )
    parent = normalize_stage_frame(
        result, tuple(columns[name] for name in result.columns), root.layout.parent_keys
    )
    return replace(root, parent=parent, parent_state=tuple(states))


def _empty_native_value(
    layout: AttributeRootLayout,
    native: AttributeNativeFormula,
    record: dict[str, object],
) -> object:
    """Run the existing original numeric recipe over this role's known-empty reductions."""
    import ibis

    from marivo.analysis.core.value_types import ibis_type
    from marivo.analysis.methods._ibis_numeric import evaluate_numeric
    from marivo.semantic._numeric_lowering import (
        NumericValue,
        lower_metric_formula,
        numeric_failure,
    )

    columns = {column.slot_id: column for column in layout.columns}
    bindings = {
        value.occurrence: NumericValue(
            ibis.literal(
                record[value.column.slot_id], type=ibis_type(columns[value.column.slot_id].dtype)
            )
        )
        for value in native.program.values
        if value.column.slot_id in record
    }
    lowered = lower_metric_formula(
        native.node, native.occurrence, bindings, dict(native.program.declared)
    )
    index = pd.RangeIndex(1)
    for check in lowered.checks:
        if evaluate_numeric(check.violation.op(), {}, index).astype("boolean").fillna(False).any():
            numeric_failure(check.expected, check.received)
    return evaluate_numeric(lowered.value.op(), {}, index).iloc[0]


def select_attribute_root(
    roots: tuple[AttributeRootInputs, ...],
    scope: Literal["parent", "fine"],
    source: AttributeMatrixSource,
) -> AttributeRootInputs:
    """Resolve one role, full use, slot and captured window without first-match lookup."""
    matched = tuple(
        root
        for root in {root.stage_id: root for root in roots}.values()
        if root.layout.scope == scope
        and any(
            leaf.use == source.use
            and (
                source.window is None or leaf.request.binding.window_key == source.window.window_key
            )
            for leaf in root.layout.leaves
        )
        and any(column.slot_id == source.source for column in root.layout.columns)
    )
    if len(matched) != 1:
        from marivo.analysis.compiler.attribute_inputs import input_failure

        input_failure(
            "missing_component_proof",
            f"{scope}: expected one exact use/slot/window role, received {len(matched)}",
        )
    return matched[0]
