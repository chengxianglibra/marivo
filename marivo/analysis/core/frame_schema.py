"""Immutable logical Frame structure and the shared flat export layout."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, TypeAlias

from marivo.analysis.core.data_schema import (
    BuiltinCalendarData,
    CalendarComparisonBinding,
    CalendarDataAxis,
    DataAxis,
    DataComparison,
    DataSchema,
    IntervalComparisonBinding,
    IntervalDataAxis,
    NamedCalendarData,
    PairedDataAxis,
)
from marivo.analysis.core.data_types import DataColumn, require_data_text
from marivo.analysis.core.frame_interpretation import (
    FrameAlignment,
    FrameRowAttributeTime,
)
from marivo.analysis.core.frame_interpretation import (
    FrameAxisSchema as FrameAxisSchema,
)
from marivo.analysis.core.frame_interpretation import (
    FrameBuiltinGrain as FrameBuiltinGrain,
)
from marivo.analysis.core.frame_interpretation import (
    FrameCalendarAxisSchema as FrameCalendarAxisSchema,
)
from marivo.analysis.core.frame_interpretation import (
    FrameCalendarComparisonBinding as FrameCalendarComparisonBinding,
)
from marivo.analysis.core.frame_interpretation import (
    FrameCalendarEndpoint as FrameCalendarEndpoint,
)
from marivo.analysis.core.frame_interpretation import (
    FrameChangeTarget as FrameChangeTarget,
)
from marivo.analysis.core.frame_interpretation import (
    FrameComparedTime as FrameComparedTime,
)
from marivo.analysis.core.frame_interpretation import (
    FrameComparison as FrameComparison,
)
from marivo.analysis.core.frame_interpretation import (
    FrameComparisonGroup as FrameComparisonGroup,
)
from marivo.analysis.core.frame_interpretation import (
    FrameComparisonPresence as FrameComparisonPresence,
)
from marivo.analysis.core.frame_interpretation import (
    FrameComparisonValue as FrameComparisonValue,
)
from marivo.analysis.core.frame_interpretation import (
    FrameDataType as FrameDataType,
)
from marivo.analysis.core.frame_interpretation import (
    FrameDecomposition as FrameDecomposition,
)
from marivo.analysis.core.frame_interpretation import (
    FrameField as FrameField,
)
from marivo.analysis.core.frame_interpretation import (
    FrameFixedAxis as FrameFixedAxis,
)
from marivo.analysis.core.frame_interpretation import (
    FrameGrain as FrameGrain,
)
from marivo.analysis.core.frame_interpretation import (
    FrameIntervalAxisSchema as FrameIntervalAxisSchema,
)
from marivo.analysis.core.frame_interpretation import (
    FrameIntervalComparisonBinding as FrameIntervalComparisonBinding,
)
from marivo.analysis.core.frame_interpretation import (
    FrameIntervalEndpoint as FrameIntervalEndpoint,
)
from marivo.analysis.core.frame_interpretation import (
    FrameLevelTarget as FrameLevelTarget,
)
from marivo.analysis.core.frame_interpretation import (
    FrameNamedGrain as FrameNamedGrain,
)
from marivo.analysis.core.frame_interpretation import (
    FrameObservationContext as FrameObservationContext,
)
from marivo.analysis.core.frame_interpretation import (
    FrameOrdinaryAxisSchema as FrameOrdinaryAxisSchema,
)
from marivo.analysis.core.frame_interpretation import (
    FrameOrdinaryComparisonBinding as FrameOrdinaryComparisonBinding,
)
from marivo.analysis.core.frame_interpretation import (
    FramePairedAxisSchema as FramePairedAxisSchema,
)
from marivo.analysis.core.frame_interpretation import (
    FrameTimeEndpoint as FrameTimeEndpoint,
)
from marivo.analysis.core.frame_interpretation import (
    FrameValueContext as FrameValueContext,
)
from marivo.analysis.core.frame_interpretation import (
    UnresolvedDataType as UnresolvedDataType,
)
from marivo.analysis.core.frame_structure import (
    PendingDataAxis,
    PendingDataColumn,
    PendingDataSchema,
    axis_role,
)
from marivo.analysis.core.model import reject


def _invalid(received: str) -> None:
    reject(
        "a captured immutable Frame structure",
        received,
        "Read schema from the exact Frame whose fields you need.",
        "frame.schema",
        canonical_id="analysis_frame.schema",
    )


@dataclass(frozen=True, slots=True, repr=False)
class FieldExportColumn:
    """A flat value or presence export column, acquired from schema or page layout."""

    name: str
    slot_id: str
    kind: Literal["field"] = field(default="field", init=False)

    def __repr__(self) -> str:
        return (
            f"<FieldExportColumn name={self.name[:64]!r} slot={self.slot_id[:64]!r}; use .show()>"
        )

    def show(self) -> None:
        """Print field export identity. Args: None. Returns: None.

        Example: ``frame.schema.layout[-1].show()``.
        Constraints: Resolve slot_id only within the matching schema input.
        """
        print(repr(self))


@dataclass(frozen=True, slots=True, repr=False)
class CoordinateExportColumn:
    """A flat readable coordinate column, acquired from schema or page layout."""

    name: str
    axis_token: str
    endpoint: Literal["self", "left", "right"]
    part: Literal[
        "key",
        "label",
        "start",
        "end",
        "nominal_start",
        "nominal_end",
    ]
    kind: Literal["coordinate"] = field(default="coordinate", init=False)

    def __repr__(self) -> str:
        return f"<CoordinateExportColumn name={self.name[:32]!r} axis={self.axis_token[:32]!r} endpoint={self.endpoint} part={self.part}; use .show()>"

    def show(self) -> None:
        """Print coordinate export identity. Args: None. Returns: None.

        Example: ``frame.schema.layout[0].show()``.
        Constraints: Display labels are separate from the complete logical key.
        """
        print(repr(self))


FrameExportColumn: TypeAlias = FieldExportColumn | CoordinateExportColumn
LogicalSchema: TypeAlias = DataSchema | PendingDataSchema


def selected_columns(
    schema: LogicalSchema, columns: tuple[str, ...] | None
) -> tuple[DataColumn | PendingDataColumn, ...]:
    """Validate complete field names before any payload read, retaining request order."""
    available = (*schema.values, *schema.presence)
    if columns is None:
        return available
    names = tuple(column.name for column in available)
    if (
        type(columns) is not tuple
        or any(type(name) is not str for name in columns)
        or len(set(columns)) != len(columns)
        or any(name not in names for name in columns)
    ):
        reject(
            "distinct complete value or presence names",
            repr(columns),
            f"Choose columns from {names!r}; use columns=() for coordinates only.",
            "frame.read_page.columns",
            canonical_id="analysis_frame.read_page",
        )
    by_name = {column.name: column for column in available}
    return tuple(by_name[name] for name in columns)


def axis_export_layout(
    axis: DataAxis | PendingDataAxis,
    name: str,
    *,
    token: str | None = None,
    endpoint: Literal["self", "left", "right"] = "self",
    time_details: bool = False,
) -> tuple[CoordinateExportColumn, ...]:
    token = axis.token if token is None else token
    if isinstance(axis, PairedDataAxis):
        return (
            *axis_export_layout(
                axis.left, f"{name}.left", token=token, endpoint="left", time_details=time_details
            ),
            *axis_export_layout(
                axis.right,
                f"{name}.right",
                token=token,
                endpoint="right",
                time_details=time_details,
            ),
        )
    if isinstance(axis, (CalendarDataAxis, IntervalDataAxis)):
        bounds = (
            CoordinateExportColumn(f"{name}.start", token, endpoint, "start"),
            CoordinateExportColumn(f"{name}.end", token, endpoint, "end"),
        )
        if isinstance(axis, CalendarDataAxis) and time_details:
            return (
                *bounds,
                CoordinateExportColumn(f"{name}.label", token, endpoint, "label"),
                CoordinateExportColumn(f"{name}.nominal_start", token, endpoint, "nominal_start"),
                CoordinateExportColumn(f"{name}.nominal_end", token, endpoint, "nominal_end"),
            )
        return bounds
    return (CoordinateExportColumn(name, token, endpoint, "key"),)


def export_layout(
    schema: LogicalSchema,
    axis_names: tuple[str, ...] | None = None,
    columns: tuple[str, ...] | None = None,
    *,
    validate_names: bool = False,
    time_details: bool = False,
    member: Literal["to_pandas", "read_page"] = "to_pandas",
) -> tuple[FrameExportColumn, ...]:
    """Derive the one export layout; logical schema permits default name collisions."""
    fields = selected_columns(schema, columns)
    aliases = (
        tuple(axis.name.rsplit(".", 1)[-1] for axis in schema.axes)
        if axis_names is None
        else axis_names
    )
    layout: tuple[FrameExportColumn, ...] = (
        *(
            column
            for axis, alias in zip(schema.axes, aliases, strict=True)
            for column in axis_export_layout(axis, alias, time_details=time_details)
        ),
        *(FieldExportColumn(column.name, column.slot_id) for column in fields),
    )
    duplicates = tuple(name for name, count in Counter(c.name for c in layout).items() if count > 1)
    if validate_names and duplicates:
        reject(
            "unique flat export column names",
            repr(duplicates),
            "Pass axis_names={'full.axis.name': 'unique_name'} or use an axis token; "
            "rename conflicting values with select().",
            f"frame.{member}",
            canonical_id=f"analysis_frame.{member}",
        )
    return layout


@dataclass(frozen=True, slots=True, repr=False)
class FrameSchema:
    """Captured logical structure, acquired from AnalysisFrame.schema without payload I/O."""

    input_id: str
    _values: tuple[FrameField, ...]
    _presence: tuple[FrameField, ...]
    axes: tuple[FrameAxisSchema, ...]
    layout: tuple[FrameExportColumn, ...]
    fixed_axes: tuple[FrameFixedAxis, ...] = ()
    _value_contexts: tuple[tuple[str, FrameValueContext], ...] = ()
    comparisons: tuple[FrameComparison, ...] = ()
    decompositions: tuple[FrameDecomposition, ...] = ()
    alignments: tuple[FrameAlignment, ...] = ()

    def __post_init__(self) -> None:
        require_data_text(self.input_id, "frame.schema.input_id")
        fields = (*self._values, *self._presence, *(key for axis in self.axes for key in axis.keys))
        if len({item.slot_id for item in fields}) != len(fields):
            _invalid("unique slots within this exact Frame input")

    @property
    def value_contexts(self) -> Mapping[str, FrameValueContext]:
        """Return captured current value explanations by complete name; missing means unspecified.

        Args: None. Returns: Read-only mapping. Example: ``frame.schema.value_contexts``.
        Constraints: Captured facts only; never reads definitions or source rows.
        """
        return MappingProxyType(dict(self._value_contexts))

    @property
    def values(self) -> Mapping[str, FrameField]:
        """Return complete value names. Args: None. Returns: Read-only field mapping.

        Example: ``frame.schema.values['revenue']``. Constraints: Metadata only.
        """
        return MappingProxyType({item.name: item for item in self._values})

    @property
    def presence(self) -> Mapping[str, FrameField]:
        """Return complete presence names. Args: None. Returns: Read-only field mapping.

        Example: ``frame.schema.presence``. Constraints: Separate from value names.
        """
        return MappingProxyType({item.name: item for item in self._presence})

    @property
    def slots(self) -> Mapping[str, FrameField]:
        """Return every value, presence and axis key by exact slot identity.

        Args: None. Returns: Read-only field mapping.
        Example: ``frame.schema.slots[coordinate.slot_id]``.
        Constraints: slot_id is not a cross-result business identity.
        """
        return MappingProxyType(
            {
                item.slot_id: item
                for item in (
                    *self._values,
                    *self._presence,
                    *(key for axis in self.axes for key in axis.keys),
                )
            }
        )

    def __repr__(self) -> str:
        return f"<FrameSchema input={self.input_id[:64]!r} axes={len(self.axes)} values={len(self._values)} presence={len(self._presence)}; use .show()>"

    def show(self) -> None:
        """Print bounded current structure and interpretation facts.

        Args: None. Returns: None. Example: ``frame.schema.show()``.
        Constraints: Metadata only, with an 8192-byte budget and representative
        entries; typed directories retain every captured fact.
        """
        from marivo.render import Card

        card = Card(
            identity=repr(self),
            available=(
                ".axes",
                ".values",
                ".presence",
                ".fixed_axes",
                ".value_contexts",
                ".comparisons",
                ".alignments",
                ".decompositions",
            ),
        )
        for name, entries in (
            ("axes", self.axes),
            ("values", self._values),
            ("presence", self._presence),
            ("fixed_axes", self.fixed_axes),
            ("value_contexts", self._value_contexts),
            ("comparisons", self.comparisons),
            ("alignments", self.alignments),
            ("decompositions", self.decompositions),
        ):
            card.field(name + ".count", str(len(entries)))
            for index, item in enumerate(entries[:3]):
                if name == "value_contexts":
                    column, context = self._value_contexts[index]
                    summary = f"{column[:120]}: {context!r}"
                else:
                    summary = repr(item)
                card.field(f"{name}[{index}]", summary[:320])
            if len(entries) > 3:
                card.field(name + ".more", f"{len(entries) - 3} omitted; read .{name}")
        print(card.render(max_output_bytes=8192))


def _capture_field(column: DataColumn | PendingDataColumn) -> FrameField:
    dtype = (
        UnresolvedDataType(column.kind) if isinstance(column, PendingDataColumn) else column.dtype
    )
    return FrameField(column.slot_id, column.name, dtype, column.nullable, column.unit)


def capture_schema(input_id: str, schema: LogicalSchema) -> FrameSchema:
    """Copy logical facts only; the result owns no source, Store or internal schema."""
    return FrameSchema(
        input_id,
        tuple(_capture_field(column) for column in schema.values),
        tuple(_capture_field(column) for column in schema.presence),
        tuple(capture_axis(axis) for axis in schema.axes),
        export_layout(schema),
        schema.fixed_axes,
        schema.value_contexts,
        capture_comparisons(input_id, schema),
        schema.decompositions,
        tuple(FrameAlignment(input_id, a.alignment_id, a.left, a.right) for a in schema.alignments),
    )


def capture_grain(definition: BuiltinCalendarData | NamedCalendarData) -> FrameGrain:
    """Project only a captured nominal definition; never perform a Catalog lookup."""
    if isinstance(definition, BuiltinCalendarData):
        return FrameBuiltinGrain(definition.unit, definition.step)
    if definition.ref is None:
        _invalid("a named calendar requires its consumed semantic definition identity")
    assert definition.ref is not None
    return FrameNamedGrain(definition.ref, definition.level)


def capture_endpoint(axis: CalendarDataAxis | IntervalDataAxis) -> FrameTimeEndpoint:
    assert not isinstance(axis.at, FrameRowAttributeTime)
    if isinstance(axis, CalendarDataAxis):
        assert axis.frozen is not None
        return FrameCalendarEndpoint(
            axis.name, axis.at, axis.timezone, capture_grain(axis.frozen.definition)
        )
    return FrameIntervalEndpoint(axis.name, axis.at, axis.timezone)


def capture_axis(axis: DataAxis | PendingDataAxis) -> FrameAxisSchema:
    keys = tuple(_capture_field(c) for c in axis.columns)
    if isinstance(axis, CalendarDataAxis):
        assert axis.frozen is not None
        return FrameCalendarAxisSchema(
            axis.token,
            axis.name,
            "calendar",
            keys,
            axis.timezone,
            capture_grain(axis.frozen.definition),
            at=axis.at,
        )
    if isinstance(axis, IntervalDataAxis):
        return FrameIntervalAxisSchema(
            axis.token, axis.name, "interval", keys, axis.timezone, at=axis.at
        )
    if isinstance(axis, PairedDataAxis):
        return FramePairedAxisSchema(
            axis.token,
            axis.name,
            "paired",
            keys,
            capture_endpoint(axis.left),
            capture_endpoint(axis.right),
            at=axis.at,
        )
    return FrameOrdinaryAxisSchema(axis.token, axis.name, axis_role(axis), keys, at=axis.at)


def comparison_id(fact: DataComparison) -> str:
    return ".".join((*fact.namespace, fact.alias))


def capture_comparisons(input_id: str, schema: LogicalSchema) -> tuple[FrameComparison, ...]:
    result: list[FrameComparison] = []
    for fact in schema.comparisons:
        binding = fact.binding
        public_binding: (
            FrameOrdinaryComparisonBinding
            | FrameCalendarComparisonBinding
            | FrameIntervalComparisonBinding
        )
        if isinstance(binding, CalendarComparisonBinding):
            endpoint = capture_endpoint(binding.baseline)
            assert isinstance(endpoint, FrameCalendarEndpoint)
            public_binding = FrameCalendarComparisonBinding(
                binding.current_axis_token, "self", endpoint
            )
        elif isinstance(binding, IntervalComparisonBinding):
            endpoint = capture_endpoint(binding.baseline)
            assert isinstance(endpoint, FrameIntervalEndpoint)
            public_binding = FrameIntervalComparisonBinding(
                binding.current_axis_token, "self", endpoint
            )
        else:
            public_binding = FrameOrdinaryComparisonBinding()
        sources = tuple(
            dict.fromkeys(
                (*tuple(v.source_path for v in fact.values), *tuple(p for p, _, _ in fact.contexts))
            )
        )
        contexts = {p: (c, b) for p, c, b in fact.contexts}
        required = {
            d.target.group_id
            for d in schema.decompositions
            if isinstance(d.target, FrameChangeTarget)
            and d.target.comparison_id == comparison_id(fact)
        }
        for decomposition in schema.decompositions:
            target = decomposition.target
            if (
                isinstance(target, FrameLevelTarget)
                and isinstance(target.context, FrameObservationContext)
                and isinstance(target.context.time, FrameComparedTime)
                and target.context.time.comparison_id == comparison_id(fact)
            ):
                required.update(".".join(path) for path, _, _ in fact.contexts)
        groups = tuple(
            FrameComparisonGroup(
                ".".join(path),
                ".".join(path),
                tuple(
                    FrameComparisonValue(v.role, ".".join(v.value_path))
                    for v in fact.values
                    if v.source_path == path
                ),
                *contexts.get(path, (None, None)),
            )
            for path in sources
            if any(v.source_path == path for v in fact.values) or ".".join(path) in required
        )
        if not groups:
            continue
        presence = (
            FrameComparisonPresence(
                ".".join(fact.current_presence.path),
                ".".join(fact.baseline_presence.path),
                tuple(".".join(p.path) for p in fact.enclosing_presence),
            )
            if fact.current_presence is not None and fact.baseline_presence is not None
            else None
        )
        result.append(
            FrameComparison(
                input_id,
                comparison_id(fact),
                fact.alias if fact.named else None,
                groups,
                public_binding,
                presence,
                fact.current_fixed_axes,
                fact.baseline_fixed_axes,
            )
        )
    return tuple(result)
