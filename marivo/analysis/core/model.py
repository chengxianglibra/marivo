"""Bound semantic signatures, cells, parts, and evidence for the private core."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, NoReturn, TypeAlias

from marivo.analysis.anchors import CalendarWindow, ElapsedWindow
from marivo.analysis.core.domain_captures import (
    EntryAxisCapture,
    EventCapture,
    OrderCapture,
    StateModelCapture,
)
from marivo.analysis.core.history_types import HistoryRequest
from marivo.analysis.core.time_grid import BoundTimeGrid, GridVersionSelection
from marivo.analysis.domains.completeness import CompletenessDeclaration
from marivo.analysis.errors import AnalysisError, AnalysisRepair
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import DimensionKind, EntityKind, MetricKind, Ref, SemanticKind
from marivo.semantic.ir import TargetSnapshotSelection, TargetValiditySelection
from marivo.semantic.runtime_metric import RuntimeMetricExpr


class CoreRuleError(AnalysisError):
    """A private algebra construction has an invalid input or missing premise."""

    def __init__(self, *, expected: str, received: str, repair: str, location: str) -> None:
        super().__init__(
            message="Analysis core rule validation failed.",
            expected=expected,
            received=received,
            location=location,
            repair=AnalysisRepair(
                kind="retry",
                action=repair,
                help_target=LiveHelpTarget(surface="analysis"),
            ),
        )


def reject(expected: str, received: str, repair: str, location: str) -> NoReturn:
    raise CoreRuleError(expected=expected, received=received, repair=repair, location=location)


def _nonempty(value: str, location: str) -> None:
    if type(value) is not str or not value:
        reject(
            "a non-empty bound identifier",
            type(value).__name__,
            "Supply the exact owner value.",
            location,
        )


def _unique(values: tuple[str, ...], location: str) -> None:
    if type(values) is not tuple or any(type(value) is not str or not value for value in values):
        reject(
            "an immutable tuple of identifiers",
            type(values).__name__,
            "Use exact identifiers.",
            location,
        )
    if len(set(values)) != len(values):
        reject("distinct identifiers", repr(values), "Remove the repeated binding.", location)


@dataclass(frozen=True, slots=True)
class Binding:
    """Session, owner, input and scope of one conditional construction."""

    session_id: str
    owner_id: str
    input_id: str
    scope_id: str

    def __post_init__(self) -> None:
        for name in ("session_id", "owner_id", "input_id", "scope_id"):
            _nonempty(getattr(self, name), f"core.binding.{name}")


CoordinateRole: TypeAlias = Literal["identity", "version", "group", "anchor", "instance"]


@dataclass(frozen=True, slots=True)
class Coordinate:
    """A whole typed coordinate component, never a display-value equivalence."""

    entity_ref: Ref[EntityKind]
    field: str
    role: CoordinateRole

    def __post_init__(self) -> None:
        if type(self.entity_ref) is not Ref or self.entity_ref.kind is not SemanticKind.ENTITY:
            reject(
                "an exact Entity Ref",
                type(self.entity_ref).__name__,
                "Bind an Entity Ref.",
                "core.coordinate",
            )
        _nonempty(self.field, "core.coordinate.field")
        if self.role not in ("identity", "version", "group", "anchor", "instance"):
            reject(
                "a closed coordinate role",
                str(self.role),
                "Use the owning coordinate role.",
                "core.coordinate",
            )


DomainKind: TypeAlias = Literal[
    "entity", "group", "singleton", "journey", "interval", "anchor", "occurrence"
]


@dataclass(frozen=True, slots=True)
class Correspondence:
    """Role, multiplicity and conditional facts of one explicit domain mapping."""

    source_definition_id: str
    target_definition_id: str
    role: Literal["pair", "group", "subject", "union"]
    multiplicity: Literal["preserve", "set_image", "group", "paired"]
    premises: tuple[FactKind, ...]

    def __post_init__(self) -> None:
        _nonempty(self.source_definition_id, "core.correspondence.source")
        _nonempty(self.target_definition_id, "core.correspondence.target")
        expected_multiplicity = {
            "pair": "paired",
            "group": "group",
            "subject": "set_image",
            "union": "preserve",
        }
        if expected_multiplicity.get(self.role) != self.multiplicity:
            reject(
                "a closed mapping role and multiplicity",
                repr(self),
                "Bind an explicit mapping kind.",
                "core.correspondence",
            )
        if len(set(self.premises)) != len(self.premises):
            reject(
                "distinct mapping premises",
                repr(self.premises),
                "List each premise once.",
                "core.correspondence",
            )
        if any(
            premise not in ("mapping_total", "single_value", "mapping_injective", "key_set_equal")
            for premise in self.premises
        ):
            reject(
                "closed mapping premises",
                repr(self.premises),
                "Bind the mapping's exact conditions.",
                "core.correspondence",
            )


@dataclass(frozen=True, slots=True)
class DomainSignature:
    """Symbolic instance and target coordinates; no actual membership is stored."""

    binding: Binding
    kind: DomainKind
    instance_key: tuple[Coordinate, ...]
    target_key: tuple[Coordinate, ...]
    definition_id: str
    version_selection: (
        TargetSnapshotSelection | TargetValiditySelection | GridVersionSelection | None
    ) = None
    correspondence: Correspondence | None = None
    time_grid: BoundTimeGrid | None = None

    def __post_init__(self) -> None:
        _nonempty(self.definition_id, "core.domain.definition")
        if self.kind not in (
            "entity",
            "group",
            "singleton",
            "journey",
            "interval",
            "anchor",
            "occurrence",
        ):
            reject(
                "a closed instance kind",
                str(self.kind),
                "Use a registered domain kind.",
                "core.domain",
            )
        for name, coordinates in (
            ("instance_key", self.instance_key),
            ("target_key", self.target_key),
        ):
            if type(coordinates) is not tuple or any(
                type(item) is not Coordinate for item in coordinates
            ):
                reject(
                    "a tuple of typed coordinates",
                    type(coordinates).__name__,
                    "Bind complete coordinates.",
                    f"core.domain.{name}",
                )
            if len(set(coordinates)) != len(coordinates):
                reject(
                    "distinct coordinate components",
                    repr(coordinates),
                    "Remove duplicate coordinates.",
                    f"core.domain.{name}",
                )
        if self.kind != "singleton" and not self.instance_key:
            reject(
                "a complete non-empty instance key",
                "empty key",
                "Bind the full instance identity.",
                "core.domain",
            )
        if self.kind == "singleton" and (self.instance_key or self.target_key):
            reject(
                "empty singleton coordinates",
                "keyed singleton",
                "Use an unkeyed singleton.",
                "core.domain",
            )
        if self.version_selection is not None and not isinstance(
            self.version_selection,
            (TargetSnapshotSelection, TargetValiditySelection, GridVersionSelection),
        ):
            reject(
                "an exact Semantic version selection",
                type(self.version_selection).__name__,
                "Resolve the version selection through Semantic.",
                "core.domain.version",
            )
        if (
            self.correspondence is not None
            and self.correspondence.target_definition_id != self.definition_id
        ):
            reject(
                "correspondence to this output domain",
                self.correspondence.target_definition_id,
                "Bind the exact target domain.",
                "core.domain.correspondence",
            )


@dataclass(frozen=True, slots=True)
class ObservedQuantity:
    definition_id: str
    metric_ref: Ref[MetricKind] | RuntimeMetricExpr
    graph_fingerprint: str
    unit: str | None
    time_scope: str
    contribution_id: str
    value_policy: str
    method_version: str
    kind: Literal["observed"] = field(default="observed", init=False)


@dataclass(frozen=True, slots=True)
class DerivedQuantity:
    definition_id: str
    method_version: str
    input_ids: tuple[str, ...]
    unit: str | None
    time_scope: str
    value_policy: str
    kind: Literal["derived"] = field(default="derived", init=False)


@dataclass(frozen=True, slots=True)
class RowStatisticQuantity:
    definition_id: str
    method_version: str
    input_id: str
    input_domain_id: str
    unit: str | None
    time_scope: str
    value_policy: str
    weighting: str
    kind: Literal["row_statistic"] = field(default="row_statistic", init=False)


@dataclass(frozen=True, slots=True)
class RolledQuantity:
    definition_id: str
    original_id: str
    method_version: str
    contribution_id: str
    unit: str | None
    time_scope: str
    value_policy: str
    kind: Literal["original_rollup"] = field(default="original_rollup", init=False)


Quantity: TypeAlias = ObservedQuantity | DerivedQuantity | RowStatisticQuantity | RolledQuantity


def validate_quantity(quantity: Quantity) -> None:
    _nonempty(quantity.definition_id, "core.quantity.definition")
    _nonempty(quantity.method_version, "core.quantity.method")
    _nonempty(quantity.time_scope, "core.quantity.time")
    _nonempty(quantity.value_policy, "core.quantity.policy")
    if quantity.unit is not None:
        _nonempty(quantity.unit, "core.quantity.unit")
    if isinstance(quantity, ObservedQuantity):
        reference = quantity.metric_ref
        if isinstance(reference, RuntimeMetricExpr):
            pass
        elif type(reference) is not Ref or reference.kind is not SemanticKind.METRIC:
            reject(
                "an exact Metric Ref or runtime expression",
                type(reference).__name__,
                "Bind a Metric Ref or a closed runtime expression.",
                "core.quantity.metric",
            )
        _nonempty(quantity.contribution_id, "core.quantity.contribution")
        _nonempty(quantity.graph_fingerprint, "core.quantity.graph")
    elif isinstance(quantity, DerivedQuantity):
        if (
            type(quantity.input_ids) is not tuple
            or not quantity.input_ids
            or any(type(value) is not str or not value for value in quantity.input_ids)
        ):
            reject(
                "at least one input quantity",
                "none",
                "Bind the input quantity.",
                "core.quantity.inputs",
            )
    elif isinstance(quantity, RowStatisticQuantity):
        for value in (quantity.input_id, quantity.input_domain_id, quantity.weighting):
            _nonempty(value, "core.quantity.row_statistic")
    elif isinstance(quantity, RolledQuantity):
        _nonempty(quantity.original_id, "core.quantity.original")
        _nonempty(quantity.contribution_id, "core.quantity.contribution")
    else:
        reject(
            "a closed quantity definition",
            type(quantity).__name__,
            "Use a core quantity variant.",
            "core.quantity",
        )


@dataclass(frozen=True, slots=True)
class Defined:
    value: bool | int | float | str | date | datetime | Decimal

    def __post_init__(self) -> None:
        if type(self.value) not in (bool, int, float, str, date, datetime, Decimal):
            reject(
                "a typed scalar Cell value",
                type(self.value).__name__,
                "Decode the declared value type.",
                "core.cell.defined",
            )


@dataclass(frozen=True, slots=True)
class Null:
    reason: str

    def __post_init__(self) -> None:
        _nonempty(self.reason, "core.cell.null")


@dataclass(frozen=True, slots=True)
class Undefined:
    reason: str

    def __post_init__(self) -> None:
        _nonempty(self.reason, "core.cell.undefined")


@dataclass(frozen=True, slots=True)
class Unknown:
    reason: str

    def __post_init__(self) -> None:
        _nonempty(self.reason, "core.cell.unknown")


Cell: TypeAlias = Defined | Null | Undefined | Unknown


@dataclass(frozen=True, slots=True)
class MissingCoordinate:
    side: Literal["current", "baseline"]
    key: tuple[str | int, ...]

    def __post_init__(self) -> None:
        if (
            self.side not in ("current", "baseline")
            or type(self.key) is not tuple
            or any(type(component) not in (str, int) for component in self.key)
        ):
            reject(
                "a side and complete missing key",
                repr(self.key),
                "Repair the exact pairing.",
                "core.pair",
            )


@dataclass(frozen=True, slots=True)
class SubjectPart:
    binding: Binding
    entity_ref: Ref[EntityKind]
    source_key: tuple[Coordinate, ...]
    subject_key: tuple[Coordinate, ...]
    injective: bool
    total: bool
    version: str


@dataclass(frozen=True, slots=True)
class EndpointPart:
    binding: Binding
    side: Literal["current", "baseline"]
    quantity_id: str
    version: str
    original_state: OriginalStatePart | None = None
    coordinate_state: CoordinateStatePart | None = None


@dataclass(frozen=True, slots=True)
class CorrespondencePart:
    """Retained ordered endpoint coordinates, independent of endpoint Cell tags."""

    binding: Binding
    current_key: tuple[Coordinate, ...]
    baseline_key: tuple[Coordinate, ...]
    version: str
    policy: Literal["exact", "keep", "metric_empty"] = "exact"
    empty_rules: tuple[Literal["null", "zero", "zero_denominator"], ...] = ()
    time_index: int | None = None
    bucket_mapping: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class OriginalStatePart:
    binding: Binding
    quantity_id: str
    method_version: str
    contribution_id: str
    components: tuple[str, ...]
    version: str
    empty_rules: tuple[Literal["null", "zero"], ...] = ()
    temporal_policy: Literal["none", "partition", "repeated", "overlapping"] = "none"
    fold_kind: Literal["first", "last", "mean", "min", "max"] | None = None


@dataclass(frozen=True, slots=True)
class CoordinateStatePart:
    binding: Binding
    quantity_id: str
    dimension: Ref[DimensionKind]
    owner: Ref[EntityKind]
    components: tuple[str, ...]
    value_type: str
    version: Literal["v1"]

    extra_coordinates: tuple[Coordinate, ...] = ()
    attribution_only: bool = False

    @property
    def coordinates(self) -> tuple[Coordinate, ...]:
        return (Coordinate(self.owner, self.dimension.path, "group"), *self.extra_coordinates)

    @property
    def columns(self) -> tuple[str, ...]:
        return tuple(
            "coordinate" if i == 0 else f"coordinate_{i}" for i in range(len(self.coordinates))
        )

    def column_for(self, dimension: Ref[DimensionKind]) -> str:
        return self.columns[tuple(c.field for c in self.coordinates).index(dimension.path)]


@dataclass(frozen=True, slots=True)
class RowStatePart:
    binding: Binding
    quantity_id: str
    method_version: str
    input_domain_id: str
    components: tuple[str, ...]
    version: str


@dataclass(frozen=True, slots=True)
class CoveragePart:
    binding: Binding
    quantity_id: str
    scope_id: str
    version: str


@dataclass(frozen=True, slots=True)
class FixedReferencePart:
    binding: Binding
    reference_id: str
    scope_id: str
    version: str


@dataclass(frozen=True, slots=True)
class ReferenceStatePart:
    """An independently keyed immutable input to a reference method."""

    binding: Binding
    role: Literal["fixed_reference", "reference_proof", "strata", "stratum_values"]
    domain: DomainSignature
    reference_id: str
    original_state: OriginalStatePart | None = None
    cell_reasons: tuple[tuple[str, tuple[str, ...]], ...] = ()
    version: Literal["v1"] = "v1"


@dataclass(frozen=True, slots=True)
class DisplayPart:
    """Closed display data with independently retained original ranking scope."""

    binding: Binding
    role: Literal[
        "values", "ranks", "ranking_domain", "partitions", "ordering", "columns", "column_bindings"
    ]
    components: tuple[str, ...]
    types: tuple[str, ...]
    identity: str
    independent: bool = False
    order: Literal["ascending", "descending"] = "ascending"
    ties: Literal["ordinal", "dense", "min", "max"] = "ordinal"
    version: Literal["v1"] = "v1"


@dataclass(frozen=True, slots=True)
class AttributionPart:
    """Typed allocation evidence, independently keyed to its original scope."""

    binding: Binding
    role: Literal[
        "current_endpoint",
        "baseline_endpoint",
        "basis",
        "allocation",
        "reconciliation",
        "selection_scope",
    ]
    domain: DomainSignature
    axes: tuple[Ref[DimensionKind], ...]
    method: Literal["additive_difference", "component_mix"]
    mode: Literal["joint", "hierarchy"]
    top_k: int | None
    endpoint: EndpointPart | None = None
    complete: bool = True
    view: Literal["contribution", "current", "baseline"] = "contribution"
    version: Literal["v1"] = "v1"


@dataclass(frozen=True, slots=True)
class StatisticalWeightPart:
    binding: Binding
    role_id: str
    unit: str
    version: str


@dataclass(frozen=True, slots=True)
class PairCountsPart:
    binding: Binding
    left_quantity_id: str
    right_quantity_id: str
    version: str


@dataclass(frozen=True, slots=True)
class CohortDecisionPart:
    binding: Binding
    opportunity_domain: DomainSignature
    rule: Literal["any", "at_least", "all"]
    count: int
    empty: Literal["true", "false", "undefined"]
    version: str = "v1"


@dataclass(frozen=True, slots=True)
class OccurrencePart:
    binding: Binding
    events: tuple[EventCapture, ...]
    order: OrderCapture | None
    model: StateModelCapture | None
    components: tuple[str, ...]
    preparation_id: str
    start: str | None
    end: str
    completeness: tuple[CompletenessDeclaration, ...]
    order_use: Literal["prepare", "ordered", "one_step_every_start", "after_terminal"]
    terminal_state: str | None
    version: str = "v1"


@dataclass(frozen=True, slots=True)
class HistoryPart:
    binding: Binding
    preparation: OccurrencePart
    capture_domain: DomainSignature
    window_start: str
    window_end: str
    version: Literal["v1"] = "v1"


@dataclass(frozen=True, slots=True)
class HistoryViewPart:
    binding: Binding
    history: HistoryPart
    request: HistoryRequest
    complete: bool = True
    version: Literal["v1"] = "v1"


@dataclass(frozen=True, slots=True)
class JourneyPart:
    binding: Binding
    preparation: OccurrencePart
    steps: tuple[str, ...]
    events: tuple[str, ...]
    policy: Literal["first_per_subject", "exclusive", "shared"]
    cohort_start: str
    cohort_end: str
    completion_through: str
    complete: bool = True
    version: str = "v1"


@dataclass(frozen=True, slots=True)
class AnchorDomainPart:
    binding: Binding
    preparation: OccurrencePart
    during_start: str
    during_end: str
    journey: JourneyPart | None = None
    components: tuple[str, ...] = ("started_at", "sequence_int", "sequence_enum")
    version: Literal["v1"] = "v1"


@dataclass(frozen=True, slots=True)
class AnchorObservationPart:
    binding: Binding
    domain: AnchorDomainPart
    window: ElapsedWindow | CalendarWindow
    component_roots: tuple[Ref[EntityKind], ...]
    component_types: tuple[str, ...]
    components: tuple[str, ...]
    version: Literal["v1"] = "v1"


@dataclass(frozen=True, slots=True)
class EntryAxesPart:
    binding: Binding
    cohort_start: str
    cohort_end: str
    occurrence: OccurrencePart
    axes: tuple[EntryAxisCapture, ...]
    first_event: str
    version: Literal["v1"] = "v1"


@dataclass(frozen=True, slots=True)
class FunnelPart:
    binding: Binding
    capture_scope: str
    journey: JourneyPart
    axes: tuple[EntryAxisCapture, ...]
    population_id: str
    complete: bool = True
    version: Literal["v1"] = "v1"


@dataclass(frozen=True, slots=True)
class FunnelComparisonPart:
    binding: Binding
    current: FunnelPart
    baseline: FunnelPart
    complete: bool = True
    version: Literal["v1"] = "v1"


@dataclass(frozen=True, slots=True)
class FunnelAllocationPart:
    binding: Binding
    comparison: FunnelComparisonPart
    original: FunnelComparisonPart
    axes: tuple[Ref[DimensionKind], ...]
    target_step: int
    mode: Literal["joint", "hierarchy"]
    top_k: int | None
    complete: bool = True
    view: Literal["contribution", "current", "baseline"] = "contribution"
    version: Literal["v1"] = "v1"


@dataclass(frozen=True, slots=True)
class FindingPolicyPart:
    binding: Binding
    producer: Literal["funnel.compare", "funnel_ratio_mix"]
    extractor: Literal["graph.funnel_delta_findings@v1", "graph.funnel_contribution_findings@v1"]
    policy: Literal["bounded_algebraic_findings@v1"] = "bounded_algebraic_findings@v1"
    version: Literal["v1"] = "v1"


Part: TypeAlias = (
    AnchorDomainPart
    | AnchorObservationPart
    | EntryAxesPart
    | FunnelPart
    | FunnelComparisonPart
    | FunnelAllocationPart
    | FindingPolicyPart
    | HistoryPart
    | HistoryViewPart
    | JourneyPart
    | OccurrencePart
    | AttributionPart
    | SubjectPart
    | CohortDecisionPart
    | EndpointPart
    | CorrespondencePart
    | OriginalStatePart
    | CoordinateStatePart
    | RowStatePart
    | CoveragePart
    | FixedReferencePart
    | ReferenceStatePart
    | DisplayPart
    | StatisticalWeightPart
    | PairCountsPart
)
PartRole: TypeAlias = Literal[
    "anchor",
    "history",
    "history_view",
    "entry_axes",
    "funnel_state",
    "finding_policy",
    "journey",
    "occurrences",
    "basis",
    "allocation",
    "reconciliation",
    "selection_scope",
    "values",
    "ranks",
    "ranking_domain",
    "partitions",
    "ordering",
    "columns",
    "column_bindings",
    "reference_proof",
    "strata",
    "stratum_values",
    "correspondence",
    "subject",
    "current_endpoint",
    "baseline_endpoint",
    "original_state",
    "allocation_state",
    "coordinate_state",
    "row_state",
    "coverage",
    "fixed_reference",
    "statistical_weight",
    "pair_counts",
    "cohort_decision",
]


def part_role(part: Part) -> PartRole:
    if isinstance(part, (AnchorDomainPart, AnchorObservationPart)):
        return "anchor"
    if isinstance(part, EntryAxesPart):
        return "entry_axes"
    if isinstance(part, (FunnelPart, FunnelComparisonPart, FunnelAllocationPart)):
        return "funnel_state"
    if isinstance(part, FindingPolicyPart):
        return "finding_policy"
    if isinstance(part, HistoryViewPart):
        return "history_view"
    if isinstance(part, HistoryPart):
        return "history"
    if isinstance(part, JourneyPart):
        return "journey"
    if isinstance(part, OccurrencePart):
        return "occurrences"
    if isinstance(part, AttributionPart):
        return part.role
    if isinstance(part, DisplayPart):
        return part.role
    if isinstance(part, ReferenceStatePart):
        return part.role
    if isinstance(part, CohortDecisionPart):
        return "cohort_decision"
    if isinstance(part, CorrespondencePart):
        return "correspondence"
    if isinstance(part, SubjectPart):
        return "subject"
    if isinstance(part, EndpointPart):
        return "current_endpoint" if part.side == "current" else "baseline_endpoint"
    if isinstance(part, OriginalStatePart):
        return "original_state"
    if isinstance(part, CoordinateStatePart):
        return "allocation_state" if part.attribution_only else "coordinate_state"
    if isinstance(part, RowStatePart):
        return "row_state"
    if isinstance(part, CoveragePart):
        return "coverage"
    if isinstance(part, FixedReferencePart):
        return "fixed_reference"
    if isinstance(part, PairCountsPart):
        return "pair_counts"
    return "statistical_weight"


def validate_part(part: Part) -> None:
    if isinstance(part, (AnchorDomainPart, AnchorObservationPart)):
        from marivo.analysis.core.anchor_rules import validate as validate_anchor

        validate_anchor(part)
        return
    if isinstance(
        part,
        (EntryAxesPart, FunnelPart, FunnelComparisonPart, FunnelAllocationPart, FindingPolicyPart),
    ):
        from marivo.analysis.core.funnel_rules import validate

        validate(part)
        return
    if isinstance(part, HistoryViewPart):
        from marivo.analysis.core.history_rules import validate as validate_history_view

        validate_history_view(part)
        return
    if isinstance(part, HistoryPart):
        from datetime import datetime

        validate_part(part.preparation)
        if (
            part.version != "v1"
            or part.preparation.model is None
            or part.preparation.start is not None
            or part.preparation.end != part.window_end
            or part.preparation.order_use != "prepare"
            or part.preparation.events != part.preparation.model.triggers
            or part.preparation.order != part.preparation.model.order
            or part.capture_domain.binding != part.binding
            or part.preparation.binding != part.binding
            or part.capture_domain.kind != "occurrence"
            or datetime.fromisoformat(part.window_start).utcoffset() is None
            or not datetime.fromisoformat(part.window_start)
            < datetime.fromisoformat(part.window_end)
        ):
            reject(
                "exact canonical History declaration",
                repr(part),
                "Rebuild replay from bound captures.",
                "core.history",
            )
        return
    if isinstance(part, JourneyPart):
        from datetime import datetime

        validate_part(part.preparation)
        if (
            type(part.complete) is not bool
            or not part.steps
            or len(set(part.steps)) != len(part.steps)
            or len(part.steps) != len(part.events)
            or not set(part.events) <= {event.ref.path for event in part.preparation.events}
            or part.policy not in ("first_per_subject", "exclusive", "shared")
            or part.version != "v1"
            or any(
                datetime.fromisoformat(value).utcoffset() is None
                for value in (part.cohort_start, part.cohort_end, part.completion_through)
            )
            or not datetime.fromisoformat(part.cohort_start)
            < datetime.fromisoformat(part.cohort_end)
            <= datetime.fromisoformat(part.completion_through)
        ):
            reject(
                "exact canonical Journey declaration",
                repr(part),
                "Rebuild matching from bound captures.",
                "core.journey",
            )
        return
    if isinstance(part, OccurrencePart):
        from hashlib import sha256

        components = (
            "occurred_at",
            *(
                ("sequence_int",)
                if part.order is not None
                and any(item.order == "integer" for item in part.order.definition.sequences)
                else ("sequence_enum",)
                if part.order is not None and part.order.definition.sequences
                else ()
            ),
        )
        expected = sha256(
            repr(
                (
                    part.events,
                    part.start,
                    part.end,
                    part.order,
                    part.model,
                    part.completeness,
                    part.order_use,
                    part.terminal_state,
                )
            ).encode()
        ).hexdigest()
        if (
            not part.events
            or part.components != components
            or part.version != "v1"
            or part.preparation_id != expected
        ):
            reject(
                "complete occurrence inputs at v1",
                repr(part),
                "Rebuild the captures.",
                "core.occurrence",
            )
        return
    if isinstance(part, AttributionPart):
        if (
            not part.axes
            or type(part.axes) is not tuple
            or any(type(a) is not Ref or a.kind is not SemanticKind.DIMENSION for a in part.axes)
            or len(set(part.axes)) != len(part.axes)
            or part.method not in ("additive_difference", "component_mix")
            or part.mode not in ("joint", "hierarchy")
            or (part.mode == "hierarchy" and len(part.axes) < 2)
            or (
                part.top_k is not None
                and (type(part.top_k) is not int or not 1 <= part.top_k <= 1000)
            )
            or part.version != "v1"
            or type(part.complete) is not bool
            or part.view not in ("contribution", "current", "baseline")
            or (part.role in ("current_endpoint", "baseline_endpoint"))
            != (part.endpoint is not None)
            or (part.endpoint is not None and part.role != part.endpoint.side + "_endpoint")
        ):
            reject(
                "closed attribution state at v1",
                repr(part),
                "Rebuild attribution from complete endpoint components.",
                "core.attribution",
            )
        if part.endpoint is not None:
            validate_part(part.endpoint)
        return
    if isinstance(part, DisplayPart):
        if (
            part.role
            not in (
                "values",
                "ranks",
                "ranking_domain",
                "partitions",
                "ordering",
                "columns",
                "column_bindings",
            )
            or not part.identity
            or part.version != "v1"
            or not part.components
            or len(set(part.components)) != len(part.components)
            or len(part.components) != len(part.types)
            or type(part.independent) is not bool
            or part.order not in ("ascending", "descending")
            or part.ties not in ("ordinal", "dense", "min", "max")
        ):
            reject(
                "a closed complete display part at v1",
                repr(part),
                "Re-execute the display definition.",
                "core.display",
            )
        return
    if isinstance(part, ReferenceStatePart):
        if (
            part.role not in ("fixed_reference", "reference_proof", "strata", "stratum_values")
            or part.version != "v1"
            or any(
                tag not in ("null", "undefined", "unknown") or not reasons
                for tag, reasons in part.cell_reasons
            )
            or len({tag for tag, _ in part.cell_reasons}) != len(part.cell_reasons)
        ):
            reject(
                "a closed reference state role at v1",
                repr(part),
                "Re-execute the reference method.",
                "core.reference",
            )
        _nonempty(part.reference_id, "core.reference.identity")
        if part.original_state is not None:
            validate_part(part.original_state)
        if (part.binding.session_id, part.binding.owner_id) != (
            part.domain.binding.session_id,
            part.domain.binding.owner_id,
        ):
            reject(
                "one reference Session and owner",
                repr(part.domain),
                "Bind this Session's reference.",
                "core.reference",
            )
        return
    if isinstance(part, CohortDecisionPart):
        if (
            part.rule not in ("any", "at_least", "all")
            or type(part.count) is not int
            or part.count < 1
            or part.empty not in ("true", "false", "undefined")
            or part.version != "v1"
        ):
            reject(
                "a closed cohort decision contract",
                repr(part),
                "Rebuild the quantifier and complete opportunity domain.",
                "analysis.cohort",
            )
        return
    if isinstance(part, CorrespondencePart):
        if (
            part.version not in ("v1", "v2")
            or part.policy not in ("exact", "keep", "metric_empty")
            or (len(part.empty_rules) != (2 if part.policy == "metric_empty" else 0))
            or any(rule not in ("zero", "null", "zero_denominator") for rule in part.empty_rules)
            or len(part.current_key) != len(part.baseline_key)
            or (part.time_index is None and bool(part.bucket_mapping))
            or (
                part.time_index is not None
                and (
                    type(part.time_index) is not int
                    or not 0 <= part.time_index < len(part.current_key)
                    or part.current_key[part.time_index].role != "anchor"
                    or part.baseline_key[part.time_index].role != "anchor"
                    or not part.bucket_mapping
                    or any(not left or not right for left, right in part.bucket_mapping)
                    or len({left for left, _ in part.bucket_mapping}) != len(part.bucket_mapping)
                    or len({right for _, right in part.bucket_mapping}) != len(part.bucket_mapping)
                )
            )
            or any(type(key) is not Coordinate for key in (*part.current_key, *part.baseline_key))
        ):
            reject(
                "v2 complete typed endpoint coordinates",
                repr(part),
                "Retain both ordered endpoint keys.",
                "core.part.correspondence",
            )
        return
    if isinstance(part, SubjectPart):
        if type(part.entity_ref) is not Ref or part.entity_ref.kind is not SemanticKind.ENTITY:
            reject(
                "an exact Subject Entity Ref",
                repr(part.entity_ref),
                "Bind the Subject Entity.",
                "core.part.subject",
            )
        if (
            not part.source_key
            or not part.subject_key
            or type(part.injective) is not bool
            or type(part.total) is not bool
        ):
            reject(
                "complete subject mapping and cardinality",
                repr(part),
                "Bind the complete Subject map.",
                "core.part.subject",
            )
    elif isinstance(part, EndpointPart):
        if part.side not in ("current", "baseline"):
            reject(
                "a named endpoint side",
                str(part.side),
                "Use current or baseline.",
                "core.part.endpoint",
            )
        _nonempty(part.quantity_id, "core.part.endpoint.quantity")
        if part.original_state is not None:
            validate_part(part.original_state)
        if part.coordinate_state is not None:
            validate_part(part.coordinate_state)
    elif isinstance(part, CoordinateStatePart):
        _nonempty(part.quantity_id, "core.part.coordinate.quantity")
        _unique(part.components, "core.part.coordinate.components")
        if (
            type(part.attribution_only) is not bool
            or len(set(part.coordinates)) != len(part.coordinates)
            or any(c.role != "group" for c in part.extra_coordinates)
            or part.dimension.kind is not SemanticKind.DIMENSION
            or part.owner.kind is not SemanticKind.ENTITY
            or (
                tuple(
                    name
                    for name in part.components
                    if name not in ("numerator_absolute_sum", "absolute_weighted_numerator")
                    and not (
                        name.startswith(("plus_", "minus_")) and name.endswith("_absolute_sum")
                    )
                )
                not in (
                    ("sum", "non_null_count"),
                    ("sum", "non_null_count", "absolute_sum"),
                    (
                        "weighted_numerator",
                        "weight_sum",
                        "non_null_pair_count",
                        "row_count",
                        "absolute_weight_sum",
                    ),
                    (
                        "numerator_sum",
                        "numerator_non_null_count",
                        "denominator_sum",
                        "denominator_non_null_count",
                        "denominator_absolute_sum",
                    ),
                    ("min", "non_null_count"),
                    ("max", "non_null_count"),
                    ("count",),
                    ("sum", "non_null_count", "row_count"),
                    ("sum", "non_null_count", "row_count", "absolute_sum"),
                    ("weighted_numerator", "weight_sum", "non_null_pair_count", "row_count"),
                    (
                        "numerator_sum",
                        "numerator_non_null_count",
                        "denominator_sum",
                        "denominator_non_null_count",
                    ),
                )
                and not (
                    len(part.components) >= 4
                    and len(
                        tuple(
                            name for name in part.components if not name.endswith("_absolute_sum")
                        )
                    )
                    % 2
                    == 0
                    and all(
                        part.components[2 * i : 2 * i + 2]
                        in (
                            (f"plus_{i}_sum", f"plus_{i}_non_null_count"),
                            (f"minus_{i}_sum", f"minus_{i}_non_null_count"),
                        )
                        for i in range(
                            len(
                                tuple(
                                    name
                                    for name in part.components
                                    if not name.endswith("_absolute_sum")
                                )
                            )
                            // 2
                        )
                    )
                )
            )
            or (
                part.value_type not in ("int64", "float64")
                and part.value_type not in tuple(f"decimal(38,{scale})" for scale in range(39))
                and part.value_type
                not in tuple(f"interval('{unit}')" for unit in ("s", "ms", "us", "ns"))
            )
            or part.version != "v1"
        ):
            reject(
                "a bound coordinate component contract",
                repr(part),
                "Retain the original contribution coordinate.",
                "core.part.coordinate",
            )
    elif isinstance(part, (OriginalStatePart, RowStatePart)):
        if isinstance(part, OriginalStatePart) and (
            ((part.method_version == "fold@v1") != (part.fold_kind is not None))
            or any(rule not in ("null", "zero") for rule in part.empty_rules)
            or (part.method_version == "ratio@v1" and len(part.empty_rules) != 2)
            or (
                part.method_version == "linear@v1"
                and len(part.empty_rules) * 2
                != len(
                    tuple(name for name in part.components if not name.endswith("_absolute_sum"))
                )
            )
        ):
            reject(
                "one empty rule per original component",
                repr(part.empty_rules),
                "Retain each component empty policy.",
                "core.part.state",
            )
        _nonempty(part.quantity_id, "core.part.state.quantity")
        _nonempty(part.method_version, "core.part.state.method")
        _unique(part.components, "core.part.state.components")
        if not part.components:
            reject(
                "at least one state component",
                "empty",
                "Retain the method state.",
                "core.part.state",
            )
        _nonempty(
            part.contribution_id if isinstance(part, OriginalStatePart) else part.input_domain_id,
            "core.part.state.binding",
        )
    elif isinstance(part, CoveragePart):
        _nonempty(part.quantity_id, "core.part.coverage.quantity")
        _nonempty(part.scope_id, "core.part.coverage.scope")
    elif isinstance(part, FixedReferencePart):
        _nonempty(part.reference_id, "core.part.reference.id")
        _nonempty(part.scope_id, "core.part.reference.scope")
    elif isinstance(part, StatisticalWeightPart):
        _nonempty(part.role_id, "core.part.weight.role")
        _nonempty(part.unit, "core.part.weight.unit")
    elif isinstance(part, PairCountsPart):
        _nonempty(part.left_quantity_id, "core.part.pairs.left")
        _nonempty(part.right_quantity_id, "core.part.pairs.right")
    else:
        reject(
            "a closed retained part", type(part).__name__, "Use a core part variant.", "core.part"
        )
    _nonempty(part.version, "core.part.version")


FactKind: TypeAlias = Literal[
    "declared_key",
    "unique_key",
    "single_value",
    "mapping_total",
    "mapping_injective",
    "key_set_equal",
    "complete_coverage",
    "contribution_partition",
    "finite_numeric",
    "state_binding",
    "field_ownership",
    "cell_policy",
    "output_key",
    "subject_image",
]


@dataclass(frozen=True, slots=True)
class FactInput:
    """Exact domain and quantity consumed by an ordered relational premise."""

    domain: DomainSignature
    quantity: Quantity | None


@dataclass(frozen=True, slots=True)
class Fact:
    kind: FactKind
    binding: Binding
    subject_id: str
    version: str
    inputs: tuple[FactInput, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in (
            "declared_key",
            "unique_key",
            "single_value",
            "mapping_total",
            "mapping_injective",
            "key_set_equal",
            "complete_coverage",
            "contribution_partition",
            "finite_numeric",
            "state_binding",
            "field_ownership",
            "cell_policy",
            "output_key",
            "subject_image",
        ):
            reject(
                "a closed premise", str(self.kind), "Use a registered fact kind.", "core.fact.kind"
            )
        _nonempty(self.subject_id, "core.fact.subject")
        _nonempty(self.version, "core.fact.version")


EvidenceBasis: TypeAlias = Literal["declaration", "builder", "check", "observation", "deduction"]


@dataclass(frozen=True, slots=True)
class Evidence:
    fact: Fact
    basis: EvidenceBasis
    source_id: str
    dependencies: tuple[Fact, ...] = ()

    def __post_init__(self) -> None:
        _nonempty(self.source_id, "core.evidence.source")
        if self.basis not in ("declaration", "builder", "check", "observation", "deduction"):
            reject(
                "a closed evidence basis", str(self.basis), "Use the owning basis.", "core.evidence"
            )
        if self.basis == "declaration" and self.fact.kind not in (
            "declared_key",
            "field_ownership",
        ):
            reject(
                "a declared definition fact",
                self.fact.kind,
                "Use a completed source check for runtime facts.",
                "core.evidence.declaration",
            )
        if self.basis == "builder" and self.fact.kind != "field_ownership":
            reject(
                "a builder-owned definition fact",
                self.fact.kind,
                "Keep conditional Post and source checks pending.",
                "core.evidence.builder",
            )
        if type(self.dependencies) is not tuple or any(
            type(fact) is not Fact for fact in self.dependencies
        ):
            reject(
                "bound fact dependencies",
                type(self.dependencies).__name__,
                "Bind each premise.",
                "core.evidence",
            )
        if self.basis == "deduction" and not self.dependencies:
            reject(
                "deduction with bound premises",
                "no dependencies",
                "Name the facts that justify this deduction.",
                "core.evidence.deduction",
            )


CheckId: TypeAlias = Literal[
    "source.unique_key@v1",
    "source.single_value@v1",
    "source.exact_pairing@v1",
    "source.group_mapping@v1",
    "source.finite_numeric@v1",
    "source.cell_policy@v1",
    "source.contribution_partition@v1",
    "source.complete_coverage@v1",
    "source.calendar_members@v1",
    "source.calendar_contributions@v1",
]

_CHECK_FACTS: dict[CheckId, frozenset[FactKind]] = {
    "source.unique_key@v1": frozenset({"unique_key"}),
    "source.single_value@v1": frozenset({"single_value"}),
    "source.exact_pairing@v1": frozenset({"key_set_equal", "single_value", "mapping_injective"}),
    "source.group_mapping@v1": frozenset({"mapping_total", "single_value"}),
    "source.finite_numeric@v1": frozenset({"finite_numeric"}),
    "source.cell_policy@v1": frozenset({"cell_policy"}),
    "source.contribution_partition@v1": frozenset({"contribution_partition"}),
    "source.complete_coverage@v1": frozenset({"complete_coverage"}),
    "source.calendar_members@v1": frozenset({"complete_coverage"}),
    "source.calendar_contributions@v1": frozenset({"complete_coverage"}),
}


@dataclass(frozen=True, slots=True)
class Obligation:
    """Typed future check handoff; it is never evidence or execution support."""

    fact: Fact
    check_id: CheckId
    before: Literal["consume", "publish"]

    def __post_init__(self) -> None:
        if self.check_id not in _CHECK_FACTS or self.fact.kind not in _CHECK_FACTS[self.check_id]:
            reject(
                "a registered check for this exact premise",
                f"{self.check_id}: {self.fact.kind}",
                "Use the check owned by this fact or reject the operation.",
                "core.obligation.check",
            )
        if self.before not in ("consume", "publish"):
            reject(
                "consume or publish", str(self.before), "Name the check stage.", "core.obligation"
            )


@dataclass(frozen=True, slots=True)
class Signature:
    domain: DomainSignature
    quantity: Quantity | None = None
    parts: tuple[Part, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    obligations: tuple[Obligation, ...] = ()

    def __post_init__(self) -> None:
        if self.quantity is not None:
            validate_quantity(self.quantity)
        roles: list[PartRole] = []
        for part in self.parts:
            validate_part(part)
            if (
                part.binding.session_id != self.domain.binding.session_id
                or part.binding.owner_id != self.domain.binding.owner_id
            ):
                reject(
                    "same Session and owner part",
                    repr(part.binding),
                    "Bind the part to this owner.",
                    "core.signature.parts",
                )
            if isinstance(part, SubjectPart) and (
                part.source_key != self.domain.instance_key
                or any(coordinate.entity_ref != part.entity_ref for coordinate in part.subject_key)
            ):
                reject(
                    "a Subject map from the exact instance key to its Entity key",
                    repr(part.source_key),
                    "Bind the complete source and Subject identities.",
                    "core.signature.subject",
                )
            if isinstance(
                part, (OriginalStatePart, RowStatePart, CoveragePart, CoordinateStatePart)
            ) and (
                part.binding != self.domain.binding
                or self.quantity is None
                or part.quantity_id != self.quantity.definition_id
            ):
                reject(
                    "a part bound to this quantity and input scope",
                    repr(part.binding),
                    "Retain the exact quantity, scope, and state binding.",
                    "core.signature.parts",
                )
            roles.append(part_role(part))
        if len(set(roles)) != len(roles):
            reject(
                "one part per role",
                repr(roles),
                "Use separate bound quantities or roles.",
                "core.signature.parts",
            )
        for item in self.evidence:
            binding = item.fact.binding
            if (
                binding.session_id != self.domain.binding.session_id
                or binding.owner_id != self.domain.binding.owner_id
            ):
                reject(
                    "same Session and owner fact",
                    repr(binding),
                    "Bind the fact to this owner.",
                    "core.signature.evidence",
                )
        for obligation in self.obligations:
            binding = obligation.fact.binding
            if (
                binding.session_id != self.domain.binding.session_id
                or binding.owner_id != self.domain.binding.owner_id
            ):
                reject(
                    "same Session and owner fact",
                    repr(binding),
                    "Bind the fact to this owner.",
                    "core.signature.evidence",
                )


def available_facts(signature: Signature) -> frozenset[Fact]:
    """Close evidence dependencies without promoting checks or conditional Post."""
    pending = {item.fact for item in signature.obligations}
    resolved: set[Fact] = set()
    remaining = list(signature.evidence)
    while remaining:
        ready = [
            item
            for item in remaining
            if item.fact not in pending and set(item.dependencies).issubset(resolved)
        ]
        if not ready:
            break
        resolved.update(item.fact for item in ready)
        remaining = [item for item in remaining if item not in ready]
    return frozenset(resolved)


def require_part(signature: Signature, role: PartRole) -> Part:
    for part in signature.parts:
        if part_role(part) == role:
            return part
    reject(
        f"retained {role} part",
        "missing part",
        "Rebuild from an input retaining this part.",
        "core.parts",
    )
    raise AssertionError("unreachable")
