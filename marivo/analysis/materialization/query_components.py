"""Single-read transport of checked fine results and ephemeral original inputs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

import pandas as pd

from marivo.analysis.compiler.attribute_inputs import AttributeLeafInput, AttributeRootLayout
from marivo.analysis.compiler.original import (
    ComponentLayout,
)
from marivo.analysis.core.data_types import DataColumn, DurationDataType, ScalarDataType
from marivo.analysis.core.frozen_calendar import FrozenKey
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import normalize_stage_frame, source_columns

if TYPE_CHECKING:
    from marivo.analysis.core.attribute import AttributeFailureContext, AttributeWitness
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
    """Checked same-statement parent/fine components, with their source receipt."""

    layout: AttributeRootLayout
    stage_id: str
    parent: pd.DataFrame
    fine: pd.DataFrame
    receipts: tuple[CheckReceipt, ...]
    parent_state: tuple[Literal["present", "known_empty"], ...] = ()


def attribute_witness_order(rows: pd.DataFrame, columns: tuple[DataColumn, ...]) -> tuple[int, ...]:
    """Choose source witnesses deterministically by complete exact typed keys."""
    from marivo.analysis.core.frozen_calendar import frozen_key_rows

    keys = frozen_key_rows(rows, columns)
    return tuple(sorted(range(len(keys)), key=lambda index: repr(keys[index])))


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
    """Prove scope coverage and one joint tuple inside each complete parent fiber."""
    from marivo.analysis.compiler.attribute_inputs import input_failure
    from marivo.analysis.core.frozen_calendar import frozen_key_rows
    from marivo.analysis.materialization.graph_exchange import CheckReceipt

    columns = {column.slot_id: column for column in layout.columns}
    identity = tuple(column for column, _ in layout.identity)
    parent_columns = tuple(columns[name] for name in layout.parent_keys)
    full_columns = tuple(columns[name] for name in layout.keys)
    scope = frame.loc[frame[layout.kind] == 2].reset_index(drop=True)
    assigned = frame.loc[frame[layout.kind] == 3].reset_index(drop=True)
    scope_columns = (
        *parent_columns,
        *identity,
        DataColumn("__attribute_root_duplicate", "root duplicate", ScalarDataType("boolean"), True),
    )
    scope = normalize_stage_frame(
        scope.loc[:, [column.slot_id for column in scope_columns]], scope_columns, ()
    )
    assigned_columns = (
        *full_columns,
        *identity,
        *(leaf.distinct for leaf in layout.leaves if leaf.distinct is not None),
    )
    assigned = normalize_stage_frame(
        assigned.loc[:, [column.slot_id for column in assigned_columns]], assigned_columns, ()
    )
    identity_names = [column.slot_id for column in identity]
    if (
        scope.loc[:, identity_names].isna().any().any()
        or assigned.loc[:, identity_names].isna().any().any()
    ):
        input_failure("missing_component_proof", f"{stage_id}: incomplete scoped root identity")
    from marivo.analysis.core.attribute import AttributeCoverageWitness, AttributePartitionWitness

    assignments = tuple(frozen_key_rows(assigned, full_columns))
    for fiber_number in dict.fromkeys(leaf.fiber for leaf in layout.leaves):
        leaves = tuple(leaf for leaf in layout.leaves if leaf.fiber == fiber_number)
        local_scope = scope.loc[
            frame.loc[frame[layout.kind] == 2, layout.fiber].reset_index(drop=True) == fiber_number
        ].reset_index(drop=True)
        local_scope = local_scope.iloc[
            list(attribute_witness_order(local_scope, (*parent_columns, *identity)))
        ].reset_index(drop=True)
        local_assigned = assigned.loc[
            frame.loc[frame[layout.kind] == 3, layout.fiber].reset_index(drop=True) == fiber_number
        ].reset_index(drop=True)
        roots = tuple(frozen_key_rows(local_scope, (*parent_columns, *identity)))
        classified: dict[FrozenKey, set[FrozenKey]] = {}
        for root_key, key in zip(
            frozen_key_rows(local_assigned, (*parent_columns, *identity)),
            frozen_key_rows(local_assigned, full_columns),
            strict=True,
        ):
            classified.setdefault(root_key, set()).add(key)
        duplicated = local_scope["__attribute_root_duplicate"].astype("boolean").fillna(False)
        if duplicated.any():
            position = next(i for i, bad in enumerate(duplicated) if bad)
            witness: AttributeWitness = AttributeCoverageWitness(
                frozen_key_rows(local_scope.iloc[[position]], identity)[0]
            )
            error = _invalid("duplicate complete contribution identity in one scoped parent input")
            error.attribute_context = attribute_failure_context(
                layout, local_scope.iloc[[position]], leaves, (*parent_columns, *identity), witness
            )
            raise error
        for index, key in enumerate(roots):
            groups = classified.get(key, set())
            if len(groups) != 1:
                actual = local_scope.iloc[[index]]
                identity_key = frozen_key_rows(actual, identity)[0]
                witness = (
                    AttributeCoverageWitness(identity_key)
                    if not groups
                    else AttributePartitionWitness(
                        "root", identity_key, tuple(sorted(groups, key=repr)[:6])
                    )
                )
                input_failure(
                    "undefined_input" if not groups else "overlapping_groups",
                    f"{stage_id}: scoped identity does not have exactly one joint tuple",
                    context=attribute_failure_context(
                        layout, actual, leaves, (*parent_columns, *identity), witness
                    ),
                )
        for leaf in leaves:
            if leaf.distinct is None:
                continue
            counted = local_assigned.loc[local_assigned[leaf.distinct.slot_id].notna()].reset_index(
                drop=True
            )
            counted = counted.iloc[
                list(attribute_witness_order(counted, (*parent_columns, leaf.distinct)))
            ].reset_index(drop=True)
            partition: dict[FrozenKey, set[FrozenKey]] = {}
            counted_keys = tuple(frozen_key_rows(counted, (*parent_columns, leaf.distinct)))
            for key, group in zip(
                counted_keys, frozen_key_rows(counted, full_columns), strict=True
            ):
                partition.setdefault(key, set()).add(group)
            for index, key in enumerate(counted_keys):
                groups = partition[key]
                if len(groups) > 1:
                    actual = counted.iloc[[index]]
                    witness = AttributePartitionWitness(
                        "distinct",
                        frozen_key_rows(actual, (leaf.distinct,))[0],
                        tuple(sorted(groups, key=repr)[:6]),
                    )
                    input_failure(
                        "overlapping_groups",
                        f"{stage_id}: counted distinct value crosses joint groups",
                        context=attribute_failure_context(
                            layout, actual, (leaf,), (*parent_columns, leaf.distinct), witness
                        ),
                    )
    receipts = (
        CheckReceipt("attribute.scoped_identity_coverage", stage_id),
        CheckReceipt("attribute.disjoint_joint_partition", stage_id),
    )
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
    for leaf in layout.leaves:
        assert leaf.read.reduction is not None
        slot = leaf.read.reduction.value_slot
        for kind, target, keys in ((1, parent, parent_columns), (0, fine, full_columns)):
            completed = originals[(kind, slot)]
            completed_keys = frozen_key_rows(completed, keys)
            key_frame = normalize_stage_frame(
                target.loc[:, [column.slot_id for column in keys]], keys, ()
            )
            active = tuple(
                i
                for i in range(len(target))
                if leaf.read.reduction.window_key is None
                or target[leaf.read.reduction.calendars[0][0]].iloc[i]
                == leaf.read.reduction.window_key
            )
            target_keys = frozen_key_rows(key_frame.iloc[list(active)].reset_index(drop=True), keys)
            if len(set(completed_keys)) != len(completed_keys) or set(completed_keys) != set(
                target_keys
            ):
                raise _invalid("finished original values differ from their complete aggregate keys")
            by_key = {key: index for index, key in enumerate(completed_keys)}
            restored = pd.Series([pd.NA] * len(target), dtype=completed[slot].dtype)
            for index, key in zip(active, target_keys, strict=True):
                restored.iloc[index] = completed[slot].iloc[by_key[key]]
            target[slot] = restored
    actual_columns = {
        column.slot_id: column for column in source_columns(fine, layout.columns, layout.keys)
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
    parent = normalize_stage_frame(
        parent, tuple(columns[name] for name in parent.columns), layout.parent_keys
    )
    fine = normalize_stage_frame(fine, layout.columns, layout.keys)
    if layout.parent_keys and parent.duplicated(list(layout.parent_keys)).any():
        input_failure("missing_component_proof", f"{stage_id}: duplicate complete parent totals")
    if layout.keys and fine.duplicated(list(layout.keys)).any():
        input_failure("missing_component_proof", f"{stage_id}: duplicate complete fine inputs")
    observed_parents = set(frozen_key_rows(scope, parent_columns))
    actual_parents = set(frozen_key_rows(parent, parent_columns))
    if not observed_parents <= actual_parents:
        input_failure(
            "missing_component_proof", f"{stage_id}: missing a parent aggregate for a scoped root"
        )
    if not set(assignments) <= set(frozen_key_rows(fine, full_columns)):
        input_failure(
            "missing_component_proof", f"{stage_id}: missing a fine aggregate for an assigned root"
        )
    receipts = (
        *receipts,
        CheckReceipt("attribute.parent_fine_component_coverage", stage_id),
        *(
            (CheckReceipt("attribute.distinct_value_partition", stage_id),)
            if any(leaf.distinct is not None for leaf in layout.leaves)
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
    return fine, AttributeRootInputs(layout, stage_id, parent, fine, receipts, states)


def complete_attribute_parent_domain(
    root: AttributeRootInputs, projection: StageOutput
) -> AttributeRootInputs:
    """Retain every selected parent, distinguishing checked emptiness from absence."""
    from marivo._definition.expressions import EntityCount, SourceAggregate, SourceWeightedMean
    from marivo.analysis.core.attribute import AttributeCheckWitness
    from marivo.analysis.core.frozen_calendar import frozen_key_rows
    from marivo.analysis.materialization.graph_source_execution import (
        _attribute_source_check_failure,
    )

    columns = {column.slot_id: column for column in root.layout.columns}
    parent_columns = tuple(columns[name] for name in root.layout.parent_keys)
    calendar_slots = {
        slot
        for leaf in root.layout.leaves
        if leaf.read.reduction is not None
        for slot, _ in leaf.read.reduction.calendars
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
            reduction = leaf.read.reduction
            assert reduction is not None
            node = reduction.node
            if isinstance(node, SourceWeightedMean) and node.zero_denominator == "error":
                witness = AttributeCheckWitness(
                    "__attribute_empty_parent_denominator",
                    leaf.read.source_id,
                    (key,),
                    parent_columns,
                )
                _attribute_source_check_failure(
                    leaf.read,
                    "__attribute_empty_parent_denominator",
                    "a nonzero denominator",
                    "weighted_mean(zero_denominator='error')",
                    selected.iloc[[index]],
                    attribute_failure_context(
                        root.layout, selected.iloc[[index]], (leaf,), parent_columns, witness
                    ),
                )
            zero = isinstance(node, EntityCount) or (
                isinstance(node, SourceAggregate)
                and (
                    node.reducer in ("count", "count_distinct")
                    or (node.reducer == "sum" and node.empty == "zero")
                )
            )
            record[reduction.value_slot] = 0 if zero else None
            record[leaf.support.slot_id] = 0
            record[leaf.effective.slot_id] = 0
            record.update({column.slot_id: 0 for _, column in leaf.roles})
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
