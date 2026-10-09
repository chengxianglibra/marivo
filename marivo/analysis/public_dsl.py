"""Existing public Analysis receivers over one typed graph Runtime and Store 8."""

from __future__ import annotations

import builtins
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from inspect import signature
from typing import TYPE_CHECKING, Literal, NoReturn, TypeAlias, overload

import pandas as pd
import pyarrow as pa

from marivo._data_render import _validate_display
from marivo._temporal import BeforeEndBoundary, Grain, TimeScope
from marivo.analysis._cohort import (
    AllInstances,
    AnyInstance,
    AtLeast,
    CohortRule,
)
from marivo.analysis._comparison import CohortContrast as CohortContrast
from marivo.analysis._comparison import ExactKeys as ExactKeys
from marivo.analysis._comparison import PeriodChange as PeriodChange
from marivo.analysis._comparison import TimeChange as TimeChange
from marivo.analysis._comparison import UnionKeys as UnionKeys
from marivo.analysis._subject import SubjectBinding, subject_binding
from marivo.analysis._time_grid import GridEndpoint as GridEndpoint
from marivo.analysis._time_grid import TimeGrid as TimeGrid
from marivo.analysis._time_grid import time_grid as time_grid
from marivo.analysis.anchors import AnyAnchor, CalendarWindow, ElapsedWindow, EveryAnchor
from marivo.analysis.core.graph import FixedLeaf, MethodNode, method_node, retained_nodes, topology
from marivo.analysis.core.history_types import HistoryField
from marivo.analysis.core.model import (
    AnchorDomainPart,
    AnchorObservationPart,
    AssociationStatePart,
    AttributionPart,
    Coordinate,
    CoordinateStatePart,
    CoveragePart,
    DerivedQuantity,
    DisplayPart,
    FitInputsPart,
    ForecastStatePart,
    FunnelAllocationPart,
    FunnelComparisonPart,
    FunnelPart,
    FutureCellsPart,
    HistoryPart,
    HistoryViewPart,
    InstanceRetentionPart,
    JourneyPart,
    ObservedQuantity,
    OriginalStatePart,
    PairInputsPart,
    RolledQuantity,
    RowStatisticQuantity,
    SubjectPart,
    SubjectRetentionPart,
    TrainingInputsPart,
    coordinate_binding_id,
    part_role,
)
from marivo.analysis.core.predicates import DurationLiteral, TemporalLiteral, ValuePredicate
from marivo.analysis.core.rules import (
    AnchorBind,
    AnchorObserve,
    AnchorRetention,
    AssociationRead,
    AssociationScore,
    BindProject,
    CellDerive,
    CompleteGroups,
    DeviationRead,
    DisplayRank,
    DisplayTable,
    ForecastRead,
    FunnelAttribute,
    FunnelCompare,
    FunnelField,
    FunnelRead,
    FunnelReduce,
    HistoryRead,
    HistoryReplay,
    HistoryView,
    JourneyCompleted,
    JourneyDuration,
    JourneyMatch,
    JourneyRead,
    MapCorrespond,
    ObserveCount,
    ObserveMetric,
    ObserveWeightedMean,
    OccurrenceCombine,
    PartsTransport,
    PreparedObservation,
    ReferenceDerive,
    RetentionBySubject,
    RowState,
    TimeProduct,
)
from marivo.analysis.core.time_grid import BoundTimeGrid, GridPoint, bind_grid
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.domains.completeness import (
    BoundedCompletenessDeclarationV1,
    SourceOriginCompletenessDeclarationV1,
)
from marivo.analysis.errors import AnalysisError
from marivo.analysis.event import PatternStep
from marivo.analysis.evidence._dataset_types import ArtifactDigest, Finding, FindingPage
from marivo.analysis.forecast_models import ForecastHorizon, ForecastModel, naive
from marivo.analysis.funnel import FunnelLossRate
from marivo.analysis.lifecycle import InState
from marivo.analysis.materialization.graph_dataset import (
    GraphDataset,
    duration_facts,
    interpretation_facts,
)
from marivo.analysis.materialization.graph_fields import (
    BooleanField,
    BoundPredicate,
    CategoryField,
    CategoryPredicate,
    CompositePredicate,
    NumericField,
    NumericPredicate,
    ScalarPredicate,
    StatePredicate,
    TemporalField,
    not_,
)
from marivo.analysis.materialization.graph_relation import FrozenBinding, LiveBinding, Relation
from marivo.analysis.methods.physical import DecimalType, DurationType, ScalarType
from marivo.analysis.observation.route_inputs import (
    RootRoutesValue,
    RootRouteValue,
    root_route,
    root_routes,
)
from marivo.analysis.refs import ArtifactRef
from marivo.analysis.subject import DroppedBefore
from marivo.introspection.live.reflect import required_arguments
from marivo.refs import (
    DimensionKind,
    EntityKind,
    MeasureKind,
    MetricKind,
    Ref,
    RelationshipKind,
    SemanticKind,
    TimeDimensionKind,
)
from marivo.render import _DEFAULT_MAX_OUTPUT_BYTES
from marivo.semantic.event import ParticipantRoleHandle
from marivo.semantic.ir import TargetRelationshipContract
from marivo.semantic.runtime_metric import RuntimeMetricExpr
from marivo.semantic.validator import normalize_target_relationship

if TYPE_CHECKING:
    from marivo.analysis.datasets.state import MaterializedDatasetState
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.materialization.graph_exchange import ExchangeResult

RootRoute: TypeAlias = RootRouteValue
RootRoutes: TypeAlias = RootRoutesValue
MetricInputValue: TypeAlias = Ref[MetricKind] | RuntimeMetricExpr
_TOKEN = object()
_DEFAULT_FORECAST_MODEL = naive()
_EXACT_KEYS = ExactKeys()
_TIME_CHANGE = TimeChange()


def _reject(expected: str, received: str, repair: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected=expected, received=received, repair=repair, location="analysis.dsl"
    )


@dataclass(frozen=True, slots=True)
class RowMethod:
    """Closed current-row statistic selected by the caller."""

    kind: Literal["sum", "count", "count_defined", "min", "max", "mean"]

    def __repr__(self) -> str:
        return f"RowMethod(kind={self.kind!r}; use marivo.help('analysis.RowMethod'))"

    def __post_init__(self) -> None:
        if self.kind not in ("sum", "count", "count_defined", "min", "max", "mean"):
            raise _reject(
                "sum, count, count_defined, min, max or mean",
                str(self.kind),
                "Use mv.sum/count/count_defined/min/max/mean().",
            )


@dataclass(frozen=True, slots=True)
class CountMethod(RowMethod):
    """Closed count-all or Defined-count descriptor for scalar relations."""

    kind: Literal["count", "count_defined"]

    def __post_init__(self) -> None:
        if self.kind not in ("count", "count_defined"):
            raise _reject(
                "count or count_defined", str(self.kind), "Use mv.count() or mv.count_defined()."
            )

    def __repr__(self) -> str:
        return f"CountMethod(kind={self.kind!r}; use marivo.help('analysis.CountMethod'))"


def sum() -> RowMethod:
    """Select the current-row sum method.

    Args: None.
    Returns: A closed RowMethod value.
    Example: ``result = relation.summarize(mv.sum())``.
    Constraints: Sum uses the relation's current rows and rejects unsupported Cells.
    """
    return RowMethod("sum")


def count() -> CountMethod:
    """Select the current-row count method.

    Args: None.
    Returns: A closed RowMethod value.
    Example: ``result = relation.summarize(mv.count())``.
    Constraints: Count includes current rows with non-Defined Cells.
    """
    return CountMethod("count")


def mean() -> RowMethod:
    """Select the equal-current-row mean method.

    Args: None.
    Returns: A closed RowMethod value.
    Example: ``result = relation.summarize(mv.mean())``.
    Constraints: Non-Defined participating Cells reject the strict mean.
    """
    return RowMethod("mean")


def count_defined() -> CountMethod:
    """Select the Defined current-row count method.

    Args: None.
    Returns: A closed RowMethod value.
    Example: ``result = relation.summarize(mv.count_defined())``.
    Constraints: Counts only Defined Cells, without numeric conversion.
    """
    return CountMethod("count_defined")


def min() -> RowMethod:
    """Select the current-row minimum method.

    Args: None.
    Returns: A closed RowMethod value.
    Example: ``result = relation.summarize(mv.min())``.
    Constraints: Requires finite Defined values; empty input is Undefined(empty_min).
    """
    return RowMethod("min")


def max() -> RowMethod:
    """Select the current-row maximum method.

    Args: None.
    Returns: A closed RowMethod value.
    Example: ``result = relation.summarize(mv.max())``.
    Constraints: Requires finite Defined values; empty input is Undefined(empty_max).
    """
    return RowMethod("max")


def route(root: Ref[EntityKind], *, through: tuple[Ref[RelationshipKind], ...]) -> RootRoute:
    """Bind a contribution Entity to one governed relationship path.

    Args:
        root: Exact contribution Entity Ref.
        through: Ordered Relationship Ref path; empty selects identity at the root.
    Returns: A closed RootRoute for read or observe.
    Example: ``path = mv.route(order, through=(buyer,))``.
    Constraints: The route is validated against the selected member and Metric.
    """
    return root_route(root, through=through)


def routes(*items: RootRoute) -> RootRoutes:
    """Bind explicit relationship roles by contribution root identity.

    Args: items: One or more RootRoute values for distinct roots.
    Returns: A closed RootRoutes value for read or observe.
    Example: ``pair = mv.routes(line_route, order_route)``.
    Constraints: Read requires one member-rooted route. Observe accepts partial root overrides in any order; remaining roots resolve automatically. Duplicate and extra roots reject.
    """
    return root_routes(*items)


@dataclass(frozen=True, slots=True)
class AnalysisAction:
    """One admitted receiver call and its exact native Help target."""

    call: str
    help_target: str

    def show(self) -> None:
        """Print the exact receiver call and its native Help route.

        Args: None.
        Returns: None; prints one bounded continuation fact.
        Example: ``relation.contract().actions[0].show()``.
        Constraints: This is static guidance and does not execute the call.
        """
        print(f"{self.call[:160]}; marivo.help({self.help_target!r})")

    def __repr__(self) -> str:
        return f"<AnalysisAction call={self.call[:80]}; use .show()>"


@dataclass(frozen=True, slots=True)
class AnalysisContract:
    """Bounded current result kind and mechanically valid next operations."""

    kind: str
    phase: Literal["logical", "materialized"]
    actions: tuple[AnalysisAction, ...]
    domain: str
    quantity: str | None
    required_parts: tuple[str, ...]
    retained_parts: tuple[str, ...]
    _facts: tuple[tuple[str, str], ...] = field(default=(), repr=False)

    def show(self) -> None:
        """Print bounded definition, retained-state and admissible-call facts.

        Args:
            None.
        Returns: None; prints one bounded contract card.
        Example: ``relation.contract().show()``.
        Constraints: Reads bound metadata only; never queries business source rows.
        """
        lines = [
            f"{self.phase} {self.kind}",
            f"domain={self.domain}; quantity={self.quantity or 'none'}",
            f"required_parts={','.join(self.required_parts) or 'none'}; "
            f"retained_parts={','.join(self.retained_parts) or 'none'}",
            *(f"{name}={value}" for name, value in self._facts),
            *(f"call={action.call}; help={action.help_target}" for action in self.actions),
        ]
        text = "\n".join(lines)
        print(text.encode("utf-8")[:8192].decode("utf-8", errors="ignore"))

    def __repr__(self) -> str:
        return f"<AnalysisContract kind={self.kind} phase={self.phase}; use .show()>"


def _kind(node: Relation) -> str:
    from marivo.analysis.core.model import AssociationStatePart, ForecastStatePart, RunCellsPart

    if not any(isinstance(p, DisplayPart) for p in node.root.signature.parts):
        association = next(
            (p for p in node.root.signature.parts if isinstance(p, AssociationStatePart)), None
        )
        if association is not None:
            return "correlate" if association.view == "result" else "association_read"
        forecast = next(
            (p for p in node.root.signature.parts if isinstance(p, ForecastStatePart)), None
        )
        if forecast is not None:
            return "forecast" if forecast.view == "result" else "forecast_read"

    run = next((p for p in node.root.signature.parts if isinstance(p, RunCellsPart)), None)
    if run is not None and not any(isinstance(p, DisplayPart) for p in node.root.signature.parts):
        return "time_runs" if run.view == "result" else "run_read"
    fit = next(
        (part for part in node.root.signature.parts if isinstance(part, FitInputsPart)), None
    )
    if fit is not None and fit.view == "result":
        return "deviation"
    if isinstance(node.captured_definition.parameters, DeviationRead):
        return "deviation_read"
    if isinstance(node.captured_definition.parameters, AnchorRetention):
        return "retention"
    if isinstance(node.captured_definition.parameters, RetentionBySubject):
        return "subject_retention"
    if isinstance(node.captured_definition.parameters, AnchorBind):
        return "anchor"
    if isinstance(node.captured_definition.parameters, AnchorObserve):
        return "observe"
    definition = node.captured_definition
    if isinstance(definition.parameters, FunnelReduce):
        return "funnel"
    if isinstance(definition.parameters, FunnelCompare):
        return "funnel_comparison"
    if isinstance(definition.parameters, FunnelRead):
        return "funnel_read"
    if isinstance(definition.parameters, FunnelAttribute):
        return "attribution"
    if isinstance(definition.parameters, HistoryRead):
        return "history_read"
    if isinstance(definition.parameters, HistoryView):
        return "history_" + definition.parameters.request.kind
    if isinstance(definition.parameters, PartsTransport) and definition.signature.quantity is None:
        part = next((p for p in definition.signature.parts if isinstance(p, HistoryViewPart)), None)
        if part is not None:
            return "history_" + part.request.kind
    if isinstance(definition.parameters, HistoryReplay):
        return "history"
    if isinstance(definition.parameters, JourneyMatch):
        return "journey"
    if isinstance(definition.parameters, JourneyDuration):
        return "event_duration"
    if isinstance(definition.parameters, JourneyCompleted):
        return "completed_journeys"
    if isinstance(definition.parameters, JourneyRead):
        return "journey_read"
    params, quantity = definition.parameters, definition.signature.quantity
    attribution = next(
        (
            p
            for p in definition.signature.parts
            if isinstance(p, AttributionPart) and p.role == "allocation"
        ),
        None,
    )
    if any(
        isinstance(p, DisplayPart) and p.role == "ranking_domain"
        for p in definition.signature.parts
    ):
        if isinstance(params, PartsTransport) and params.display_view is not None:
            if params.display_view == "values":
                if isinstance(quantity, RowStatisticQuantity):
                    return "summarize"
                if isinstance(quantity, (ObservedQuantity, RolledQuantity)):
                    return (
                        "ratio_rollup"
                        if isinstance(quantity, RolledQuantity)
                        and quantity.method_version == "ratio@v1"
                        else "rollup"
                        if isinstance(quantity, RolledQuantity)
                        else "ratio_observe"
                        if quantity.method_version == "ratio@v1"
                        else "observe"
                    )
            return "relation_ratio"
        return "ranking"
    if any(isinstance(p, FunnelAllocationPart) for p in definition.signature.parts):
        return (
            "attribution_view"
            if isinstance(params, PartsTransport) and params.attribution_view is not None
            else "attribution"
        )
    if attribution is not None:
        return (
            "attribution_view"
            if quantity is not None and quantity.definition_id != attribution.domain.definition_id
            else "attribution"
        )
    if isinstance(params, DisplayTable):
        return "table"
    if isinstance(params, TimeProduct):
        return "members"
    if isinstance(params, CompleteGroups):
        quantity = node.root.signature.quantity
        return (
            "group"
            if quantity is None
            else "summarize"
            if isinstance(quantity, RowStatisticQuantity)
            else "ratio_observe"
            if isinstance(quantity, ObservedQuantity) and quantity.method_version == "ratio@v1"
            else "observe"
            if isinstance(quantity, ObservedQuantity)
            else "ratio_rollup"
            if quantity is not None and quantity.method_version == "ratio@v1"
            else "rollup"
        )
    if isinstance(params, ReferenceDerive):
        return "relation_ratio"
    if isinstance(params, RowState):
        return "summarize"
    if isinstance(params, AssociationScore):
        return "correlate"
    if isinstance(params, CellDerive):
        return "relation_ratio" if params.method == "ratio" else "compare"
    if isinstance(params, BindProject):
        return "read"
    if isinstance(params, MapCorrespond):
        return "group" if params.mode in ("group", "group_keys") else "members"
    if isinstance(params, PartsTransport):
        if params.mode == "business_coverage":
            return "observe"
        if not params.keep_quantity:
            return "members"
        if isinstance(quantity, DerivedQuantity):
            return (
                "correlate_where"
                if quantity.method_version == "association.spearman@v1"
                else "where"
            )
        return "where"
    if isinstance(quantity, (ObservedQuantity, RolledQuantity)):
        ratio = quantity.method_version == "ratio@v1"
        rolled = isinstance(quantity, RolledQuantity)
        return (
            "ratio_rollup"
            if ratio and rolled
            else "ratio_observe"
            if ratio
            else "rollup"
            if rolled
            else "observe"
        )
    return "members"


class _Value:
    __slots__ = ("_dataset", "_node", "_runtime")
    _runtime: DatasetRuntime
    _node: Relation
    _dataset: GraphDataset | None

    def __init__(
        self,
        token: object,
        node: Relation,
        runtime: DatasetRuntime,
        *,
        inputs: tuple[_Value, ...] = (),
        dataset: GraphDataset | None = None,
    ) -> None:
        if token is not _TOKEN:
            raise TypeError(
                "Construct Analysis values through a Session or another Analysis value."
            )
        if any(
            item._runtime.session_ref != runtime.session_ref
            or item._runtime.store.store_id != runtime.store.store_id
            for item in inputs
        ):
            raise _reject(
                "one Session", "foreign Session inputs", "Use this Session's exact inputs."
            )
        self._runtime, self._dataset = runtime, dataset
        self._node = (
            node
            if dataset is None
            or dataset.projection is not None
            or (
                isinstance(node.root, FixedLeaf)
                and node.root.artifact.ref == dataset.artifact.artifact_ref
            )
            else Relation.restore(dataset)
        )

    def _summarize_rows(self, method: RowMethod) -> LogicalStatisticRelation:
        if not isinstance(method, RowMethod):
            raise _reject(
                "mv.sum/count/count_defined/min/max/mean()",
                type(method).__name__,
                "Select a closed row method.",
            )
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )

    def _materialize_before_continuing(self) -> bool:
        return (
            self._dataset is None
            and isinstance(self._node.binding, LiveBinding)
            and (
                (
                    isinstance(self._node.root.value_type, DurationType)
                    and isinstance(self._node.root.signature.quantity, RowStatisticQuantity)
                )
                or any(
                    isinstance(node, MethodNode)
                    and isinstance(node.parameters, PreparedObservation)
                    and node.inputs[0].node.identity != node.inputs[1].node.identity
                    for node in topology(self._node.root)
                )
            )
        )

    def contract(self) -> AnalysisContract:
        """Return the verified relation state and its mechanically valid next calls.

        Args: None.
        Returns: An AnalysisContract for this exact relation.
        Example: ``relation.contract().show()``.
        Constraints: Fixed results verify their Store 8 files without opening sources.
        """
        if self._dataset is not None:
            self._dataset.verified()
        signature = self._node.root.signature
        comparison_unavailable = self._node.comparison_error
        kind, fixed = _kind(self._node), self._dataset is not None
        roles = tuple(part_role(part) for part in signature.parts)
        names: tuple[str, ...]
        if kind == "forecast":
            names = ("prediction", "lower", "upper", "where")
        elif kind == "forecast_read":
            names = ("where", "rank", "summarize", "table")
        elif kind == "association_read":
            names = (
                ("where", "table")
                if self._node.root.value_type == ScalarType("boolean")
                else ("where", "rank", "summarize", "table")
            )
        elif kind == "correlate" and "pair_inputs" in roles:
            names = ("coefficient", "selected", "where")
        elif kind == "time_runs":
            names = ("start", "end", "count", "duration", "where")
        elif kind == "run_read":
            names = (
                ("where", "table")
                if self._node.root.value_type.name != "int64"
                else ("where", "rank", "table")
            )
            if self._node.root.value_type == ScalarType("int64") and any(
                isinstance(p, SubjectPart) for p in signature.parts
            ):
                names += ("members",)
        elif kind == "deviation":
            names = ("observed", "reference", "deviation", "score", "where")
        elif kind == "deviation_read":
            names = (
                "where",
                "summarize",
                *(("rollup",) if "original_state" in roles and "coverage" in roles else ()),
            )
        elif kind in ("retention", "subject_retention"):
            names = (
                "status",
                "known_true",
                "known_false",
                "unknown",
                *(("by_subject",) if kind == "retention" else ()),
            )
        elif kind == "history":
            names = ("read", "distribution", "transitions", "violations", "intervals", "dwell")
        elif kind in (
            "history_distribution",
            "history_transitions",
            "history_dwell",
            "history_violations",
            "history_intervals",
        ):
            from marivo.analysis.core.history_rules import FIELDS

            names = (
                *FIELDS[kind.removeprefix("history_")],
                "where",
                *(
                    ("subjects", "members")
                    if kind in ("history_violations", "history_intervals")
                    else ()
                ),
            )
        elif kind in ("history_read", "history_in_state"):
            names = ("where", "summarize", *(("members",) if "subject" in roles else ()))
        elif kind == "journey":
            names = (
                "time_to_event",
                "subjects",
                *(
                    ("read", "funnel")
                    if next(p for p in signature.parts if isinstance(p, JourneyPart)).policy
                    == "first_per_subject"
                    else ()
                ),
            )
        elif kind in ("event_duration", "completed_journeys"):
            names = (
                "status",
                "started_at",
                "completed_at",
                "duration",
                "observed_duration",
                "followup_until",
                "completed",
                "subjects",
            )
        elif kind == "journey_read":
            names = ("where", "members")
            params = self._node.definition.parameters
            if isinstance(params, JourneyRead) and params.field in (
                "duration",
                "observed_duration",
            ):
                names = (*names, "summarize")
        elif kind == "funnel":
            part = next(p for p in signature.parts if isinstance(p, FunnelPart))
            names = ("read", *(("compare",) if part.complete else ()))
        elif kind == "funnel_comparison":
            names = ("read", "attribute")
        elif kind == "funnel_read":
            names = ("where", "summarize", "rank")
        elif kind == "attribution":
            names = ("contribution", "current", "baseline", "where")
        elif kind == "attribution_view":
            names = ("where", "summarize")
        elif kind == "ranking":
            names = ("values", "ranks", "where", "limit")
        elif isinstance(self, MaterializedCoefficientRelation):
            names = ("where", "summarize")
        elif kind == "members":
            names = (
                (("cohort",) if signature.domain.time_grid is None else ())
                if fixed
                else (("cohort", "execute") if signature.domain.time_grid is None else ("execute",))
                if self._has_fixed()
                else ("read", "group_by", "observe", "execute")
                if signature.domain.time_grid is not None
                else ("cohort", "read", "group_by", "observe", "execute")
            )
        elif kind == "read":
            names = (
                ("where", "members", "group_by", "summarize")
                if fixed
                else ("where", "members", "group_by", "summarize", "execute")
            )
        elif isinstance(signature.quantity, RowStatisticQuantity):
            names = (
                (("group_by", "rollup") if signature.domain.instance_key else ("rollup",))
                if "row_state" in roles
                else ()
            )
        elif kind == "group":
            names = ()
        elif kind == "correlate":
            names = ("coefficient",) if fixed else ("execute",)
        elif kind == "correlate_where":
            names = ("summarize",)
        elif kind in ("compare", "relation_ratio"):
            names = ("where", "summarize")
        elif kind == "where":
            names = (
                *(("members",) if any(isinstance(p, SubjectPart) for p in signature.parts) else ()),
                *(("summarize",) if signature.quantity is not None else ()),
            )
        elif kind in ("observe", "ratio_observe", "rollup", "ratio_rollup"):
            names = (
                *(
                    ("group_by",)
                    if "coordinate_state" in roles or signature.domain.instance_key
                    else ()
                ),
                *(("rollup",) if "original_state" in roles and "coverage" in roles else ()),
                "summarize",
                *(
                    (("compare",) if "coordinate_state" in roles else ("compare", "correlate"))
                    if kind == "observe" and signature.domain.kind == "entity"
                    else ()
                ),
            )
        else:
            names = ()
        if kind == "anchor":
            names = ("subjects",) if fixed else ("observe", "subjects", "execute")
        if kind == "where":
            names = ("where", *names)
        state = next((p for p in signature.parts if isinstance(p, OriginalStatePart)), None)
        if state is not None and state.temporal_policy in ("repeated", "overlapping"):
            names = tuple(name for name in names if name != "rollup")
        if any(
            isinstance(p, CoveragePart) and p.business_windows is not None for p in signature.parts
        ):
            names = tuple(name for name in names if name not in ("rollup", "group_by"))
        if isinstance(self, _CountRelation):
            names = tuple(dict.fromkeys((*names, "group_by", "summarize")))
        if (
            signature.quantity is not None
            and signature.quantity.method_version == "history.read@v1"
        ):
            names = tuple(name for name in names if name != "group_by")
        if not fixed and kind not in ("members", "read", "group", "correlate"):
            names = (*names, "execute")
        if isinstance(self, (LogicalDifferenceRelation, MaterializedDifferenceRelation)):
            from marivo.analysis.materialization.graph_attribution import method

            try:
                method(signature)
            except DatasetConstructionError:
                pass
            else:
                names = tuple(dict.fromkeys((*names, "attribute")))
        if isinstance(self, _NumericComparison):
            try:
                self._node._check_runs_input()
            except AnalysisError:
                pass
            else:
                names = (*names, "runs", "forecast")
            names = tuple(
                dict.fromkeys(
                    (
                        *names,
                        "rank",
                        *(
                            ("deviation", "correlate")
                            if isinstance(self._node.root.value_type, DecimalType)
                            or self._node.root.value_type
                            in (ScalarType("int64"), ScalarType("float64"))
                            else ()
                        ),
                    )
                )
            )
            if self._node.comparison_error is None:
                names = tuple(dict.fromkeys((*names, "compare", "ratio")))
            elif any(isinstance(p, AttributionPart) for p in signature.parts):
                names = tuple(dict.fromkeys((*names, "ratio")))
            else:
                names = tuple(name for name in names if name not in ("compare", "ratio"))
            from marivo.analysis.materialization.graph_reference import original, statistical_unit

            try:
                parameters = original(self._node.definition).parameters
            except DatasetConstructionError:
                pass
            else:
                if isinstance(parameters, (ObserveCount, OccurrenceCombine)) or (
                    isinstance(parameters, ObserveMetric)
                    and parameters.method == "sum"
                    and parameters.fold is None
                ):
                    names = tuple(dict.fromkeys((*names, "share_of")))
            try:
                statistical_unit(self._node.definition)
            except DatasetConstructionError:
                pass
            else:
                if signature.domain.kind == "group" and (
                    isinstance(self._node.root.value_type, DecimalType)
                    or self._node.root.value_type in (ScalarType("int64"), ScalarType("float64"))
                ):
                    names = tuple(dict.fromkeys((*names, "standardize")))
        if isinstance(self, _CohortDomain) and signature.domain.kind == "entity":
            names = tuple(dict.fromkeys((*names, "penetration_in")))
        if (
            "subject" in roles
            and callable(getattr(type(self), "members", None))
            and "members" not in names
        ):
            names = (*names, "members")
        retention = next(
            (
                p
                for p in signature.parts
                if isinstance(p, (InstanceRetentionPart, SubjectRetentionPart))
            ),
            None,
        )
        if retention is not None:
            if isinstance(self, _Retention):
                names = (
                    "status",
                    "known_true",
                    "known_false",
                    "unknown",
                    *(("by_subject",) if isinstance(retention, InstanceRetentionPart) else ()),
                    *(("execute",) if not fixed else ()),
                )
            else:
                names = (
                    "where",
                    *(("members",) if retention.selection == "true" else ()),
                    *(("execute",) if not fixed else ()),
                )
        if "anchor" in roles and signature.quantity is not None:
            names = (
                "where",
                "summarize",
                *(("members",) if kind == "where" else ()),
                *(("execute",) if not fixed else ()),
            )
        if isinstance(self._node.root.value_type, DurationType) and (
            any(isinstance(part, (JourneyPart, HistoryViewPart)) for part in signature.parts)
            or isinstance(signature.quantity, RowStatisticQuantity)
        ):
            names = tuple(name for name in names if name not in ("rank", "compare", "ratio"))
            if any(
                isinstance(part, HistoryViewPart) and part.request.kind == "dwell"
                for part in signature.parts
            ):
                names = tuple(name for name in names if name != "summarize")
        if self._materialize_before_continuing():
            names = ("execute",)
        if "condition_cells" in roles and isinstance(self._node.root.value_type, DurationType):
            names = tuple(
                name
                for name in names
                if name not in ("rank", "members", "compare", "ratio", "deviation")
            )
        if (
            signature.quantity is not None
            and signature.quantity.value_policy == "prediction_interval_bound"
        ):
            names = tuple(
                n
                for n in names
                if n
                not in (
                    "summarize",
                    "rollup",
                    "attribute",
                    "forecast",
                    "correlate",
                    "deviation",
                    "rank",
                )
            )
        if isinstance(self, _NumericComparison) and "compare" in names:
            from marivo.analysis.materialization.graph_composition import (
                comparison_bindings,
                comparison_template,
            )

            try:
                comparison_template(self._node.definition)
                comparison_bindings(self._node.definition)
            except DatasetConstructionError as error:
                names = tuple(name for name in names if name != "compare")
                comparison_unavailable = error.received
        if isinstance(self, _NumericComparison) and not signature.domain.instance_key:
            names = tuple(name for name in names if name != "correlate")
        required = tuple(role for role in roles if role != "subject")
        actions = self._action_contract(names)
        if kind == "time_runs":
            actions += (
                AnalysisAction(
                    "mv.table(start=relation.start, end=relation.end, count=relation.count, duration=relation.duration)",
                    "analysis.dsl.table",
                ),
            )
        elif kind == "run_read":
            actions += (AnalysisAction("mv.table(value=relation)", "analysis.dsl.table"),)
        if "anchor" in roles and signature.quantity is not None:
            allowed = (
                ("count", "count_defined", "mean")
                if isinstance(self._node.root.value_type, DurationType)
                else ("count", "count_defined")
                if isinstance(self._node.root.value_type, DecimalType)
                else (
                    "count",
                    "count_defined",
                    "sum",
                    "min",
                    "max",
                    *(("mean",) if self._node.root.value_type == ScalarType("float64") else ()),
                )
            )
            actions = tuple(
                replacement
                for action in actions
                for replacement in (
                    tuple(
                        AnalysisAction(
                            f"relation.where(relation.value.is_defined()).summarize(mv.{method}())",
                            action.help_target,
                        )
                        for method in allowed
                    )
                    if action.call.startswith("relation.summarize(")
                    else (action,)
                )
            )
        facts = self._contract_facts()
        if comparison_unavailable is not None and self._node.comparison_error is None:
            facts += (("comparison_unavailable", comparison_unavailable),)
        return AnalysisContract(
            kind,
            "materialized" if fixed else "logical",
            actions,
            signature.domain.kind,
            None if signature.quantity is None else signature.quantity.kind,
            required,
            roles if fixed else (),
            facts,
        )

    def _contract_facts(
        self, *, checked: ExchangeResult | None = None, display: bool = False
    ) -> tuple[tuple[str, str], ...]:
        signature = self._node.root.signature
        quantity = signature.quantity
        facts = list(interpretation_facts(signature, self._node.root.value_type))
        assumptions = tuple(
            dict.fromkeys(item.fact for item in signature.evidence if item.basis == "assumption")
        )
        if assumptions:
            facts.append(
                (
                    "premise_assumptions",
                    ", ".join(
                        f"{kind}:{len([fact for fact in assumptions if fact.kind == kind])}"
                        for kind in sorted({fact.kind for fact in assumptions})
                    )
                    + "; not checked",
                )
            )
        from marivo.analysis.core.model import RunCellsPart

        run = next((p for p in signature.parts if isinstance(p, RunCellsPart)), None)
        if run is not None:
            facts.extend(
                (
                    ("run_scope", run.run_id),
                    ("run_transform", "select_output_retain_scope@v1"),
                    ("scope_boundary", "observation ended; business condition may continue"),
                )
            )
            if self._dataset is not None:
                from marivo.analysis.materialization.runs_execution import _decode as decode_runs

                _, run_state = decode_runs(
                    (checked if checked is not None else self._dataset.verified()).parts
                )
                for label in ("true", "false", "unavailable"):
                    facts.append(("original_" + label, str(run_state.classifications.count(label))))
        association_state = next(
            (p for p in signature.parts if isinstance(p, AssociationStatePart)), None
        )
        forecast_state = next(
            (p for p in signature.parts if isinstance(p, ForecastStatePart)), None
        )
        if association_state is not None:
            declaration = next(p for p in signature.parts if isinstance(p, PairInputsPart))
            facts.extend(
                (
                    ("association_method", declaration.method),
                    ("observation_unit", declaration.input_domain.kind),
                    ("quantity_count", str(len(declaration.quantities))),
                    ("original_lags", repr(declaration.lags[:16])),
                    ("selection", "max_abs_coefficient_min_abs_lag_min_signed_lag@v1"),
                    ("scope", "original search retained; output selection never reselects"),
                )
            )
            if self._dataset is not None:
                from marivo.analysis.materialization.statistical_execution import decode_pairs

                _, original_association = decode_pairs(
                    (checked if checked is not None else self._dataset.verified()).parts
                )
                facts.extend(
                    (
                        ("original_candidates", str(len(original_association.candidates))),
                        (
                            "valid_candidates",
                            str(
                                builtins.sum(
                                    c.score.status == "valid"
                                    for c in original_association.candidates
                                )
                            ),
                        ),
                        (
                            "selected_candidates",
                            str(builtins.sum(c.selected for c in original_association.candidates)),
                        ),
                    )
                )
        if forecast_state is not None:
            declaration_f = next(p for p in signature.parts if isinstance(p, TrainingInputsPart))
            future = next(p for p in signature.parts if isinstance(p, FutureCellsPart))
            facts.extend(
                (
                    ("forecast_model", declaration_f.model),
                    ("horizon", str(len(future.grid.cells))),
                    ("interval_level", repr(declaration_f.level)),
                    ("assumptions", "zero_mean_uncorrelated_homoskedastic_normal_innovations@v1"),
                    ("interval", "normal_residual@v1; nominal future observations"),
                    ("scope", "original training and future grids retained"),
                )
            )
            if self._dataset is not None:
                from marivo.analysis.materialization.statistical_execution import decode_forecast

                _, original_f = decode_forecast(
                    (checked if checked is not None else self._dataset.verified()).parts
                )
                facts.extend(
                    (
                        ("series_count", str(len(original_f.series))),
                        (
                            "history_lengths",
                            repr(tuple(s.training.n for s in original_f.series[:8])),
                        ),
                        (
                            "degrees_of_freedom",
                            repr(tuple(s.training.df for s in original_f.series[:8])),
                        ),
                        (
                            "exact_zero_series",
                            str(builtins.sum(s.training.exact_zero for s in original_f.series)),
                        ),
                    )
                )
        fit = next((p for p in signature.parts if isinstance(p, FitInputsPart)), None)
        if fit is not None:
            facts.extend(
                (
                    ("fit_method", f"deviation.{fit.method}@v1"),
                    ("fit_scope", fit.fit_id),
                    ("fit_transform", "select_output_retain_scope@v1"),
                    ("numeric_policy", "r8_numeric_v1"),
                    ("partition_count", "pending execution"),
                )
            )
            if self._dataset is not None:
                from marivo.analysis.materialization.deviation_execution import _decode

                _, fit_state = _decode(
                    (checked if checked is not None else self._dataset.verified()).parts
                )
                facts[-1] = ("partition_count", str(len(fit_state.partitions)))
                for label in ("defined", "null", "undefined", "unknown"):
                    facts.append(
                        (
                            "original_" + label,
                            str(builtins.sum(getattr(p, label) for p in fit_state.partitions)),
                        )
                    )
                facts.extend(
                    (
                        (
                            "original_count",
                            str(builtins.sum(len(p.indices) for p in fit_state.partitions)),
                        ),
                        (
                            "scale_branches",
                            ", ".join(sorted({p.fit.branch for p in fit_state.partitions})),
                        ),
                        ("empty_fit", str(not any(p.fit.n for p in fit_state.partitions))),
                        (
                            "unavailable_defined_scores",
                            str(
                                builtins.sum(
                                    p.defined
                                    for p in fit_state.partitions
                                    if p.fit.n < 2
                                    or p.fit.raw_scale is None
                                    or p.fit.raw_scale.value() == 0
                                )
                            ),
                        ),
                    )
                )
                for index, partition in enumerate(fit_state.partitions[:3]):
                    fitted = partition.fit

                    def bounded_fact(value: str) -> str:
                        return (
                            value
                            if display or len(value) <= 96
                            else value[:96] + f"... (excerpt; {len(value)} characters retained)"
                        )

                    center = (
                        "unavailable"
                        if fitted.center is None
                        else bounded_fact(f"{fitted.center.numerator}/{fitted.center.denominator}")
                    )
                    scale = (
                        "unavailable"
                        if fitted.raw_scale is None
                        else bounded_fact(
                            f"{fitted.raw_scale.numerator}/{fitted.raw_scale.denominator}"
                        )
                    )
                    facts.append(
                        (
                            f"fit_partition_{index}",
                            f"original={len(partition.indices)}; valid={fitted.n}; "
                            f"center={center}; raw_scale_or_variance={scale}; branch={fitted.branch}",
                        )
                    )
                if len(fit_state.partitions) > 3:
                    facts.append(
                        (
                            "fit_partitions_truncated",
                            f"{len(fit_state.partitions) - 3} additional partitions retained in original fit authority",
                        )
                    )
        if self._materialize_before_continuing():
            facts.append(
                (
                    "continuation_boundary",
                    "Execute this local result first; use the materialized result for further operations.",
                )
            )
        history = next((p for p in signature.parts if isinstance(p, HistoryPart)), None)
        if history is not None:
            facts.extend(
                (
                    ("statistical_unit", "Subject"),
                    ("seed", "from_inception"),
                    ("window", history.window_start + "/" + history.window_end),
                )
            )
            if self._dataset is not None:
                facts.append(
                    (
                        "captured_precision",
                        (
                            (
                                checked if checked is not None else self._dataset.verified()
                            ).primary.schema.metadata
                            or {}
                        )
                        .get(b"r7.precision", b"unavailable")
                        .decode()[:2048],
                    )
                )
        view = next((p for p in signature.parts if isinstance(p, HistoryViewPart)), None)
        if view is not None:
            facts.extend(
                (
                    ("history_view", view.request.kind),
                    ("window", view.history.window_start + "/" + view.history.window_end),
                    ("complete_domain", str(view.complete)),
                    (
                        "time_precision",
                        "captured microseconds; HALF_EVEN once after exact Fraction interpolation",
                    ),
                )
            )
            if view.request.kind == "dwell":
                facts.append(
                    (
                        "estimand",
                        "completed_window_fragment_duration@v1; left-clipped completed included; censored excluded",
                    )
                )
        journey = next((p for p in signature.parts if isinstance(p, JourneyPart)), None)
        anchor = next(
            (
                p
                for p in signature.parts
                if isinstance(p, (AnchorDomainPart, AnchorObservationPart))
            ),
            None,
        )
        if anchor is not None:
            domain = anchor.domain if isinstance(anchor, AnchorObservationPart) else anchor
            facts.extend(
                (
                    ("statistical_unit", "Anchor"),
                    ("start_selection", domain.during_start + "/" + domain.during_end),
                    ("origin", "Journey" if domain.journey else "Event"),
                    ("time_precision", "captured microseconds; deadlines must be exact"),
                )
            )
            if isinstance(anchor, AnchorObservationPart):
                facts.extend(
                    (
                        ("within", repr(anchor.window)),
                        ("contribution_policy", "per Anchor use bindings; overlapping windows"),
                    )
                )
        if journey is not None:
            facts.extend(
                (
                    ("statistical_unit", "Journey"),
                    ("matching", journey.policy),
                    ("complete_opportunities", str(journey.complete)),
                    (
                        "time_precision",
                        "captured microseconds; native conversion may lose finer source precision",
                    ),
                )
            )
            if self._dataset is not None:
                metadata = (
                    checked if checked is not None else self._dataset.verified()
                ).primary.schema.metadata or {}
                facts.append(
                    (
                        "captured_precision",
                        metadata.get(b"r7.precision", b"unavailable").decode()[:2048],
                    )
                )

        funnel = next(
            (
                p
                for p in signature.parts
                if isinstance(p, (FunnelPart, FunnelComparisonPart, FunnelAllocationPart))
            ),
            None,
        )
        if funnel is not None:
            original = (
                funnel
                if isinstance(funnel, FunnelPart)
                else funnel.current
                if isinstance(funnel, FunnelComparisonPart)
                else funnel.comparison.current
            )
            facts.extend(
                (
                    ("assignment", "retained canonical first_per_subject"),
                    (
                        "entry_axes",
                        ", ".join(a.dimension.ref.path for a in original.axes) or "none",
                    ),
                    ("component_scope", original.capture_scope),
                    ("complete_partition", str(funnel.complete)),
                    (
                        "source_route",
                        "ibis_python preparation before local consumption; fixed artifact_python",
                    ),
                )
            )
            if isinstance(funnel, FunnelAllocationPart):
                facts.extend(
                    (
                        ("allocation_method", "funnel_ratio_mix@v1"),
                        (
                            "reconciliation_scope",
                            funnel.original.current.capture_scope
                            + ":"
                            + funnel.original.baseline.capture_scope
                            + ":"
                            + str(funnel.target_step),
                        ),
                        (
                            "side_terms",
                            "allocated original components; separate loss and denominator_mix",
                        ),
                    )
                )

        retention = next(
            (
                p
                for p in signature.parts
                if isinstance(p, (InstanceRetentionPart, SubjectRetentionPart))
            ),
            None,
        )
        if retention is not None:
            facts.extend(
                (
                    ("population", "fixed original Omega; status views preserve its denominator"),
                    ("selection", retention.selection),
                )
            )
            if isinstance(retention, SubjectRetentionPart):
                facts.append(("subject_rule", retention.rule.kind))
            if self._dataset is not None:
                from marivo.analysis.materialization.retention_execution import read, summary

                exchange = checked if checked is not None else self._dataset.verified()
                facts.extend(
                    summary(
                        read(next(p for p in exchange.parts if p.role == "retention")), retention
                    )
                )
        if self._node.comparison_error is not None:
            facts.append(("comparison_unavailable", self._node.comparison_error))
        if isinstance(self, _NumericComparison) and not signature.domain.instance_key:
            facts.append(
                ("correlation_unavailable", "Scalar has no Entity/category/time statistical units")
            )
        params = self._node.definition.parameters
        attribution = next((p for p in signature.parts if isinstance(p, AttributionPart)), None)
        if attribution is not None:
            facts += (
                ("allocation_method", attribution.method),
                ("reconciliation_scope", attribution.domain.definition_id),
                ("complete_partition", str(attribution.complete)),
                ("side_terms", "allocated original components; not grouped ratios"),
            )
        reference = next(
            (
                node.parameters
                for node in retained_nodes(self._node.definition)
                if isinstance(node, MethodNode)
                and isinstance(node.parameters, ReferenceDerive)
                and node.signature.quantity == quantity
            ),
            None,
        )
        if reference is not None:
            facts.extend(
                (
                    ("reference", reference.reference_id),
                    ("reference_policy", "retained original inputs; where preserves reference"),
                )
            )
            if reference.kind == "standardize":
                facts.extend(
                    (
                        ("interpretation", "weighted stratum value; no actual population claim"),
                        (
                            "statistical_entity",
                            reference.statistical_unit.path
                            if reference.statistical_unit
                            else "unproved",
                        ),
                        (
                            "weights",
                            "complete keys including zero weights; represented sum, no normalization",
                        ),
                    )
                )
            elif reference.kind == "share":
                facts.extend(
                    (
                        (
                            "range",
                            "signed unless nonnegative support and positive denominator proved",
                        ),
                        ("partition", "complete only if current keys equal retained support"),
                    )
                )
        if isinstance(params, BindProject):
            facts.extend((("field", params.ref.path), ("field_kind", params.ref.kind.value)))
            facts.append(
                ("field_path", " -> ".join(item.path for item in params.path) or "identity")
            )
        bound_observations = tuple(
            item
            for item in retained_nodes(self._node.definition)
            if isinstance(item, MethodNode)
            and isinstance(item.parameters, (ObserveMetric, ObserveCount, ObserveWeightedMean))
        )
        for index, bound in enumerate(bound_observations[:4]):
            observation = bound.parameters
            assert isinstance(observation, (ObserveMetric, ObserveCount, ObserveWeightedMean))
            facts.append(
                (
                    f"contribution_binding_{index}",
                    observation.contribution.path
                    + ": "
                    + (" -> ".join(item.ref.path for item in observation.path) or "identity"),
                )
            )
            for position, coordinate in enumerate(observation.classification_coordinates[:4]):
                facts.append(
                    (
                        f"coordinate_binding_{index}_{position}",
                        coordinate.field + ": " + coordinate.binding_id[:22],
                    )
                )
            fields = {
                c.field
                for c in (
                    *bound.signature.domain.instance_key,
                    *observation.classification_coordinates,
                )
            }
            classifications = tuple(
                dict.fromkeys(
                    (
                        item.parameters.ref.path,
                        item.inputs[0].node.signature.domain.instance_key[0].entity_ref.path,
                        tuple(hop.path for hop in item.parameters.path),
                        item.parameters.attribute_time,
                    )
                    for edge in bound.inputs
                    for item in topology(edge.node)
                    if isinstance(item, MethodNode)
                    and isinstance(item.parameters, BindProject)
                    and item.parameters.ref.path in fields
                )
            )
            for position, (dimension_field, origin, path, version_time) in enumerate(
                classifications[:4]
            ):
                role = (
                    dimension_field + " from " + origin + ": " + (" -> ".join(path) or "identity")
                )
                if version_time != "untimed":
                    role += "; at=" + version_time
                facts.append(
                    (
                        f"classification_role_{index}_{position}",
                        role if len(role) <= 384 else role[:381] + "...",
                    )
                )
        if self._dataset is not None:
            schema = self._dataset.artifact.descriptor
            from marivo.analysis.materialization.graph_protocol import schema_from

            physical = schema_from(schema.realized_schema)
            if "value" in physical.names:
                facts.append(("value_type", str(physical.field("value").type)))
            if not isinstance(self._node.root.value_type, DurationType):
                for field in physical:
                    if pa.types.is_duration(field.type):
                        facts.extend(
                            (field.name + "." + name, value)
                            for name, value in duration_facts(field.type.unit)
                        )
        elif isinstance(self, _DwellSummary):
            from marivo.analysis.core.history_rules import FIELDS
            from marivo.analysis.core.rules import HistoryRead
            from marivo.analysis.methods.history_view_physical import output_type

            for name in FIELDS["dwell"]:
                field_type = output_type(HistoryRead(name))
                if isinstance(field_type, DurationType):
                    facts.extend(
                        (name + "." + key, value) for key, value in duration_facts(field_type.unit)
                    )
        if _kind(self._node) in ("ratio_observe", "ratio_rollup"):
            facts.append(("weighting", "original numerator and denominator components"))
        if quantity is not None:
            facts.extend(
                (
                    (
                        "output_grain",
                        ", ".join(
                            c.field for c in signature.domain.instance_key if c.role != "anchor"
                        )
                        or "overall",
                    ),
                    (
                        "time_axes",
                        ", ".join(
                            c.field for c in signature.domain.instance_key if c.role == "anchor"
                        )
                        or "none",
                    ),
                )
            )
            facts.extend(
                (
                    ("unit", quantity.unit or "not declared"),
                    (
                        "method",
                        quantity.method_version.split("@")[0]
                        .removeprefix("row.")
                        .removeprefix("cell."),
                    ),
                    (
                        "statistical_unit",
                        "one current row"
                        if isinstance(quantity, RowStatisticQuantity)
                        else "one Entity member"
                        if signature.domain.kind == "entity"
                        else "one current group"
                        if signature.domain.kind == "group"
                        else "one selected domain",
                    ),
                )
            )
            if isinstance(quantity, ObservedQuantity):
                from marivo.analysis.methods.semantics import observation_disclosure

                facts.extend(
                    observation_disclosure(quantity.method_version, self._node.root.value_type)
                )
                facts.append(
                    (
                        "metric",
                        quantity.metric_ref.label
                        if isinstance(quantity.metric_ref, RuntimeMetricExpr)
                        else quantity.metric_ref.path,
                    )
                )
                coverage = next(
                    (
                        p
                        for p in signature.parts
                        if isinstance(p, CoveragePart) and p.business_windows is not None
                    ),
                    None,
                )
                if coverage is not None:
                    facts.extend(
                        (
                            (
                                "business_coverage",
                                f"{len(coverage.business_windows or ())} declared complete intervals; uncovered buckets are Unknown",
                            ),
                            (
                                "partial_state",
                                "retained for integrity; cannot roll up incomplete business observations",
                            ),
                        )
                    )
            if quantity.method_version == "ratio@v1":
                facts.append(("weighting", "original numerator and denominator components"))
            if (
                isinstance(quantity, RowStatisticQuantity)
                and quantity.method_version == "row.mean@v1"
            ):
                facts.append(("weighting", "equal current rows"))
        if self._dataset is not None and not display:
            state = self._dataset.state
            facts.extend(
                (
                    ("rows", str(state.realized_row_count)),
                    ("source", "exact retained Artifact; no source reconnect"),
                )
            )
            cells = tuple(
                field.name for field in state.realized_schema.columns if field.role_id == "cell"
            )
            if cells:
                facts.append(("cell_state_fields", ",".join(cells)))
        elif self._dataset is None:
            facts.append(("source", "logical definition; no business rows read"))
        if display:
            # The callable owner separates reading meaning from continuation/storage facts.
            internal = {
                "run_scope",
                "run_transform",
                "fit_scope",
                "fit_transform",
                "numeric_policy",
                "continuation_boundary",
                "source_route",
                "comparison_unavailable",
                "correlation_unavailable",
                "reference",
                "component_scope",
                "reconciliation_scope",
                "partial_state",
                "value_type",
                "cell_state_fields",
                "source",
                "rows",
                "captured_precision",
            }
            facts = [(name, value) for name, value in facts if name not in internal]
        return tuple(dict.fromkeys(facts)) if display else tuple(facts)

    def __repr__(self) -> str:
        identity = (
            self._dataset.state.artifact_ref.ref[:40]
            if self._dataset is not None
            else self._node.root.fingerprint[:22]
        )
        detail = ".show()" if self._dataset is not None else ".contract().show()"
        return f"<{type(self).__name__} kind={_kind(self._node)} id={identity}; use {detail}>"

    def _has_fixed(self) -> bool:
        return isinstance(self._node.binding, FrozenBinding)

    def _run(self) -> GraphDataset:
        return self._dataset if self._dataset is not None else self._node.execute()

    def _bound_predicate(
        self, predicate: BoundPredicate
    ) -> tuple[ValuePredicate, tuple[Relation, ...]]:
        dependencies: list[Relation] = [self._node]

        def index(root: object, relation: Relation | None) -> int:
            for i, item in enumerate(dependencies):
                if item.root is root:
                    return i
                if (
                    isinstance(root, MethodNode)
                    and isinstance(root.parameters, (AssociationRead, ForecastRead))
                    and isinstance(item.root, MethodNode)
                    and item.root.parameters == root.parameters
                    and item.root.inputs[0].node is root.inputs[0].node
                ):
                    return i
            if relation is None or relation.root is not root:
                raise _reject(
                    "an exact bound predicate input",
                    "missing relation",
                    "Build predicates from relation.value.",
                )
            if (
                relation.runtime is not self._node.runtime
                or isinstance(relation.binding, FrozenBinding) != self._has_fixed()
            ):
                raise _reject(
                    "one Session and source/fixed mode",
                    "incompatible predicate input",
                    "Bind all predicate inputs in the receiver's mode and Session.",
                )
            dependencies.append(relation)
            return len(dependencies) - 1

        def bind(item: BoundPredicate) -> ValuePredicate:
            binding = self._node.root.signature.domain.binding
            if isinstance(item, CompositePredicate):
                return ValuePredicate(
                    binding,
                    item.operation,
                    0,
                    children=tuple(bind(child) for child in item.children),
                )
            if not isinstance(
                item, (CategoryPredicate, NumericPredicate, ScalarPredicate, StatePredicate)
            ):
                raise _reject(
                    "a closed bound predicate",
                    type(item).__name__,
                    "Build a typed predicate from relation.value.",
                )
            left = index(item.root, item.relation)
            if isinstance(item, StatePredicate):
                return ValuePredicate(binding, "is_defined", 0, input_index=left)
            value: (
                int
                | float
                | Decimal
                | str
                | bool
                | date
                | timedelta
                | NumericField
                | CategoryField
                | BooleanField
                | TemporalField
            )
            operation: Literal["eq", "lt", "le", "gt", "ge"]
            if isinstance(item, NumericPredicate):
                operation = (
                    "le"
                    if item.operation == "lte"
                    else "ge"
                    if item.operation == "gte"
                    else item.operation
                )
                value = item.threshold
            else:
                operation = "eq" if isinstance(item, CategoryPredicate) else item.operator
                value = item.value
            right = None
            if isinstance(value, (NumericField, CategoryField, BooleanField, TemporalField)):
                right = index(value.root, value.relation)
                literal: int | float | str | bool | TemporalLiteral | DurationLiteral | Decimal = 0
            elif isinstance(value, timedelta):
                literal = DurationLiteral(
                    (value.days * 86400 + value.seconds) * 1000000 + value.microseconds
                )
            elif isinstance(value, date):
                literal = TemporalLiteral(
                    "timestamp" if isinstance(value, datetime) else "date", value.isoformat()
                )
            else:
                literal = value
            assert operation in ("eq", "ne", "lt", "le", "gt", "ge")
            return ValuePredicate(binding, operation, literal, input_index=left, right_index=right)

        tree = bind(predicate)
        return tree, tuple(dependencies[1:])

    @property
    def subject_binding(self) -> SubjectBinding:
        """Return the producer-owned complete Subject mapping.

        Args: None.
        Returns: An immutable SubjectBinding for this exact instance domain.
        Example: ``binding = values.subject_binding``.
        Constraints: Requires a retained total Subject map; this property reads no business rows.
        """
        part = next(
            (item for item in self._node.root.signature.parts if isinstance(item, SubjectPart)),
            None,
        )
        if part is None or not part.total:
            raise _reject(
                "a retained total Subject map",
                "missing mapping",
                "Use an Entity or Entity/time producer retaining its Subject identities.",
            )
        return subject_binding(self._node.root.signature.domain, part)

    def _members_domain(
        self, through: SubjectBinding | None
    ) -> LogicalAnalysisDomain | LogicalFixedAnalysisDomain:
        node = self._subject_members(through)
        receiver = LogicalFixedAnalysisDomain if self._has_fixed() else LogicalAnalysisDomain
        return receiver(_TOKEN, node, self._runtime, inputs=(self,))

    def _subject_members(self, through: SubjectBinding | None) -> Relation:
        retention = next(
            (
                p
                for p in self._node.root.signature.parts
                if isinstance(p, (InstanceRetentionPart, SubjectRetentionPart))
            ),
            None,
        )
        if retention is not None and retention.selection != "true":
            from marivo.analysis.core.domain_captures import DomainPreparationError

            raise DomainPreparationError(
                "r7.retention_members",
                "construction",
                "a selected decidable true retention status",
                retention.selection,
                "Select retention.known_true(), then use its exact SubjectBinding for instance status.",
            )
        if (
            retention is not None
            and isinstance(retention, InstanceRetentionPart)
            and through is None
        ):
            raise _reject(
                "the retained Anchor SubjectBinding",
                "missing through",
                "Pass through=selected.subject_binding.",
            )
        if through is not None and (
            type(through) is not SubjectBinding or through != self.subject_binding
        ):
            raise _reject(
                "the exact retained SubjectBinding",
                "foreign mapping",
                "Acquire the binding from this relation.subject_binding.",
            )
        return self._node.selected_members()

    def _select(self, predicate: BoundPredicate) -> Relation:
        tree, dependencies = self._bound_predicate(predicate)
        return self._node.where(tree, dependencies=dependencies)

    def _classification_key(self, category: CategoryRelation) -> Coordinate:
        if (
            category._node.root.signature.domain.binding.session_id
            != self._node.root.signature.domain.binding.session_id
        ):
            raise _reject(
                "a same-Session classification",
                "a foreign Session",
                "Read the classification in this Session.",
            )
        return category._node.classification_coordinate()

    def _attribution_axes(
        self, axes: tuple[Ref[DimensionKind] | CategoryRelation, ...]
    ) -> tuple[Ref[DimensionKind] | Coordinate, ...]:
        if type(axes) is not tuple or any(
            not isinstance(
                axis,
                (
                    Ref,
                    LogicalCategoryRelation,
                    MaterializedCategoryRelation,
                    LogicalSelectedCategoryRelation,
                    MaterializedSelectedCategoryRelation,
                ),
            )
            for axis in axes
        ):
            raise _reject(
                "an ordered tuple of Dimensions or corresponding classifications",
                type(axes).__name__,
                "Pass axes=(dimension_or_classification, ...).",
            )
        return tuple(a if isinstance(a, Ref) else self._classification_key(a) for a in axes)

    def _group_nodes(
        self,
        keys: tuple[
            Ref[DimensionKind]
            | Ref[EntityKind]
            | LogicalCategoryRelation
            | MaterializedCategoryRelation
            | LogicalSelectedCategoryRelation
            | MaterializedSelectedCategoryRelation
            | TimeGrid
            | Grain,
            ...,
        ],
    ) -> tuple[Relation, Relation]:
        node = self._node
        references: list[Ref[DimensionKind] | Ref[EntityKind] | BoundTimeGrid | Coordinate] = []
        for key in keys:
            if isinstance(key, Grain):
                from marivo._temporal import time_scope

                source_grid = node.root.signature.domain.time_grid
                if source_grid is None:
                    raise _reject(
                        "one retained time axis", "no time grid", "Observe a time grid first."
                    )
                target = bind_grid(
                    time_scope(start=source_grid.cells[0].start, end=source_grid.cells[-1].end),
                    key,
                    report_timezone=source_grid.report_timezone,
                    explicit_timezone=source_grid.boundary_timezone,
                )
                references.append(target)
                continue
            if isinstance(key, TimeGrid):
                if key._bound is None or key._bound != node.root.signature.domain.time_grid:
                    raise _reject(
                        "the retained TimeGrid",
                        "foreign or unbound grid",
                        "Group by the grid carried by this relation.",
                    )
                references.append(key._bound)
                continue
            if isinstance(
                key,
                (
                    LogicalCategoryRelation,
                    MaterializedCategoryRelation,
                    LogicalSelectedCategoryRelation,
                    MaterializedSelectedCategoryRelation,
                ),
            ):
                coordinate = self._classification_key(key)
                retained = tuple(
                    dict.fromkeys(
                        (
                            *node.root.signature.domain.instance_key,
                            *(
                                c
                                for part in node.root.signature.parts
                                if isinstance(part, CoordinateStatePart)
                                and not part.attribution_only
                                for c in part.coordinates
                            ),
                        )
                    )
                )
                retained_matches = tuple(
                    c
                    for c in retained
                    if c == coordinate
                    or (c.field == coordinate.field and coordinate.binding_id in c.bindings)
                )
                if len(retained_matches) > 1:
                    raise _reject(
                        "one retained classification binding",
                        coordinate.field,
                        "Select an unambiguous classification role.",
                    )
                if retained_matches:
                    references.append(retained_matches[0])
                else:
                    node = node.attach_category(key._node)
                    references.append(coordinate)
            else:
                references.append(key)
        if isinstance(node.root.signature.quantity, (ObservedQuantity, RolledQuantity)):
            return node.rollup(*references), node
        coordinates: list[Coordinate] = []
        for reference in references:
            if isinstance(reference, BoundTimeGrid):
                coordinates.extend(
                    c for c in node.root.signature.domain.instance_key if c.role == "anchor"
                )
                continue
            if isinstance(reference, Coordinate):
                coordinates.append(reference)
                continue
            matches = [
                c
                for c in node.root.signature.domain.instance_key
                if (
                    c.entity_ref == reference and c.role == "identity"
                    if reference.kind == "entity"
                    else c.field == reference.path
                )
            ]
            if not matches or (reference.kind == "dimension" and len(matches) != 1):
                raise _reject(
                    "one retained complete coordinate",
                    reference.path,
                    "Use a retained axis or a corresponding categorical read.",
                )
            coordinates.extend(matches)
        return node.group_domain(tuple(coordinates)), node

    def _action_contract(self, names: tuple[str, ...]) -> tuple[AnalysisAction, ...]:
        from marivo.analysis._capabilities.registry import REGISTRY

        actions: list[AnalysisAction] = []
        for name in names:
            member = getattr(type(self), name, None)
            if isinstance(member, property):
                if any(
                    isinstance(p, HistoryViewPart) for p in self._node.root.signature.parts
                ) or name in (
                    "values",
                    "ranks",
                    "contribution",
                    "observed",
                    "reference",
                    "deviation",
                    "score",
                    "start",
                    "end",
                    "count",
                    "current",
                    "baseline",
                    "status",
                    "started_at",
                    "completed_at",
                    "duration",
                    "observed_duration",
                    "followup_until",
                ):
                    actions.append(
                        AnalysisAction("relation." + name, "analysis." + type(self).__name__)
                    )
                if name == "coefficient":
                    actions.append(
                        AnalysisAction(
                            "relation.coefficient",
                            "analysis.MaterializedCoefficientRelation"
                            if isinstance(self, _MaterializedValue)
                            else "analysis.LogicalCoefficientRelation",
                        )
                    )
                if name == "selected":
                    actions.append(
                        AnalysisAction(
                            "relation.selected",
                            "analysis.MaterializedBooleanRelation"
                            if isinstance(self, _MaterializedValue)
                            else "analysis.LogicalBooleanRelation",
                        )
                    )
                if name in ("prediction", "lower", "upper"):
                    actions.append(
                        AnalysisAction(
                            "relation." + name,
                            "analysis.MaterializedNumericRelation"
                            if isinstance(self, _MaterializedValue)
                            else "analysis.LogicalNumericRelation",
                        )
                    )
                continue
            if not callable(member):
                continue
            bound = getattr(self, name)
            descriptor = REGISTRY.by_callable(bound)
            arguments = required_arguments(signature(bound).parameters.values())
            actions.append(
                AnalysisAction(
                    f"relation.{name}({', '.join(arguments)})",
                    f"analysis.{descriptor.canonical_id}",
                )
            )
        return tuple(actions)


def _rank_relation(
    self: _Value,
    order: Literal["ascending", "descending"],
    ties: Literal["ordinal", "dense", "min", "max"],
    partition_by: tuple[CategoryRelation, ...],
) -> LogicalRankingResult:
    from marivo.analysis.materialization.graph_display import bind, invalid

    if type(partition_by) is not tuple or any(
        not isinstance(
            p,
            (
                LogicalCategoryRelation,
                MaterializedCategoryRelation,
                LogicalSelectedCategoryRelation,
                MaterializedSelectedCategoryRelation,
            ),
        )
        for p in partition_by
    ):
        raise invalid("partition_by must contain CategoryRelations")
    if len({p._node.root.identity for p in partition_by}) != len(partition_by):
        raise invalid("duplicate partition bindings")
    node = bind(
        (self._node, *(p._node for p in partition_by)),
        DisplayRank(
            self._node.root.value_type.name,
            order,
            ties,
            tuple(p._node.root.value_type.name for p in partition_by),
        ),
    )
    return LogicalRankingResult(_TOKEN, node, self._runtime, inputs=(self, *partition_by))


class _NumericComparison(_Value):
    """Shared numeric composition without granting original Metric reductions."""

    @property
    def value(self) -> NumericField:
        """Bind a predicate to this numeric quantity's exact current domain.

        Args: None.
        Returns: A NumericField carrying the receiver's original type and unit.
        Example: ``condition = daily.value.gt(20)``.
        Constraints: Numeric comparisons consume Defined Cells except within runs classification.
        """
        return NumericField(self._node.root, self._node)

    def correlate(
        self,
        *others: NumericRelation,
        method: Literal["pearson", "spearman", "kendall"] = "pearson",
        lag_range: range | None = None,
    ) -> LogicalAssociationResult:
        """Describe all pairs of corresponding quantities over their original complete domain.

        Args: others: One to fifteen distinct corresponding quantities. method: pearson, spearman, or kendall. lag_range: Signed offsets on the original complete time grid, or None for zero lag.
        Returns: A LogicalAssociationResult with coefficient and selected owned views.
        Example: ``result = revenue.correlate(orders, method="spearman")``.
        Constraints: One Session and source/fixed closure; pairwise ordinary Null deletion only. Pair observations by the complete original key tuple, retaining each Entity, field and coordinate role in pairing_key; association output keys identify candidates. Positive lag pairs the left t with right t+k. No causal or significance claim.
        """
        if any(not isinstance(v, _NumericComparison) for v in others):
            raise _reject(
                "corresponding NumericRelations",
                repr(tuple(type(v).__name__ for v in others)),
                "Use numeric quantities over this receiver's exact domain.",
            )
        return LogicalAssociationResult(
            _TOKEN,
            self._node.correlate(tuple(v._node for v in others), method, lag_range),
            self._runtime,
            inputs=(self, *others),
        )

    def forecast(
        self,
        *,
        horizon: ForecastHorizon,
        model: ForecastModel = _DEFAULT_FORECAST_MODEL,
        interval_level: float = 0.95,
    ) -> LogicalForecastResult:
        """Predict approved future periods with a named model and nominal normal interval.

        Args: horizon: Factory-produced periods(1..1000). model: naive(), drift(), or seasonal_naive(periods=s). interval_level: Finite float strictly between zero and one.
        Returns: A LogicalForecastResult with prediction, lower and upper owned views.
        Example: ``future = daily.forecast(horizon=mv.periods(4), model=mv.drift())``.
        Constraints: Complete Defined finite history and approved future grid; no imputation, model selection or interval addition. Numerical admission does not establish actual interval coverage.
        """
        if not isinstance(horizon, ForecastHorizon) or not isinstance(model, ForecastModel):
            raise _reject(
                "factory-produced ForecastHorizon and ForecastModel",
                repr((horizon, model)),
                "Use mv.periods() and the named model factories.",
            )
        kind: Literal["naive", "drift", "seasonal_naive"] = (
            "naive"
            if model.model_id == "naive@v1"
            else "drift"
            if model.model_id == "drift@v1"
            else "seasonal_naive"
        )
        return LogicalForecastResult(
            _TOKEN,
            self._node.forecast(horizon.count, kind, model.season_length, interval_level),
            self._runtime,
            inputs=(self,),
        )

    def runs(self, *, where: BoundPredicate) -> LogicalTimeRunResult:
        """Find maximal true intervals on the original complete time grid.

        Args: where: A predicate over exactly corresponding original fields.
        Returns: A LogicalTimeRunResult with start, end, count and duration views.
        Example: ``segments = daily.runs(where=daily.value.gt(20))``.
        Constraints: Requires a complete non-partial grid; unavailable dependencies break runs.
        """
        predicate, dependencies = self._bound_predicate(where)
        return LogicalTimeRunResult(
            _TOKEN, self._node.runs(predicate, dependencies), self._runtime, inputs=(self,)
        )

    def deviation(
        self,
        *,
        method: Literal["zscore", "mad"],
        partition_by: tuple[
            LogicalCategoryRelation
            | MaterializedCategoryRelation
            | LogicalSelectedCategoryRelation
            | MaterializedSelectedCategoryRelation,
            ...,
        ] = (),
    ) -> LogicalDeviationResult:
        """Fit signed equal-row deviation scores over the current numeric domain.

        Args:
            method: Required zscore or mad fit, with the registered numeric policy.
            partition_by: Explicit corresponding categorical relations; empty means one global fit.
        Returns: A LogicalDeviationResult with observed, reference, deviation and score views.
        Example: ``scored = change.deviation(method="mad")``.
        Constraints: One Session and source/fixed closure; no automatic threshold or partitioning.
        """
        if type(partition_by) is not tuple or any(
            not isinstance(
                item,
                (
                    LogicalCategoryRelation,
                    MaterializedCategoryRelation,
                    LogicalSelectedCategoryRelation,
                    MaterializedSelectedCategoryRelation,
                ),
            )
            for item in partition_by
        ):
            from marivo.analysis.errors import StatisticalRelationError

            raise StatisticalRelationError(
                code="r8.correspondence",
                operation="deviation",
                method=f"deviation.{method}@v1",
                input_identity=self._node.root.fingerprint,
                expected="a tuple of exactly corresponding CategoryRelations",
                received=repr(partition_by),
                repair="Read categories over this receiver's complete domain and pass them as a tuple.",
            )
        return LogicalDeviationResult(
            _TOKEN,
            self._node.deviation(method, tuple(item._node for item in partition_by)),
            self._runtime,
            inputs=(self, *partition_by),
        )

    def rank(
        self,
        *,
        order: Literal["ascending", "descending"],
        ties: Literal["ordinal", "dense", "min", "max"],
        partition_by: tuple[CategoryRelation, ...] = (),
    ) -> LogicalRankingResult:
        """Rank finite Defined values within explicit complete category partitions.

        Args: order: Ascending or descending values. ties: ordinal, dense, min or max. partition_by: Corresponding CategoryRelations, or one global partition.
        Returns: A logical ranking with same-key values and ranks views.
        Example: ``ranking = values.rank(order="descending", ties="dense")``.
        Constraints: Non-Defined Cells retain their reason and sort last; exact typed keys break ties. No epsilon ties or implicit coercion.
        """
        return _rank_relation(self, order, ties, partition_by)

    def share_of(self, reference: NumericRelation) -> LogicalNumericRelation:
        """Calculate shares against one immutable same-measure Singleton reference.

        Args: reference: An explicit original additive rollup in this Session and mode.
        Returns: A logical dimensionless numeric relation retaining its reference.
        Example: ``shares = values.share_of(values.rollup())``.
        Constraints: Requires proved support inclusion; zero denominators are Undefined.
        """
        from marivo.analysis.materialization.graph_reference import bind

        if not isinstance(reference, _NumericComparison):
            raise _reject("a NumericRelation", type(reference).__name__, "Bind a typed reference.")
        return LogicalNumericRelation(
            _TOKEN,
            bind(self._node, reference._node, "share"),
            self._runtime,
            inputs=(self, reference),
        )

    def standardize(self, *, reference: ReferenceWeights) -> LogicalNumericRelation:
        """Weight complete stratum values using an independently bound composition.

        Args: reference: ReferenceWeights with the same axes and statistical Entity.
        Returns: One Singleton standardized quantity preserving the measurement unit.
        Example: ``result = stratum_values.standardize(reference=weights).execute()``.
        Constraints: Missing strata reject; weights are never normalized; Duration rejects.
        """
        from marivo.analysis.materialization.graph_reference import bind

        if not isinstance(reference, ReferenceWeights):
            raise _reject("ReferenceWeights", type(reference).__name__, "Use mv.reference_weights.")
        return LogicalNumericRelation(
            _TOKEN,
            bind(
                self._node,
                reference._values._node,
                "standardize",
                unit=reference._unit,
                strata_dependencies=tuple(
                    item._node.definition.fingerprint for item in reference._strata
                ),
                reference_id=reference._identity,
            ),
            self._runtime,
            inputs=(self, reference._values, *reference._strata),
        )

    def compare(
        self,
        baseline: NumericRelation,
        *,
        design: TimeChange | CohortContrast | PeriodChange = _TIME_CHANGE,
        value: Literal["difference", "relative_change"] = "difference",
    ) -> LogicalDifferenceRelation:
        """Construct an absolute or relative comparison with ordered recursive endpoints.

        Args:
            baseline: Numeric endpoint with a compatible recursive quantity template.
            design: TimeChange, common-coordinate CohortContrast, or complete-grid PeriodChange.
            value: Absolute difference or change divided by the absolute baseline.
        Returns: A LogicalDifferenceRelation bound to this exact relation.
        Example: ``result = relation.compare(baseline)``.
        Constraints: Every frozen quantity node needs a registered comparison template.
        Time/period comparisons share target captures; cohorts share Group/Singleton coordinates and time.
        Use homogeneous numeric types; absolute Decimal differences need equal scales and Duration units must match.
        Float folds and quantiles require an error envelope and are not comparison-qualified.
        Restoring an old Difference requires re-executing its source comparison.
        """
        if (
            not isinstance(baseline, _NumericComparison)
            or type(design) not in (TimeChange, CohortContrast, PeriodChange)
            or value not in ("difference", "relative_change")
        ):
            raise _reject(
                "numeric endpoints, a closed comparison design and value choice",
                repr((type(baseline).__name__, type(design).__name__, value)),
                "Use typed numeric endpoints and mv.TimeChange(), mv.CohortContrast(), or mv.PeriodChange(alignment=mv.window_bucket()).",
            )
        return LogicalDifferenceRelation(
            _TOKEN,
            self._node.combine(
                baseline._node,
                value,
                design=design.kind,
                pairing=design.pairing.missing
                if isinstance(design.pairing, UnionKeys)
                else "exact",
                verification=design.pairing.verification
                if isinstance(design.pairing, ExactKeys)
                else "check",
            ),
            self._runtime,
            inputs=(self, baseline),
        )

    def ratio(
        self, other: NumericRelation, *, pairing: ExactKeys | OneToOneCorrespondence = _EXACT_KEYS
    ) -> LogicalNumericRelation:
        """Divide corresponding numeric values, preserving their ordered endpoints.

        Args:
            other: Denominator relation from the same Session and source/fixed mode.
            pairing: Unique complete typed-key correspondence.
        Returns: A LogicalNumericRelation with ordinary quotient semantics.
        Example: ``quotient = current.ratio(reference, pairing=mv.ExactKeys())``.
        Constraints: Time roles must match; zero denominators remain Undefined.
        Duration quotients preserve Unknown coverage or entry reasons as scalar
        Unknown; they do not cancel uncertainty when dividing a relation by itself.
        Ordinary ratios do not acquire original Metric rollup or attribution.
        Float folds and quantiles without retained error envelopes reject.
        """
        if not isinstance(other, _NumericComparison) or type(pairing) not in (
            ExactKeys,
            OneToOneCorrespondence,
        ):
            raise _reject(
                "ExactKeys or exact-node one_to_one",
                type(pairing).__name__,
                "Bind numeric endpoints and their explicit correspondence.",
            )
        if isinstance(pairing, OneToOneCorrespondence):
            if (
                pairing._left.root is not self._node.root
                or pairing._right.root is not other._node.root
            ):
                raise _reject(
                    "the correspondence's exact ordered endpoint nodes",
                    "correspondence reused for different nodes",
                    "Build one_to_one(left=this_relation, right=other, via=relationship).",
                )
            node = self._node.combine(
                other._node,
                "relation_ratio",
                design="period" if pairing._time is not None else "time",
                relationship=pairing._relationship,
                verification=pairing._time.pairing.verification
                if pairing._time is not None and isinstance(pairing._time.pairing, ExactKeys)
                else "check",
            )
        else:
            node = self._node.combine(
                other._node, "relation_ratio", verification=pairing.verification
            )
        return LogicalNumericRelation(_TOKEN, node, self._runtime, inputs=(self, other))


class _OriginalContinuation(_NumericComparison):
    def group_by(
        self,
        *dimensions: Ref[DimensionKind]
        | Ref[EntityKind]
        | LogicalCategoryRelation
        | MaterializedCategoryRelation
        | LogicalSelectedCategoryRelation
        | MaterializedSelectedCategoryRelation
        | TimeGrid
        | Grain,
        groups: LogicalAnalysisDomain
        | GroupedAnalysisDomain
        | MaterializedAnalysisDomain
        | None = None,
    ) -> GroupedNumericRelation | GroupedRatioRelation:
        """Retain selected coordinates for another original-state reduction.

        Args:
            dimensions: Retained complete axes or explicit corresponding classifications.
            groups: Optional explicit target retaining empty groups.
        Returns: A grouped original numeric or ratio relation.
        Example: ``result = relation.group_by(dimension).rollup().execute()``.
        Constraints: Removed axes cannot be recovered and grouping never reloads attributes.
        """
        grouped, rows = self._group_nodes(dimensions)
        if groups is not None:
            grouped = grouped.complete_groups(groups._node)
        quantity = self._node.root.signature.quantity
        cls = (
            GroupedRatioRelation
            if quantity is not None and quantity.method_version == "ratio@v1"
            else GroupedNumericRelation
        )
        return cls(
            _TOKEN,
            grouped,
            self._runtime,
            inputs=(self,),
            row_node=rows,
            target_node=None if groups is None else groups._node,
        )

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Reduce the current finished Cells into a new row statistic.

        Args: method: A descriptor returned by a current-row method factory.
        Returns: A logical RowStatistic independent of original Metric identity.
        Example: ``result = relation.summarize(mv.mean()).execute()``.
        Constraints: Numeric reducers require finite Defined Cells.
        """
        if not isinstance(method, RowMethod):
            raise _reject("a RowMethod", type(method).__name__, "Choose a row method factory.")
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )

    def rollup(self) -> LogicalRolledNumericRelation | LogicalRolledRatioRelation:
        """Merge retained original components over all remaining coordinates.

        Args: None.
        Returns: A logical original numeric or ratio total.
        Example: ``total = relation.rollup().execute()``.
        Constraints: Requires complete original state and coverage; no finished-value averaging.
        Count merges retained occurrence counts. Missing physical support reports the exact key
        and admitted physical profile candidates; execution never changes routes automatically.
        PostgreSQL Count rejects nested categorical state; overall, Entity and time-only groups remain supported.
        Execute a prepared observation after local Subject selection before rolling it up.
        """
        node = self._node.rollup()
        quantity = node.root.signature.quantity
        cls = (
            LogicalRolledRatioRelation
            if quantity is not None and quantity.method_version == "ratio@v1"
            else LogicalRolledNumericRelation
        )
        return cls(_TOKEN, node, self._runtime, inputs=(self,))


class _CountRelation(_Value):
    def group_by(
        self,
        *keys: Ref[DimensionKind]
        | Ref[EntityKind]
        | LogicalCategoryRelation
        | MaterializedCategoryRelation
        | LogicalSelectedCategoryRelation
        | MaterializedSelectedCategoryRelation
        | TimeGrid,
        groups: GroupedAnalysisDomain | MaterializedAnalysisDomain | None = None,
    ) -> GroupedAnalysisDomain:
        """Bind complete classification keys for current-row counts.

        Args:
            keys: Retained coordinates or corresponding categorical relations.
            groups: Optional complete target domain, including empty groups.
        Returns: A grouped count receiver and target domain.
        Example: ``result = category.group_by().summarize(mv.count()).execute()``.
        Constraints: Categories, including selections, group their own Defined values without keys; other scalar kinds form Singleton.
        """
        effective = keys or (
            (self,)
            if isinstance(
                self,
                (
                    LogicalCategoryRelation,
                    MaterializedCategoryRelation,
                    LogicalSelectedCategoryRelation,
                    MaterializedSelectedCategoryRelation,
                ),
            )
            else ()
        )
        node = self._node
        coordinates: list[Coordinate] = []
        for key in effective:
            if isinstance(key, TimeGrid):
                if key._bound is None or key._bound != node.root.signature.domain.time_grid:
                    raise _reject(
                        "the retained grid", "foreign grid", "Use the receiver's time grid."
                    )
                coordinates.extend(
                    c for c in node.root.signature.domain.instance_key if c.role == "anchor"
                )
                continue
            if isinstance(
                key,
                (
                    LogicalCategoryRelation,
                    MaterializedCategoryRelation,
                    LogicalSelectedCategoryRelation,
                    MaterializedSelectedCategoryRelation,
                ),
            ):
                coordinate = self._classification_key(key)
                retained_matches = tuple(
                    c
                    for c in node.root.signature.domain.instance_key
                    if c == coordinate
                    or (c.field == coordinate.field and coordinate.binding_id in c.bindings)
                )
                if len(retained_matches) > 1:
                    raise _reject(
                        "one retained classification binding",
                        coordinate.field,
                        "Select an unambiguous classification role.",
                    )
                if retained_matches:
                    coordinates.append(retained_matches[0])
                else:
                    node = node.attach_category(key._node)
                    coordinates.append(node.root.signature.domain.instance_key[-1])
            else:
                matches = [
                    c
                    for c in node.root.signature.domain.instance_key
                    if (
                        c.entity_ref == key and c.role == "identity"
                        if key.kind == "entity"
                        else c.field == key.path
                    )
                ]
                if not matches or (key.kind == "dimension" and len(matches) != 1):
                    raise _reject(
                        "retained grouping coordinates",
                        key.path,
                        "Pass a retained coordinate or corresponding categorical read.",
                    )
                coordinates.extend(matches)
        return GroupedAnalysisDomain(
            _TOKEN,
            node.group_domain(tuple(coordinates)),
            self._runtime,
            inputs=(self,),
            row_node=node,
            target_groups=groups,
        )

    def summarize(self, method: CountMethod) -> LogicalStatisticRelation:
        """Count current scalar rows under the selected Cell policy.

        Args: method: A descriptor from mv.count() or mv.count_defined().
        Returns: A new logical current-row statistic.
        Example: ``result = relation.summarize(mv.count_defined()).execute()``.
        Constraints: Numeric reducers are not admitted on categorical, temporal or boolean values.
        """
        if not isinstance(method, CountMethod):
            raise _reject(
                "mv.count() or mv.count_defined()",
                type(method).__name__,
                "Choose a count method for this scalar relation.",
            )
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )


class _MaterializedRead(_Value):
    def show(
        self, *, n: int | None = None, max_output_bytes: int | None = _DEFAULT_MAX_OUTPUT_BYTES
    ) -> None:
        """Show committed rows, their meaning, and explicit display omissions.

        Args:
            n: Maximum displayed rows; None means all and zero means metadata only.
            max_output_bytes: UTF-8 budget including the newline; None removes the budget.
        Returns: None; prints retained values and interpretation boundaries.
        Example: ``relation.show(n=20)``.
        Constraints: Reads verified committed state only, redacts member identities,
            and preserves Cell states. Row limits do not change the result.
        """
        _validate_display(n, max_output_bytes)
        assert self._dataset is not None
        checked = self._dataset.verified()
        disclosure = self._contract_facts(checked=checked, display=True)
        meaning = {
            "unit",
            "method",
            "metric",
            "statistical_unit",
            "field",
            "field_kind",
            "field_path",
            "window",
            "start_selection",
            "within",
            "history_view",
            "entry_axes",
            "association_method",
            "forecast_model",
            "horizon",
            "interval_level",
        }
        meaning.update(
            name
            for name, _ in disclosure
            if name.startswith(
                ("contribution_binding_", "coordinate_binding_", "classification_role_")
            )
        )
        self._dataset.show(
            n=n,
            max_output_bytes=max_output_bytes,
            checked=checked,
            facts=(
                ("kind", _kind(self._node)),
                *(item for item in disclosure if item[0] in meaning),
            ),
            boundaries=tuple(item for item in disclosure if item[0] not in meaning),
            findings=True,
        )

    def evidence_digest(self) -> ArtifactDigest:
        """Read the retained Evidence summary of this committed Artifact.

        Args: None.
        Returns: The exact ArtifactDigest including Finding count and extractor versions.
        Example: ``digest = result.evidence_digest()``.
        Constraints: Reads committed Evidence without re-reading result payloads.
        """
        assert self._dataset is not None
        return self._dataset.evidence_digest()

    def findings(self, limit: int = 20, cursor: str | None = None) -> FindingPage:
        """Read one bounded page from this Artifact's frozen Finding collection.

        Args: limit: Exact integer 1..100. cursor: This Artifact's previous page cursor.
        Returns: A FindingPage in frozen extractor order.
        Example: ``page = result.findings(limit=20)``.
        Constraints: The cursor belongs to this Artifact; local committed Findings are trusted.
        """
        assert self._dataset is not None
        return self._dataset.findings(limit, cursor)

    def finding(self, finding_id: str) -> Finding:
        """Read one exact Finding owned by this committed Artifact.

        Args: finding_id: Identity obtained from this Artifact's FindingPage.
        Returns: The checked immutable Finding.
        Example: ``item = result.finding(page.items[0].finding_id)``.
        Constraints: Foreign or missing identities reject without skipping corrupt rows.
        """
        assert self._dataset is not None
        return self._dataset.finding(finding_id)

    def to_pandas(self) -> pd.DataFrame:
        """Return an isolated complete DataFrame under governed read checks.

        Args:
            None.
        Returns: An isolated DataFrame of the exact result.
        Example: ``result = relation.to_pandas()``.
        Constraints: Requires a valid committed Artifact and returns an isolated copy.
        """
        assert self._dataset is not None
        return self._dataset.to_pandas()


class _MaterializedValue(_MaterializedRead):
    @property
    def state(self) -> MaterializedDatasetState:
        """Return the exact committed Artifact and Run identity.

        Args:
            None.
        Returns: The committed MaterializedDatasetState.
        Example: ``result = relation.state``.
        Constraints: Only materialized values have committed Artifact state.
        """
        assert self._dataset is not None
        return self._dataset.state


class _CohortDomain(_Value):
    def penetration_in(self, reference: AnalysisDomain) -> LogicalNumericRelation:
        """Calculate the exact member intersection divided by a fixed reference count.

        Args: reference: A complete same-Entity member domain in this Session and mode.
        Returns: One dimensionless Singleton NumericRelation.
        Example: ``rate = selected.penetration_in(all_members).execute()``.
        Constraints: Composite identities are complete; empty reference is Undefined.
        """
        from marivo.analysis.materialization.graph_reference import bind

        if not isinstance(reference, _CohortDomain):
            raise _reject("an AnalysisDomain", type(reference).__name__, "Bind complete members.")
        return LogicalNumericRelation(
            _TOKEN,
            bind(self._node, reference._node, "penetration"),
            self._runtime,
            inputs=(self, reference),
        )

    def cohort(
        self, predicate: BoundPredicate, *, rule: CohortRule, through: SubjectBinding | None = None
    ) -> LogicalAnalysisDomain | LogicalFixedAnalysisDomain:
        """Select exact Subjects using their full retained opportunity domain.

        Args:
            predicate: Typed conditions over complete Entity or Entity/time opportunities.
            rule: any_instance, at_least, or all_instances with explicit empty policy.
            through: Optional exact SubjectBinding acquired from the opportunity producer.
        Returns: A logical source or fixed AnalysisDomain with decision evidence.
        Example: ``members.cohort(values.value.gt(0), rule=mv.at_least(3))``.
        Constraints: Every Subject must be decidable; missing opportunities and Null/Undefined comparisons reject.
        """
        if _kind(self._node) != "members" or not isinstance(
            rule, (AnyInstance, AtLeast, AllInstances)
        ):
            raise _reject(
                "an Entity target and closed cohort rule",
                type(rule).__name__,
                "Use original members.cohort(..., rule=mv.any_instance()).",
            )
        tree, dependencies = self._bound_predicate(predicate)
        subject = (
            next(
                (
                    part
                    for part in dependencies[0].root.signature.parts
                    if isinstance(part, SubjectPart)
                ),
                None,
            )
            if dependencies
            else None
        )
        if through is not None and (
            subject is None
            or through != subject_binding(dependencies[0].root.signature.domain, subject)
        ):
            raise _reject(
                "the opportunity producer's exact SubjectBinding",
                "foreign mapping",
                "Acquire through from the opportunity relation.subject_binding.",
            )
        node = self._node.cohort(tree, dependencies, rule)
        result_type = LogicalFixedAnalysisDomain if self._has_fixed() else LogicalAnalysisDomain
        return result_type(_TOKEN, node, self._runtime, inputs=(self,))


class LogicalAnalysisDomain(_CohortDomain):
    """Unexecuted governed Entity membership and its selected subdomains."""

    def _bind_time_grid(self, grid: TimeGrid) -> tuple[Relation, BoundTimeGrid]:
        """Bind one operation's complete member/time product without a public receiver."""
        if not isinstance(grid, TimeGrid):
            raise _reject("TimeGrid", type(grid).__name__, "Use mv.time_grid().")
        live = self._node._live()
        owner = self._runtime._source_context.current
        snapshot = None
        if grid._grain.kind == "semantic" and owner is not None:
            snapshot = next(
                (
                    s
                    for s in owner.period_calendar_snapshots
                    if s.calendar_ref == grid._grain.calendar
                ),
                None,
            )
        bound = grid._bind(live.report_timezone, snapshot)
        retained = self._node.root.signature.domain.time_grid
        if retained is not None:
            if retained != bound:
                raise _reject(
                    "one exact time grid", "conflicting grid", "Use the operation's own grid."
                )
            return self._node, bound
        return self._node.each(bound), bound

    def execute(self) -> MaterializedAnalysisDomain:
        """Evaluate and publish this exact member domain.

        Args:
            None.
        Returns: A MaterializedAnalysisDomain bound to this exact relation.
        Example: ``result = relation.execute()``.
        Constraints: A source branch reevaluates; a fixed branch uses exact retained Artifacts.
        """
        return MaterializedAnalysisDomain(_TOKEN, self._node, self._runtime, dataset=self._run())

    @overload
    def read(
        self,
        field: Ref[MeasureKind],
        *,
        at: datetime | BeforeEndBoundary | GridEndpoint | None = None,
        via: Ref[RelationshipKind] | RootRoute | RootRoutes | None = None,
        match_verification: Literal["check", "assume"] = "check",
    ) -> LogicalNumericRelation: ...

    @overload
    def read(
        self,
        field: Ref[DimensionKind],
        *,
        at: datetime | BeforeEndBoundary | GridEndpoint | None = None,
        via: Ref[RelationshipKind] | RootRoute | RootRoutes | None = None,
        match_verification: Literal["check", "assume"] = "check",
    ) -> LogicalCategoryRelation | LogicalBooleanRelation: ...

    @overload
    def read(
        self,
        field: Ref[TimeDimensionKind],
        *,
        at: datetime | BeforeEndBoundary | GridEndpoint | None = None,
        via: Ref[RelationshipKind] | RootRoute | RootRoutes | None = None,
        match_verification: Literal["check", "assume"] = "check",
    ) -> LogicalTemporalRelation: ...

    def read(
        self,
        field: Ref[MeasureKind] | Ref[DimensionKind] | Ref[TimeDimensionKind],
        *,
        at: datetime | BeforeEndBoundary | GridEndpoint | None = None,
        via: Ref[RelationshipKind] | RootRoute | RootRoutes | None = None,
        match_verification: Literal["check", "assume"] = "check",
    ) -> (
        LogicalNumericRelation
        | LogicalCategoryRelation
        | LogicalBooleanRelation
        | LogicalTemporalRelation
    ):
        """Read one typed attribute for every current complete member identity.

        Args:
            field: Declared Measure, direct Dimension/TimeDimension, or bound Boolean Dimension expression Ref.
            at: Independent aware attribute instant or grid endpoint; an endpoint binds every member/time cell. Unversioned fields accept None or a grid endpoint and keep their stable value.
            via: Relationship Ref, RootRoute or one-entry RootRoutes selecting a member-to-owner role; omit or pass None to infer the unique directed to-one path.
            match_verification: Check unknown owner matching, or assume it for this call.
        Returns: Numeric, Category, Boolean or Temporal relation according to field kind.
        Example: ``values = members.read(field, at=scope.before_end)``.
        Constraints: Unknown matching is checked by default; assume records a call premise. Declared to-one cardinality is trusted. Member versions remain independent; before_end is a symbolic left limit.
        """
        point: datetime | BeforeEndBoundary | GridPoint | None = (
            at if not isinstance(at, GridEndpoint) else None
        )
        receiver = self._node
        if isinstance(at, GridEndpoint):
            receiver, bound = self._bind_time_grid(at._grid)
            point = GridPoint(bound, at._side)
        node = receiver.read(field, at=point, via=via, match_verification=match_verification)
        if field.kind is SemanticKind.MEASURE:
            return LogicalNumericRelation(_TOKEN, node, self._runtime, inputs=(self,))
        if field.kind is SemanticKind.TIME_DIMENSION:
            return LogicalTemporalRelation(_TOKEN, node, self._runtime, inputs=(self,))
        if node.root.value_type == ScalarType("boolean"):
            return LogicalBooleanRelation(_TOKEN, node, self._runtime, inputs=(self,))
        return LogicalCategoryRelation(_TOKEN, node, self._runtime, inputs=(self,))

    def group_by(
        self,
        *keys: Ref[DimensionKind] | LogicalCategoryRelation | LogicalSelectedCategoryRelation,
        groups: LogicalAnalysisDomain | GroupedAnalysisDomain | None = None,
    ) -> GroupedAnalysisDomain:
        """Bind a complete combination of member classifications.

        Args:
            keys: Own Dimension Refs or explicitly corresponding categorical reads.
            groups: Optional explicit target domain, including empty groups.
        Returns: A lazy grouped member domain; no keys denotes Singleton.
        Example: ``targets = members.group_by(region).execute()``.
        Constraints: Classifications must be Defined on consumed members and align by full keys.
        """
        node = self._node
        categories: list[LogicalCategoryRelation | LogicalSelectedCategoryRelation] = []
        coordinates: list[Coordinate] = []
        for key in keys:
            category = self.read(key) if isinstance(key, Ref) else key
            if not isinstance(category, (LogicalCategoryRelation, LogicalSelectedCategoryRelation)):
                raise _reject(
                    "a categorical Dimension",
                    type(category).__name__,
                    "Use a qualified categorical classification.",
                )
            node = node.attach_category(category._node)
            categories.append(category)
            coordinates.append(node.root.signature.domain.instance_key[-1])
        target = node.group_domain(tuple(coordinates))
        return GroupedAnalysisDomain(
            _TOKEN,
            target,
            self._runtime,
            inputs=(self, *categories),
            row_node=node,
            target_groups=groups,
        )

    def observe(
        self,
        metric: MetricInputValue,
        *,
        during: TimeScope | TimeGrid | None = None,
        at: datetime | GridEndpoint | None = None,
        via: Ref[RelationshipKind] | RootRoute | RootRoutes | None = None,
        by: tuple[
            Ref[EntityKind]
            | Ref[DimensionKind]
            | LogicalCategoryRelation
            | LogicalSelectedCategoryRelation,
            ...,
        ] = (),
        groups: LogicalAnalysisDomain | GroupedAnalysisDomain | None = None,
        complete_during: tuple[TimeScope, ...] | None = None,
    ) -> LogicalNumericRelation | LogicalRatioRelation:
        """Compute a governed Metric directly at the requested output grain.

        Args:
            metric: Declared Metric Ref or closed runtime Metric expression to observe.
            during: Fixed TimeScope, a TimeGrid selecting each bucket's window, or None for no added restriction.
            at: Explicit cumulative endpoint, grid endpoint binding every time cell, or aware datetime.
            via: Relationship Ref, RootRoute or RootRoutes selecting contribution roles by root identity. Omit or pass None to infer unique directed to-one paths; partial overrides leave other roots automatic.
            by: Ordered tuple of the receiver's member Entity, categorical Dimensions, or
                same-Session logical member or contribution-root classifications. Independent scalar Dimension branches are allowed. The Entity retains its full primary key.
                Empty means Singleton on ordinary members, or one overall value per time bucket.
            groups: Optional same-Session logical target domain retaining explicit empty groups.
            complete_during: Explicit business-complete scopes with aware datetime bounds.
                Omit for the existing observation policy; an empty tuple declares no complete buckets.
        Returns: An original LogicalNumericRelation | LogicalRatioRelation at the selected grain.
        Example: ``result = relation.observe(metric, during=mv.time_scope(start="2026-08-01", end="2026-09-01"), via=buyer)``.
        Constraints: During and at are alternatives. Ambiguous roles require via or an explicitly bound read in by. Contribution and classification paths are independent; every component must bind every axis on complete keys without fanout. Versioned fields still require an explicit attribute time. By cannot introduce a time grid; timed classifications must match the bound grid. Ordinary Metric/Count paths are directed keyed to-one between unversioned Entities. Physical admission never selects another role. Completeness requires one original sum on during=grid without grouping or at; uncovered buckets are Unknown. Business-covered observations cannot roll up; production uses DuckDB table/Parquet.
        """
        if during is not None and at is not None:
            raise _reject(
                "one observation window or endpoint",
                "both during and at",
                "Pass during=scope/grid or at=instant/grid.end, not both.",
            )
        if during is not None and not isinstance(during, (TimeScope, TimeGrid)):
            raise _reject(
                "TimeScope or TimeGrid", type(during).__name__, "Pass during=scope or during=grid."
            )
        node = self._node
        window: TimeScope | BoundTimeGrid | None = during if isinstance(during, TimeScope) else None
        if isinstance(during, TimeGrid):
            node, window = self._bind_time_grid(during)
        point: datetime | GridPoint | None = at if not isinstance(at, GridEndpoint) else None
        if isinstance(at, GridEndpoint):
            node, bound_point = self._bind_time_grid(at._grid)
            point = GridPoint(bound_point, at._side)
        if type(by) is not tuple:
            raise _reject(
                "an ordered tuple of typed observation axes",
                type(by).__name__,
                "Pass by=(axis, ...).",
            )
        if groups is not None and not isinstance(
            groups, (LogicalAnalysisDomain, GroupedAnalysisDomain)
        ):
            raise _reject(
                "a same-Session logical target domain",
                type(groups).__name__,
                "Use a logical complete target domain for groups.",
            )
        subject = next(p for p in node.root.signature.parts if isinstance(p, SubjectPart))
        live = node._live()
        metric_contract = node.resolve_metric(metric)
        member_classifier_base = node
        from marivo.analysis.observation.relationship_binding import RelationshipResolver
        from marivo.refs import ref as semantic_ref
        from marivo.semantic.validator import normalize_target_dimension

        resolver = RelationshipResolver.build(live.graph.registry)
        root_refs = tuple(
            semantic_ref.entity(root.path) for root in metric_contract.computation_roots
        )
        overrides = resolver.overrides(
            via, roots=root_refs, target="dsl.LogicalAnalysisDomain.observe"
        )
        paths_list: list[tuple[Ref[RelationshipKind], ...]] = []
        for root in root_refs:
            prefixes = tuple(
                tuple(semantic_ref.relationship(item.path) for item in component.event_time_path)
                for component in metric_contract.components
                if component.computation_root.path == root.path
            )
            prefix = builtins.max(prefixes, key=len, default=())
            if any(prefix[: len(item)] != item for item in prefixes):
                raise _reject(
                    "compatible declared component roles",
                    root.path,
                    "Observe conflicting component roles independently.",
                )
            paths_list.append(
                resolver.resolve(
                    root.path,
                    subject.entity_ref.path,
                    explicit=overrides.get(root.path),
                    prefix=prefix,
                )
            )
        paths = tuple(paths_list)
        keys: list[Coordinate] = []
        categories: list[LogicalCategoryRelation | LogicalSelectedCategoryRelation] = []
        contribution_domains: dict[str, Relation] = {}
        contribution_bases: dict[str, Relation] = {}
        classification_coordinates: list[Coordinate] = []

        def contribution_domain(root: Ref[EntityKind]) -> Relation:
            if root.path not in contribution_bases:
                contribution_bases[root.path] = Relation.members(
                    self._runtime, live.graph.registry, live.sidecar, live.report_timezone, root
                )
            return contribution_bases[root.path]

        for axis in by:
            if isinstance(axis, Ref) and axis.kind == "entity":
                if axis != subject.entity_ref:
                    raise _reject(
                        "the receiver's member Entity",
                        axis.path,
                        "Use the complete member Entity ref in by.",
                    )
                keys.extend(subject.subject_key)
                continue
            category: LogicalCategoryRelation | LogicalSelectedCategoryRelation | None = None
            if isinstance(axis, Ref) and axis.kind == "dimension":
                dimension = semantic_ref.dimension(axis.path)
                field = normalize_target_dimension(live.graph.registry, axis.path)
                if field.entity_ref.path == subject.entity_ref.path:
                    member_read = member_classifier_base.read(dimension, resolver=resolver)
                    node = node.attach_category(member_read)
                    keys.append(member_read.classification_coordinate())
                    continue
                member_paths = resolver.candidates(subject.entity_ref.path, field.entity_ref.path)
                if len(member_paths) == 1 and all(
                    resolver.candidates(
                        root.path,
                        field.entity_ref.path,
                        bound_member=(subject.entity_ref.path, route_path),
                    )
                    == ((*route_path, *member_paths[0]),)
                    for root, route_path in zip(root_refs, paths, strict=True)
                ):
                    member_read = member_classifier_base.read(
                        dimension,
                        via=RootRouteValue(subject.entity_ref, member_paths[0]),
                        resolver=resolver,
                    )
                    node = node.attach_category(member_read)
                    keys.append(member_read.classification_coordinate())
                    continue
            elif isinstance(axis, (LogicalCategoryRelation, LogicalSelectedCategoryRelation)):
                category = axis
                categories.append(category)
                coordinate = category._node.classification_coordinate()
                dimension = semantic_ref.dimension(coordinate.field)
                field = normalize_target_dimension(live.graph.registry, dimension.path)
                category_subject = next(
                    p for p in category._node.root.signature.parts if isinstance(p, SubjectPart)
                )
                category_grid = category._node.root.signature.domain.time_grid
                if (
                    category_grid is not None
                    and category_grid != node.root.signature.domain.time_grid
                ):
                    raise _reject(
                        "a classification on the observation's exact time grid",
                        "an unbound or different classification grid",
                        "Bind the observation and classification to the same grid.",
                    )
                if category_subject.entity_ref == subject.entity_ref:
                    node = node.attach_category(category._node)
                    keys.append(coordinate)
                    continue
                if category_subject.entity_ref not in root_refs:
                    raise _reject(
                        "a classification on members or a Metric contribution root",
                        category_subject.entity_ref.path,
                        "Read the Dimension on the observation members or a declared contribution root.",
                    )
            else:
                raise _reject(
                    "a member Entity, categorical Dimension or logical classification",
                    type(axis).__name__,
                    "Choose a typed axis in by.",
                )
            bound_categories: list[tuple[Ref[EntityKind], Relation]] = []
            for root, route_path in zip(root_refs, paths, strict=True):
                base = contribution_domain(root)
                if category is not None and category_subject.entity_ref == root:
                    read = category._node
                else:
                    owner = field.entity_ref.path
                    # On-route fields inherit the selected contribution role exactly.
                    current = root.path
                    read_path: tuple[Ref[RelationshipKind], ...] | None = (
                        () if owner == current else None
                    )
                    for i, relationship in enumerate(route_path):
                        mapping = next(
                            m for m in resolver.mappings if m.ref.path == relationship.path
                        )
                        current = mapping.to_entity_ref.path
                        if current == owner:
                            read_path = route_path[: i + 1]
                            break
                    if read_path is None:
                        read_path = resolver.resolve(
                            root.path, owner, bound_member=(subject.entity_ref.path, route_path)
                        )
                    read = base.read(
                        dimension, via=RootRouteValue(root, read_path), resolver=resolver
                    )
                bound_categories.append((root, read))
            bound_coordinates = tuple(
                read.classification_coordinate() for _, read in bound_categories
            )
            coordinate = bound_coordinates[0]
            if any(item != coordinate for item in bound_coordinates[1:]):
                identities = tuple(sorted({item.binding_id for item in bound_coordinates}))
                coordinate = replace(
                    coordinate,
                    binding_id=coordinate_binding_id(
                        coordinate.entity_ref, coordinate.field, identities
                    ),
                    bindings=identities,
                )
            for root, read in bound_categories:
                classified = contribution_domains.get(root.path, contribution_domain(root))
                read_grid = read.root.signature.domain.time_grid
                if read_grid is not None and classified.root.signature.domain.time_grid is None:
                    classified = classified.each(read_grid)
                contribution_domains[root.path] = classified.attach_category(
                    read, coordinate=coordinate
                )
            classification_coordinates.append(coordinate)
            keys.append(coordinate)
        if len(set(keys)) != len(keys):
            raise _reject(
                "distinct observation bindings", repr(by), "Remove repeated bindings from by."
            )
        keys.extend(c for c in node.root.signature.domain.instance_key if c.role == "anchor")
        if complete_during is not None and (
            not isinstance(during, TimeGrid)
            or at is not None
            or any(c.role == "group" for c in keys)
            or len(metric_contract.components) > 1
        ):
            raise _reject(
                "a single original sum on during=grid without dimension grouping or at",
                "incompatible business-completeness observation",
                "Use members.observe(sum_metric, during=grid, complete_during=(scope,)).",
            )
        classifications = tuple(
            (root, relation._live().graph) for root, relation in contribution_domains.items()
        )
        if len(metric_contract.components) > 1:
            observed = node.observe_routes(
                metric,
                metric_contract=metric_contract,
                during=window,
                at=point,
                paths=paths,
                classifications=classifications,
                classification_coordinates=tuple(classification_coordinates),
                resolver=resolver,
                target_keys=tuple(keys),
            )
            if groups is not None:
                observed = observed.complete_groups(groups._node)
            if (
                observed.root.signature.quantity is not None
                and observed.root.signature.quantity.method_version == "linear@v1"
            ):
                return LogicalNumericRelation(
                    _TOKEN, observed, self._runtime, inputs=(self, *categories)
                )
            return LogicalRatioRelation(_TOKEN, observed, self._runtime, inputs=(self, *categories))
        if len(paths) != 1:
            raise _reject(
                "one route for the single contribution root",
                f"{len(paths)} routes",
                "Pass exactly the route of the observed contribution root.",
            )
        single = paths[0]
        observed = node.observe(
            metric,
            metric_contract=metric_contract,
            during=window,
            at=point,
            via=single[0] if len(single) == 1 else single,
            classifications=dict(classifications).get(root_refs[0].path),
            classification_coordinates=tuple(classification_coordinates),
            resolver=resolver,
            target_keys=tuple(keys),
        )
        if groups is not None:
            observed = observed.complete_groups(groups._node)
        if complete_during is not None:
            observed = observed.business_coverage(complete_during)
        return LogicalNumericRelation(_TOKEN, observed, self._runtime, inputs=(self, *categories))


class MaterializedAnalysisDomain(_MaterializedValue, _CohortDomain):
    """Exact fixed Entity membership; it cannot introduce a new live observation."""


class LogicalFixedAnalysisDomain(_CohortDomain):
    """Selected fixed members awaiting a retained local projection."""

    def execute(self) -> MaterializedAnalysisDomain:
        """Publish only the selected member identity from its fixed predecessor.

        Args:
            None.
        Returns: A MaterializedAnalysisDomain bound to this exact relation.
        Example: ``result = relation.execute()``.
        Constraints: A source branch reevaluates; a fixed branch uses exact retained Artifacts.
        """
        return MaterializedAnalysisDomain(_TOKEN, self._node, self._runtime, dataset=self._run())

    if not TYPE_CHECKING:
        # Static callers see only execute(); dynamic callers still receive a repair.
        def __getattr__(self, name: str) -> NoReturn:
            if name in ("read", "group_by", "observe"):
                fit = next(
                    (
                        part
                        for node in topology(self._node.root)
                        for part in node.signature.parts
                        if isinstance(part, FitInputsPart)
                    ),
                    None,
                )
                if name == "observe" and fit is not None:
                    from marivo.analysis.errors import StatisticalRelationError

                    raise StatisticalRelationError(
                        code="r8.retained_part",
                        operation="observe",
                        method=f"deviation.{fit.method}@v1",
                        input_identity=self._node.root.fingerprint,
                        expected="a retained observation contract with contributions, path, time and full Subject keys",
                        received=f"fixed selected Subject with fit_scope={fit.fit_id}; no retained observation contract",
                        repair="Read the already captured follow-up numeric Artifact and summarize it. Prepare a new observation in a complete logical source chain before execute().",
                    )
                raise _reject(
                    "source-only or fixed-only inputs",
                    "fixed selected members plus live Metric"
                    if name == "observe"
                    else "fixed selected members plus live Dimension",
                    "Construct the selection from logical source members before execution.",
                )
            raise AttributeError(name)


class GroupedAnalysisDomain(_Value):
    """Logical grouping descriptor for one member Dimension."""

    def __init__(
        self,
        token: object,
        node: Relation,
        runtime: DatasetRuntime,
        *,
        inputs: tuple[_Value, ...],
        target_groups: LogicalAnalysisDomain
        | GroupedAnalysisDomain
        | MaterializedAnalysisDomain
        | None = None,
        row_node: Relation | None = None,
    ) -> None:
        if target_groups is not None:
            node = node.complete_groups(target_groups._node)
        super().__init__(token, node, runtime, inputs=inputs)
        self._target_groups = target_groups
        self._row_node = row_node

    def contract(self) -> AnalysisContract:
        """Disclose only continuations bound by this grouping descriptor.

        Args: None.
        Returns: The bounded current grouping contract.
        Example: ``groups.contract().show()``.
        Constraints: Target-only groups do not create observations or row state.
        """
        names: tuple[str, ...] = ("execute",)
        if self._row_node is not None:
            names = (*names, "summarize")
        return replace(super().contract(), actions=self._action_contract(names))

    def summarize(self, method: CountMethod) -> LogicalStatisticRelation:
        """Count current rows within these complete groups.

        Args: method: mv.count() or mv.count_defined().
        Returns: A new grouped row-count statistic.
        Example: ``result = category.group_by().summarize(mv.count()).execute()``.
        Constraints: Requires a scalar row receiver; target-only domains cannot invent rows.
        """
        if not isinstance(method, CountMethod) or self._row_node is None:
            raise _reject(
                "a scalar count receiver and CountMethod",
                type(method).__name__,
                "Group a scalar relation and choose a count factory.",
            )
        node = self._row_node.summarize(
            method.kind, coordinates=self._node.root.signature.domain.instance_key
        )
        if self._target_groups is not None:
            node = node.complete_groups(self._target_groups._node)
        return LogicalStatisticRelation(_TOKEN, node, self._runtime, inputs=(self,))

    def execute(self) -> MaterializedAnalysisDomain:
        """Retain the complete explicit target-group domain.

        Args: None.
        Returns: A fixed group domain usable as groups in fixed continuations.
        Example: ``targets = category.group_by().execute()``.
        Constraints: Validate consumed keys against explicit targets and retain all target groups.
        """
        return MaterializedAnalysisDomain(_TOKEN, self._node, self._runtime, dataset=self._run())


class LogicalCategoryRelation(_CountRelation):
    """Unexecuted categorical member attribute relation."""

    def members(
        self, *, through: SubjectBinding | None = None
    ) -> LogicalAnalysisDomain | LogicalFixedAnalysisDomain:
        """Project complete Subject identities from this relation.

        Args: through: Optional exact producer-owned SubjectBinding; Entity projection is implicit.
        Returns: A source member domain or a fixed-only member continuation.
        Example: ``selected_members = relation.members()``.
        Constraints: Requires a retained total Subject map; fixed inputs cannot introduce sources.
        """
        return self._members_domain(through)

    @property
    def value(self) -> CategoryField:
        """Return this relation's bound categorical field.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        return CategoryField(self._node.root, self._node)

    def where(self, predicate: BoundPredicate) -> LogicalSelectedCategoryRelation:
        """Select rows using a predicate bound to this category relation.

        Args:
            predicate: Closed typed predicate over exact corresponding inputs.
        Returns: A LogicalSelectedCategoryRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: Every referenced input must cover this complete domain; all children are checked.
        """
        return LogicalSelectedCategoryRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    def execute(self) -> MaterializedCategoryRelation:
        """Evaluate and publish this categorical relation.

        Args:
            None.
        Returns: A MaterializedCategoryRelation bound to this exact relation.
        Example: ``result = relation.execute()``.
        Constraints: A source branch reevaluates; a fixed branch uses exact retained Artifacts.
        """
        return MaterializedCategoryRelation(_TOKEN, self._node, self._runtime, dataset=self._run())


class MaterializedCategoryRelation(_MaterializedValue, _CountRelation):
    """Fixed categorical relation retaining admitted member identity."""

    def members(self, *, through: SubjectBinding | None = None) -> LogicalFixedAnalysisDomain:
        """Project retained complete Subject identities without source access.

        Args: through: Optional exact producer-owned SubjectBinding; Entity projection is implicit.
        Returns: A fixed-only logical member continuation.
        Example: ``members = result.members()``.
        Constraints: Requires the exact retained Subject part and cannot read external attributes.
        """
        return LogicalFixedAnalysisDomain(
            _TOKEN, self._subject_members(through), self._runtime, inputs=(self,)
        )

    @property
    def value(self) -> CategoryField:
        """Return this relation's bound categorical field.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        return CategoryField(self._node.root, self._node)

    def where(self, predicate: BoundPredicate) -> LogicalSelectedCategoryRelation:
        """Build a fixed-only categorical selection.

        Args:
            predicate: Closed typed predicate over exact corresponding inputs.
        Returns: A LogicalSelectedCategoryRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: Every referenced input must cover this complete domain; all children are checked.
        """
        return LogicalSelectedCategoryRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )


class LogicalSelectedCategoryRelation(_CountRelation):
    """Unexecuted category selection over one exact read relation."""

    @property
    def value(self) -> CategoryField:
        """Return this relation's bound categorical field.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        return CategoryField(self._node.root, self._node)

    def where(self, predicate: BoundPredicate) -> LogicalSelectedCategoryRelation:
        """Select rows using a predicate bound to this category relation.

        Args:
            predicate: Closed typed predicate over exact corresponding inputs.
        Returns: A LogicalSelectedCategoryRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: Every referenced input must cover this complete domain; all children are checked.
        """
        return LogicalSelectedCategoryRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    def members(
        self, *, through: SubjectBinding | None = None
    ) -> LogicalAnalysisDomain | LogicalFixedAnalysisDomain:
        """Project complete Subject identities from this relation.

        Args: through: Optional exact producer-owned SubjectBinding; Entity projection is implicit.
        Returns: A source member domain or a fixed-only member continuation.
        Example: ``selected_members = relation.members()``.
        Constraints: Requires a retained total Subject map; fixed inputs cannot introduce sources.
        """
        return self._members_domain(through)

    def execute(self) -> MaterializedSelectedCategoryRelation:
        """Evaluate and publish this category selection.

        Args:
            None.
        Returns: A MaterializedSelectedCategoryRelation bound to this exact relation.
        Example: ``result = relation.execute()``.
        Constraints: A source branch reevaluates; a fixed branch uses exact retained Artifacts.
        """
        return MaterializedSelectedCategoryRelation(
            _TOKEN, self._node, self._runtime, dataset=self._run()
        )


class MaterializedSelectedCategoryRelation(_MaterializedValue, _CountRelation):
    """Fixed categorical selection with an exact retained member projection."""

    @property
    def value(self) -> CategoryField:
        """Return this relation's bound categorical field.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        return CategoryField(self._node.root, self._node)

    def where(self, predicate: BoundPredicate) -> LogicalSelectedCategoryRelation:
        """Select rows using a predicate bound to this category relation.

        Args:
            predicate: Closed typed predicate over exact corresponding inputs.
        Returns: A LogicalSelectedCategoryRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: Every referenced input must cover this complete domain; all children are checked.
        """
        return LogicalSelectedCategoryRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    def members(self, *, through: SubjectBinding | None = None) -> LogicalFixedAnalysisDomain:
        """Project selected fixed member identity without a source read.

        Args: through: Optional exact producer-owned SubjectBinding; Entity projection is implicit.
        Returns: A LogicalFixedAnalysisDomain bound to this exact relation.
        Example: ``result = relation.members()``.
        Constraints: Requires a retained total Subject map; fixed inputs cannot introduce sources.
        """
        return LogicalFixedAnalysisDomain(
            _TOKEN, self._subject_members(through), self._runtime, inputs=(self,)
        )


class GroupedNumericRelation(_Value):
    """Logical contribution-coordinate group with original Metric state."""

    def __init__(
        self,
        token: object,
        grouped: Relation,
        runtime: DatasetRuntime,
        *,
        inputs: tuple[_Value, ...],
        row_node: Relation | None = None,
        target_node: Relation | None = None,
    ) -> None:
        super().__init__(token, grouped, runtime, inputs=inputs)
        self._row_node = inputs[0]._node if row_node is None else row_node
        self._target_node = target_node

    def contract(self) -> AnalysisContract:
        """Report the grouped observation's one admitted continuation.

        Args:
            None.
        Returns: An AnalysisContract describing the current shape and valid continuations.
        Example: ``result = relation.contract()``.
        Constraints: This reads local contract metadata and does not execute a source.
        """
        actions = (
            ("execute", "rollup", "summarize")
            if self._node.root.signature.quantity is not None
            else ("summarize",)
        )
        return replace(super().contract(), actions=self._action_contract(actions))

    def execute(self) -> MaterializedGroupedNumericRelation:
        """Publish the contribution-coordinate group with its original state.

        Args:
            None.
        Returns: A MaterializedGroupedNumericRelation bound to this exact relation.
        Example: ``result = relation.execute()``.
        Constraints: A source branch reevaluates; a fixed branch uses exact retained Artifacts.
        """
        if self._node.root.signature.quantity is None:
            raise _reject(
                "an original Metric grouping",
                "current numeric rows",
                "Choose summarize with a row method.",
            )
        return MaterializedGroupedNumericRelation(
            _TOKEN, self._node, self._runtime, dataset=self._run()
        )

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Reduce current rows within the selected complete target keys.

        Args: method: A closed current-row descriptor.
        Returns: A new grouped RowStatistic.
        Example: ``result = relation.group_by(dimension).summarize(mv.mean()).execute()``.
        Constraints: Uses current rows, independently of original Metric state.
        """
        if not isinstance(method, RowMethod):
            raise _reject(
                "a RowMethod", type(method).__name__, "Use a current-row method descriptor."
            )
        node = self._row_node.summarize(
            method.kind, coordinates=self._node.root.signature.domain.instance_key
        )
        if self._target_node is not None:
            node = node.complete_groups(self._target_node)
        return LogicalStatisticRelation(_TOKEN, node, self._runtime, inputs=(self,))

    def rollup(self) -> LogicalRolledNumericRelation:
        """Merge original components into the selected complete groups.

        Args: None.
        Returns: A logical original-state reduction.
        Example: ``result = relation.group_by(dimension).rollup().execute()``.
        Constraints: Requires original state and complete coverage.
        """
        if self._node.root.signature.quantity is None:
            raise _reject(
                "original Metric state",
                "current numeric rows",
                "Choose summarize with a row method.",
            )
        return LogicalRolledNumericRelation(_TOKEN, self._node, self._runtime, inputs=(self,))


class GroupedRatioRelation(_Value):
    """Grouped original ratio components awaiting a target-domain rollup."""

    __slots__ = (
        "_row_node",
        "_target_node",
    )

    def __init__(
        self,
        token: object,
        grouped: Relation,
        runtime: DatasetRuntime,
        *,
        inputs: tuple[_Value, ...],
        row_node: Relation | None = None,
        target_node: Relation | None = None,
    ) -> None:
        super().__init__(token, grouped, runtime, inputs=inputs)
        self._row_node = inputs[0]._node if row_node is None else row_node
        self._target_node = target_node

    def contract(self) -> AnalysisContract:
        """Report the ratio group's one admitted continuation.

        Args:
            None.
        Returns: An AnalysisContract describing the current shape and valid continuations.
        Example: ``result = relation.contract()``.
        Constraints: This reads local contract metadata and does not execute a source.
        """
        return replace(super().contract(), actions=self._action_contract(("rollup", "summarize")))

    def rollup(self) -> LogicalRolledRatioRelation:
        """Merge original numerator and denominator state by the selected coordinate.

        Args:
            None.
        Returns: A LogicalRolledRatioRelation bound to this exact relation.
        Example: ``result = relation.rollup()``.
        Constraints: Requires original retained components; subgroup values are not averaged.
        """
        return LogicalRolledRatioRelation(_TOKEN, self._node, self._runtime, inputs=(self,))

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Reduce current rows within the selected complete target keys.

        Args: method: A closed current-row descriptor.
        Returns: A new grouped RowStatistic.
        Example: ``result = relation.group_by(dimension).summarize(mv.mean()).execute()``.
        Constraints: Uses current rows, independently of original Metric state.
        """
        if not isinstance(method, RowMethod):
            raise _reject(
                "a RowMethod", type(method).__name__, "Use a current-row method descriptor."
            )
        node = self._row_node.summarize(
            method.kind, coordinates=self._node.root.signature.domain.instance_key
        )
        if self._target_node is not None:
            node = node.complete_groups(self._target_node)
        return LogicalStatisticRelation(_TOKEN, node, self._runtime, inputs=(self,))


class LogicalNumericRelation(_NumericComparison):
    """One unexecuted original Metric observation over an Entity domain."""

    def members(
        self, *, through: SubjectBinding | None = None
    ) -> LogicalAnalysisDomain | LogicalFixedAnalysisDomain:
        """Project complete Subject identities from this relation.

        Args: through: Optional exact producer-owned SubjectBinding; Entity projection is implicit.
        Returns: A source member domain or a fixed-only member continuation.
        Example: ``selected_members = relation.members()``.
        Constraints: Requires a retained total Subject map; fixed inputs cannot introduce sources.
        """
        return self._members_domain(through)

    @property
    def value(self) -> NumericField:
        """Return the bound numeric field for an admitted strict predicate.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        return NumericField(self._node.root, self._node)

    def where(self, predicate: BoundPredicate) -> LogicalSelectedNumericRelation:
        """Select current numeric values using their bound predicate.

        Args: predicate: A comparison from this or an exactly corresponding numeric relation.value.
        Returns: A logical numeric selection preserving any retained Subject part.
        Example: ``selected = relation.where(relation.value.gt(0))``.
        Constraints: Numeric dependencies require exact corresponding keys; non-Defined predicates reject.
        """
        return LogicalSelectedNumericRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    def group_by(
        self,
        *dimensions: Ref[DimensionKind]
        | Ref[EntityKind]
        | LogicalCategoryRelation
        | MaterializedCategoryRelation
        | LogicalSelectedCategoryRelation
        | MaterializedSelectedCategoryRelation
        | TimeGrid
        | Grain,
        groups: LogicalAnalysisDomain
        | GroupedAnalysisDomain
        | MaterializedAnalysisDomain
        | None = None,
    ) -> GroupedNumericRelation:
        """Select complete retained axes or explicit classifications for reduction.

        Args:
            dimensions: Retained Entity/Dimension axes or corresponding categorical reads.
            groups: Optional typed target domain preserving empty groups.
        Returns: A GroupedNumericRelation bound to this exact relation.
        Example: ``result = relation.group_by(dimension, groups=targets)``.
        Constraints: Classifications use complete keys; fixed inputs require fixed categories and targets.
        """
        grouped, rows = self._group_nodes(dimensions)
        if groups is not None:
            grouped = grouped.complete_groups(groups._node)
        return GroupedNumericRelation(
            _TOKEN,
            grouped,
            self._runtime,
            inputs=(self,),
            row_node=rows,
            target_node=None if groups is None else groups._node,
        )

    def rollup(self) -> LogicalRolledNumericRelation:
        """Merge original Metric components into a Singleton result.

        Args:
            None.
        Returns: A LogicalRolledNumericRelation bound to this exact relation.
        Example: ``result = relation.rollup()``.
        Constraints: Requires original retained components; subgroup values are not averaged.
        Count merges retained occurrence counts. Missing physical support reports the exact key
        and admitted physical profile candidates; execution never changes routes automatically.
        PostgreSQL Count rejects nested categorical state; overall, Entity and time-only groups remain supported.
        """
        return LogicalRolledNumericRelation(
            _TOKEN, self._node.rollup(), self._runtime, inputs=(self,)
        )

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Construct a new current-row sum, count or equal-row mean.

        Args:
            method: Closed current-row statistic: mv.sum(), mv.count(), mv.count_defined(), mv.min(), mv.max(), or mv.mean().
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        return self._summarize_rows(method)

    def execute(self) -> MaterializedNumericRelation:
        """Evaluate and publish this exact numeric relation.

        Args:
            None.
        Returns: A MaterializedNumericRelation bound to this exact relation.
        Example: ``result = relation.execute()``.
        Constraints: A source branch reevaluates; a fixed branch uses exact retained Artifacts.
        """
        return MaterializedNumericRelation(_TOKEN, self._node, self._runtime, dataset=self._run())


class MaterializedNumericRelation(_MaterializedValue, _NumericComparison):
    """Exact fixed original Metric observation with retained components."""

    def members(self, *, through: SubjectBinding | None = None) -> LogicalFixedAnalysisDomain:
        """Project retained complete Subject identities without source access.

        Args: through: Optional exact producer-owned SubjectBinding; Entity projection is implicit.
        Returns: A fixed-only logical member continuation.
        Example: ``members = result.members()``.
        Constraints: Requires the exact retained Subject part and cannot read external attributes.
        """
        return LogicalFixedAnalysisDomain(
            _TOKEN, self._subject_members(through), self._runtime, inputs=(self,)
        )

    @property
    def value(self) -> NumericField:
        """Return the bound numeric field for a fixed-only predicate.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        return NumericField(self._node.root, self._node)

    def where(self, predicate: BoundPredicate) -> LogicalSelectedNumericRelation:
        """Select current numeric values using their bound predicate.

        Args: predicate: A comparison from this or an exactly corresponding numeric relation.value.
        Returns: A logical numeric selection preserving any retained Subject part.
        Example: ``selected = relation.where(relation.value.gt(0))``.
        Constraints: Numeric dependencies require exact corresponding keys; non-Defined predicates reject.
        """
        return LogicalSelectedNumericRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    def group_by(
        self,
        *dimensions: Ref[DimensionKind]
        | Ref[EntityKind]
        | LogicalCategoryRelation
        | MaterializedCategoryRelation
        | LogicalSelectedCategoryRelation
        | MaterializedSelectedCategoryRelation
        | TimeGrid
        | Grain,
        groups: LogicalAnalysisDomain
        | GroupedAnalysisDomain
        | MaterializedAnalysisDomain
        | None = None,
    ) -> GroupedNumericRelation:
        """Group retained fixed axes with fixed classifications and targets.

        Args:
            dimensions: Retained Entity/Dimension axes or corresponding categorical reads.
            groups: Optional typed target domain preserving empty groups.
        Returns: A GroupedNumericRelation bound to this exact relation.
        Example: ``result = relation.group_by(dimension, groups=targets)``.
        Constraints: Classifications use complete keys; fixed inputs require fixed categories and targets.
        """
        grouped, rows = self._group_nodes(dimensions)
        if groups is not None:
            grouped = grouped.complete_groups(groups._node)
        return GroupedNumericRelation(
            _TOKEN,
            grouped,
            self._runtime,
            inputs=(self,),
            row_node=rows,
            target_node=None if groups is None else groups._node,
        )

    def rollup(self) -> LogicalRolledNumericRelation:
        """Build fixed-only original-state rollup.

        Args:
            None.
        Returns: A LogicalRolledNumericRelation bound to this exact relation.
        Example: ``result = relation.rollup()``.
        Constraints: Requires original retained components; subgroup values are not averaged.
        """
        return LogicalRolledNumericRelation(
            _TOKEN, self._node.rollup(), self._runtime, inputs=(self,)
        )

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Build fixed-only current-row statistic.

        Args:
            method: Closed current-row statistic: mv.sum(), mv.count(), mv.count_defined(), mv.min(), mv.max(), or mv.mean().
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        return self._summarize_rows(method)


class MaterializedGroupedNumericRelation(MaterializedNumericRelation):
    """Fixed member or contribution group with retained Metric components."""

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Build a current-row statistic over this exact group.

        Args:
            method: Closed current-row statistic: mv.sum(), mv.count(), mv.count_defined(), mv.min(), mv.max(), or mv.mean().
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Preserves every retained group axis and counts current materialized rows, not their original contributions.
        """
        if not isinstance(method, RowMethod):
            raise _reject(
                "mv.sum/count/count_defined/min/max/mean()",
                type(method).__name__,
                "Select a closed row method.",
            )
        return LogicalStatisticRelation(
            _TOKEN,
            self._node.summarize(
                method.kind, coordinates=self._node.root.signature.domain.instance_key
            ),
            self._runtime,
            inputs=(self,),
        )


class LogicalRolledNumericRelation(_OriginalContinuation):
    """Unexecuted Singleton observation derived from original Metric state."""

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Calculate a statistic over the current Singleton row.

        Args:
            method: Closed current-row statistic: mv.sum(), mv.count(), mv.count_defined(), mv.min(), mv.max(), or mv.mean().
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        return self._summarize_rows(method)

    def execute(self) -> MaterializedRolledNumericRelation | MaterializedGroupedNumericRelation:
        """Evaluate and publish this original-state group or Singleton.

        Args:
            None.
        Returns: A MaterializedGroupedNumericRelation for retained group keys, otherwise a MaterializedRolledNumericRelation.
        Example: ``result = relation.execute()``.
        Constraints: A source branch reevaluates; a fixed branch uses exact retained Artifacts.
        """
        result = (
            MaterializedGroupedNumericRelation
            if self._node.root.signature.domain.kind == "group"
            else MaterializedRolledNumericRelation
        )
        return result(_TOKEN, self._node, self._runtime, dataset=self._run())


class MaterializedRolledNumericRelation(_MaterializedValue, _OriginalContinuation):
    """Fixed original-state Singleton observation."""

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Build a fixed-only statistic over this Singleton row.

        Args:
            method: Closed current-row statistic: mv.sum(), mv.count(), mv.count_defined(), mv.min(), mv.max(), or mv.mean().
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        return self._summarize_rows(method)


class LogicalRatioRelation(_NumericComparison):
    """Unexecuted ratio observation with its original component state."""

    def group_by(
        self,
        *dimensions: Ref[DimensionKind]
        | Ref[EntityKind]
        | LogicalCategoryRelation
        | MaterializedCategoryRelation
        | LogicalSelectedCategoryRelation
        | MaterializedSelectedCategoryRelation
        | TimeGrid
        | Grain,
        groups: LogicalAnalysisDomain
        | GroupedAnalysisDomain
        | MaterializedAnalysisDomain
        | None = None,
    ) -> GroupedRatioRelation:
        """Select complete retained axes for original ratio reduction.

        Args:
            dimensions: Retained Entity/Dimension axes or corresponding categorical reads.
            groups: Optional typed target domain preserving empty groups.
        Returns: A GroupedRatioRelation bound to this exact relation.
        Example: ``result = relation.group_by(dimension, groups=targets)``.
        Constraints: Classifications use complete keys; fixed inputs require fixed categories and targets.
        """
        grouped, rows = self._group_nodes(dimensions)
        if groups is not None:
            grouped = grouped.complete_groups(groups._node)
        return GroupedRatioRelation(
            _TOKEN,
            grouped,
            self._runtime,
            inputs=(self,),
            row_node=rows,
            target_node=None if groups is None else groups._node,
        )

    def rollup(self) -> LogicalRolledRatioRelation:
        """Reobserve the target domain from the original ratio components.

        Args:
            None.
        Returns: A LogicalRolledRatioRelation bound to this exact relation.
        Example: ``result = relation.rollup()``.
        Constraints: Requires original retained components; subgroup values are not averaged.
        """
        return LogicalRolledRatioRelation(
            _TOKEN, self._node.rollup(), self._runtime, inputs=(self,)
        )

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Calculate a statistic over current ratio rows.

        Args:
            method: Closed current-row statistic: mv.sum(), mv.count(), mv.count_defined(), mv.min(), mv.max(), or mv.mean().
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        return self._summarize_rows(method)

    def execute(self) -> MaterializedRatioRelation:
        """Evaluate and publish this ratio observation.

        Args:
            None.
        Returns: A MaterializedRatioRelation bound to this exact relation.
        Example: ``result = relation.execute()``.
        Constraints: A source branch reevaluates; a fixed branch uses exact retained Artifacts.
        """
        return MaterializedRatioRelation(_TOKEN, self._node, self._runtime, dataset=self._run())


class MaterializedRatioRelation(_MaterializedValue, _NumericComparison):
    """Fixed ratio observation with retained numerator and denominator parts."""

    def group_by(
        self,
        *dimensions: Ref[DimensionKind]
        | Ref[EntityKind]
        | LogicalCategoryRelation
        | MaterializedCategoryRelation
        | LogicalSelectedCategoryRelation
        | MaterializedSelectedCategoryRelation
        | TimeGrid
        | Grain,
        groups: LogicalAnalysisDomain
        | GroupedAnalysisDomain
        | MaterializedAnalysisDomain
        | None = None,
    ) -> GroupedRatioRelation:
        """Group complete fixed coordinates for original ratio reduction.

        Args:
            dimensions: Retained Entity/Dimension axes or corresponding categorical reads.
            groups: Optional typed target domain preserving empty groups.
        Returns: A GroupedRatioRelation bound to this exact relation.
        Example: ``result = relation.group_by(dimension, groups=targets)``.
        Constraints: Classifications use complete keys; fixed inputs require fixed categories and targets.
        """
        grouped, rows = self._group_nodes(dimensions)
        if groups is not None:
            grouped = grouped.complete_groups(groups._node)
        return GroupedRatioRelation(
            _TOKEN,
            grouped,
            self._runtime,
            inputs=(self,),
            row_node=rows,
            target_node=None if groups is None else groups._node,
        )

    def rollup(self) -> LogicalRolledRatioRelation:
        """Build a fixed-only original-component rollup.

        Args:
            None.
        Returns: A LogicalRolledRatioRelation bound to this exact relation.
        Example: ``result = relation.rollup()``.
        Constraints: Requires original retained components; subgroup values are not averaged.
        """
        return LogicalRolledRatioRelation(
            _TOKEN, self._node.rollup(), self._runtime, inputs=(self,)
        )

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Build a fixed-only current-row statistic.

        Args:
            method: Closed current-row statistic: mv.sum(), mv.count(), mv.count_defined(), mv.min(), mv.max(), or mv.mean().
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        return self._summarize_rows(method)


class LogicalRolledRatioRelation(_OriginalContinuation):
    """Unexecuted ratio reobserved from original numerator and denominator state."""

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Calculate a statistic over current rolled ratio rows.

        Args:
            method: Closed current-row statistic: mv.sum(), mv.count(), mv.count_defined(), mv.min(), mv.max(), or mv.mean().
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        return self._summarize_rows(method)

    def execute(self) -> MaterializedRolledRatioRelation:
        """Evaluate and publish this original-component ratio rollup.

        Args:
            None.
        Returns: A MaterializedRolledRatioRelation bound to this exact relation.
        Example: ``result = relation.execute()``.
        Constraints: A source branch reevaluates; a fixed branch uses exact retained Artifacts.
        """
        return MaterializedRolledRatioRelation(
            _TOKEN, self._node, self._runtime, dataset=self._run()
        )


class MaterializedRolledRatioRelation(_MaterializedValue, _OriginalContinuation):
    """Fixed ratio rollup with retained original component meaning."""

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Build a fixed-only statistic over current rolled ratio rows.

        Args:
            method: Closed current-row statistic: mv.sum(), mv.count(), mv.count_defined(), mv.min(), mv.max(), or mv.mean().
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        return self._summarize_rows(method)


class LogicalDifferenceRelation(_NumericComparison):
    """Unexecuted exact same-member absolute Difference."""

    @property
    def value(self) -> NumericField:
        """Return the Difference-bound strict numeric field.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        return NumericField(self._node.root, self._node)

    def attribute(
        self,
        *,
        axes: tuple[Ref[DimensionKind] | CategoryRelation, ...],
        mode: Literal["joint", "hierarchy"] = "joint",
        top_k: int | None = None,
    ) -> LogicalAttributionResult:
        """Allocate an absolute change from its complete original endpoint states.

        Args: axes: Unique ordered Dimensions or corresponding classifications selecting retained roles; mode: Joint tuples or authored prefixes; top_k: Common basis limit 1..1000, or None.
        Returns: A LogicalAttributionResult with same-key contribution/current/baseline views.
        Example: ``result = change.attribute(axes=(channel,), mode="joint", top_k=5).execute()``.
        Constraints: Fixed inputs require retained axes; every resolution independently reconciles.
        """
        from marivo.analysis.materialization.graph_attribution import bind

        return LogicalAttributionResult(
            _TOKEN,
            bind(self._node, self._attribution_axes(axes), mode, top_k),
            self._runtime,
            inputs=(self,),
        )

    def where(self, predicate: BoundPredicate) -> LogicalSelectedDifferenceRelation:
        """Select Defined Difference rows through this exact field.

        Args:
            predicate: Closed typed predicate over exact corresponding inputs.
        Returns: A LogicalSelectedDifferenceRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: Every referenced input must cover this complete domain; all children are checked.
        """
        return LogicalSelectedDifferenceRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Calculate a statistic over current Difference rows.

        Args:
            method: Closed current-row statistic: mv.sum(), mv.count(), mv.count_defined(), mv.min(), mv.max(), or mv.mean().
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        return self._summarize_rows(method)

    def execute(self) -> MaterializedDifferenceRelation:
        """Evaluate and publish the exact Difference.

        Args:
            None.
        Returns: A MaterializedDifferenceRelation bound to this exact relation.
        Example: ``result = relation.execute()``.
        Constraints: A source branch reevaluates; a fixed branch uses exact retained Artifacts.
        """
        return MaterializedDifferenceRelation(
            _TOKEN, self._node, self._runtime, dataset=self._run()
        )


class MaterializedDifferenceRelation(_MaterializedValue, _NumericComparison):
    """Fixed exact Difference with retained paired endpoints."""

    @property
    def value(self) -> NumericField:
        """Return the bound strict numeric field.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        return NumericField(self._node.root, self._node)

    def attribute(
        self,
        *,
        axes: tuple[Ref[DimensionKind] | CategoryRelation, ...],
        mode: Literal["joint", "hierarchy"] = "joint",
        top_k: int | None = None,
    ) -> LogicalAttributionResult:
        """Allocate an absolute change from its complete original endpoint states.

        Args: axes: Unique ordered Dimensions or corresponding classifications selecting retained roles; mode: Joint tuples or authored prefixes; top_k: Common basis limit 1..1000, or None.
        Returns: A LogicalAttributionResult with same-key contribution/current/baseline views.
        Example: ``result = change.attribute(axes=(channel,), mode="joint", top_k=5).execute()``.
        Constraints: Fixed inputs require retained axes; every resolution independently reconciles.
        """
        from marivo.analysis.materialization.graph_attribution import bind

        return LogicalAttributionResult(
            _TOKEN,
            bind(self._node, self._attribution_axes(axes), mode, top_k),
            self._runtime,
            inputs=(self,),
        )

    def where(self, predicate: BoundPredicate) -> LogicalSelectedDifferenceRelation:
        """Build a fixed-only Difference selection.

        Args:
            predicate: Closed typed predicate over exact corresponding inputs.
        Returns: A LogicalSelectedDifferenceRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: Every referenced input must cover this complete domain; all children are checked.
        """
        return LogicalSelectedDifferenceRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Build a fixed-only current-row statistic.

        Args:
            method: Closed current-row statistic: mv.sum(), mv.count(), mv.count_defined(), mv.min(), mv.max(), or mv.mean().
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        return self._summarize_rows(method)


class LogicalSelectedDifferenceRelation(_NumericComparison):
    """Unexecuted selected Difference with admitted member projection."""

    @property
    def value(self) -> NumericField:
        """Return the Difference-bound strict numeric field.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        return NumericField(self._node.root, self._node)

    def where(self, predicate: BoundPredicate) -> LogicalSelectedDifferenceRelation:
        """Select Defined Difference rows through this exact field.

        Args:
            predicate: Closed typed predicate over exact corresponding inputs.
        Returns: A LogicalSelectedDifferenceRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: Every referenced input must cover this complete domain; all children are checked.
        """
        return LogicalSelectedDifferenceRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    def members(
        self, *, through: SubjectBinding | None = None
    ) -> LogicalAnalysisDomain | LogicalFixedAnalysisDomain:
        """Project complete Subject identities from this relation.

        Args: through: Optional exact producer-owned SubjectBinding; Entity projection is implicit.
        Returns: A source member domain or a fixed-only member continuation.
        Example: ``selected_members = relation.members()``.
        Constraints: Requires a retained total Subject map; fixed inputs cannot introduce sources.
        """
        return self._members_domain(through)

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Calculate a statistic over selected current rows.

        Args:
            method: Closed current-row statistic: mv.sum(), mv.count(), mv.count_defined(), mv.min(), mv.max(), or mv.mean().
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        return self._summarize_rows(method)

    def execute(self) -> MaterializedSelectedDifferenceRelation:
        """Evaluate and publish the selected Difference.

        Args:
            None.
        Returns: A MaterializedSelectedDifferenceRelation bound to this exact relation.
        Example: ``result = relation.execute()``.
        Constraints: A source branch reevaluates; a fixed branch uses exact retained Artifacts.
        """
        return MaterializedSelectedDifferenceRelation(
            _TOKEN, self._node, self._runtime, dataset=self._run()
        )


class MaterializedSelectedDifferenceRelation(_MaterializedValue, _NumericComparison):
    """Fixed selected Difference with exact member projection."""

    @property
    def value(self) -> NumericField:
        """Return the Difference-bound strict numeric field.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        return NumericField(self._node.root, self._node)

    def where(self, predicate: BoundPredicate) -> LogicalSelectedDifferenceRelation:
        """Select Defined Difference rows through this exact field.

        Args:
            predicate: Closed typed predicate over exact corresponding inputs.
        Returns: A LogicalSelectedDifferenceRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: Every referenced input must cover this complete domain; all children are checked.
        """
        return LogicalSelectedDifferenceRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    def members(self, *, through: SubjectBinding | None = None) -> LogicalFixedAnalysisDomain:
        """Project fixed selected members without source access.

        Args: through: Optional exact producer-owned SubjectBinding; Entity projection is implicit.
        Returns: A LogicalFixedAnalysisDomain bound to this exact relation.
        Example: ``result = relation.members()``.
        Constraints: Requires a retained total Subject map; fixed inputs cannot introduce sources.
        """
        return LogicalFixedAnalysisDomain(
            _TOKEN, self._subject_members(through), self._runtime, inputs=(self,)
        )

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Build a fixed-only statistic over selected rows.

        Args:
            method: Closed current-row statistic: mv.sum(), mv.count(), mv.count_defined(), mv.min(), mv.max(), or mv.mean().
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        return self._summarize_rows(method)


class GroupedStatisticRelation(_Value):
    """A retained row-statistic grouping awaiting its state merge."""

    def rollup(self) -> LogicalStatisticRelation:
        """Finish the selected merge of this statistic's states.

        Args: None.
        Returns: A logical RowStatistic with the same contribution identity.
        Example: ``result = statistic.group_by(dimension).rollup().execute()``.
        Constraints: Removed coordinates and missing state cannot be recovered.
        """
        return LogicalStatisticRelation(_TOKEN, self._node, self._runtime, inputs=(self,))

    def contract(self) -> AnalysisContract:
        """Report the pending statistic state merge.

        Args: None.
        Returns: The current bounded continuation contract.
        Example: ``statistic.group_by(dimension).contract().show()``.
        Constraints: Does not execute or open a source.
        """
        return replace(super().contract(), actions=self._action_contract(("rollup",)))


class _StatisticContinuation(_NumericComparison):
    def group_by(
        self,
        *keys: Ref[DimensionKind] | Ref[EntityKind] | CategoryRelation,
        groups: GroupedAnalysisDomain | MaterializedAnalysisDomain | None = None,
    ) -> GroupedStatisticRelation:
        """Select retained axes for a partial row-state merge.

        Args:
            keys: Retained complete coordinates or corresponding classifications selecting roles.
            groups: Optional explicit target including valid empty groups.
        Returns: A grouped statistic awaiting rollup.
        Example: ``result = statistic.group_by(dimension).rollup().execute()``.
        Constraints: Merges this statistic's state; does not summarize finished values.
        Execute source Duration statistics before selecting merge axes.
        """
        node = self._node.rollup_statistic(
            *(key if isinstance(key, Ref) else self._classification_key(key) for key in keys)
        )
        if groups is not None:
            node = node.complete_groups(groups._node)
        return GroupedStatisticRelation(_TOKEN, node, self._runtime, inputs=(self,))


class LogicalStatisticRelation(_StatisticContinuation):
    """Unexecuted row statistic with retained-state merge continuations."""

    def rollup(self) -> LogicalStatisticRelation:
        """Merge this statistic's retained states, then finish once.

        Args: None.
        Returns: A logical statistic preserving its row-contribution identity.
        Example: ``total = statistic.rollup().execute()``.
        Constraints: Requires the exact row state; never averages finished subgroup means.
        Execute source Duration statistics before merging their retained states.
        """
        return LogicalStatisticRelation(
            _TOKEN, self._node.rollup_statistic(), self._runtime, inputs=(self,)
        )

    def execute(self) -> MaterializedStatisticRelation:
        """Evaluate and publish this current-row statistic.

        Args:
            None.
        Returns: A MaterializedStatisticRelation bound to this exact relation.
        Example: ``result = relation.execute()``.
        Constraints: A source branch reevaluates; a fixed branch uses exact retained Artifacts.
        """
        return MaterializedStatisticRelation(_TOKEN, self._node, self._runtime, dataset=self._run())


class MaterializedStatisticRelation(_MaterializedValue, _StatisticContinuation):
    """Fixed current-row statistic with retained-state merge continuations."""

    def rollup(self) -> LogicalStatisticRelation:
        """Merge this statistic's retained states, then finish once.

        Args: None.
        Returns: A logical statistic preserving its row-contribution identity.
        Example: ``total = statistic.rollup().execute()``.
        Constraints: Requires the exact row state; never averages finished subgroup means.
        """
        return LogicalStatisticRelation(
            _TOKEN, self._node.rollup_statistic(), self._runtime, inputs=(self,)
        )


class LogicalCoefficientRelation(_Value):
    """Unexecuted coefficient projection over the original search authority."""

    def execute(self) -> MaterializedCoefficientRelation:
        """Publish this coefficient projection.

        Args: None.
        Returns: A MaterializedCoefficientRelation.
        Example: ``fixed = associations.coefficient.execute()``.
        Constraints: Preserves the complete original search scope and findings.
        """
        return MaterializedCoefficientRelation(
            _TOKEN, self._node, self._runtime, dataset=self._run()
        )

    @property
    def value(self) -> NumericField:
        """Bind coefficient predicates.

        Args: None.
        Returns: The owned numeric field.
        Example: ``condition = coefficients.value.is_defined()``.
        Constraints: No original Entity membership or rollup authority.
        """
        return NumericField(self._node.root, self._node)

    def where(self, predicate: BoundPredicate) -> LogicalCoefficientSelectionRelation:
        """Select current coefficient rows.

        Args: predicate: An exactly bound predicate.
        Returns: A LogicalCoefficientSelectionRelation.
        Example: ``chosen = coefficients.where(coefficients.value.is_defined())``.
        Constraints: Does not recompute coefficients or selected lags.
        """
        return LogicalCoefficientSelectionRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Describe current coefficient rows.

        Args: method: A closed row statistic factory value.
        Returns: A LogicalStatisticRelation.
        Example: ``summary = coefficients.summarize(mv.mean())``.
        Constraints: This is not a pooled correlation.
        """
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )

    def rank(
        self,
        *,
        order: Literal["ascending", "descending"],
        ties: Literal["ordinal", "dense", "min", "max"],
        partition_by: tuple[CategoryRelation, ...] = (),
    ) -> LogicalRankingResult:
        """Rank current coefficient rows without changing search authority.

        Args: order: ascending or descending. ties: ordinal, dense, min or max. partition_by: Explicit corresponding categories.
        Returns: A LogicalRankingResult with values and ranks.
        Example: ``ranked = coefficients.rank(order="descending", ties="ordinal")``.
        Constraints: No pooling or lag reselection.
        """
        return _rank_relation(self, order, ties, partition_by)


class MaterializedCoefficientRelation(_MaterializedValue):
    """Coefficient view bound to an exact retained Association."""

    @property
    def value(self) -> NumericField:
        """Return the bound coefficient field.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        return NumericField(self._node.root, self._node)

    def where(self, predicate: BoundPredicate) -> LogicalCoefficientSelectionRelation:
        """Select a Defined coefficient on this Association.

        Args:
            predicate: Closed typed predicate over exact corresponding inputs.
        Returns: A LogicalCoefficientSelectionRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: Every referenced input must cover this complete domain; all children are checked.
        """
        return LogicalCoefficientSelectionRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Calculate a statistic over current coefficient rows.

        Args:
            method: Closed current-row statistic: mv.sum(), mv.count(), mv.count_defined(), mv.min(), mv.max(), or mv.mean().
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        return self._summarize_rows(method)

    def rank(
        self,
        *,
        order: Literal["ascending", "descending"],
        ties: Literal["ordinal", "dense", "min", "max"],
        partition_by: tuple[CategoryRelation, ...] = (),
    ) -> LogicalRankingResult:
        """Rank current coefficient rows without changing search authority.

        Args: order: ascending or descending. ties: ordinal, dense, min or max. partition_by: Explicit corresponding categories.
        Returns: A LogicalRankingResult with values and ranks.
        Example: ``ranked = coefficients.rank(order="descending", ties="ordinal")``.
        Constraints: No pooling or lag reselection.
        """
        return _rank_relation(self, order, ties, partition_by)


class LogicalCoefficientSelectionRelation(_Value):
    """Unexecuted strict coefficient selection."""

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Calculate a statistic over the selected coefficient.

        Args:
            method: Closed current-row statistic: mv.sum(), mv.count(), mv.count_defined(), mv.min(), mv.max(), or mv.mean().
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        return self._summarize_rows(method)

    def execute(self) -> MaterializedCoefficientSelectionRelation:
        """Evaluate and publish the coefficient selection.

        Args:
            None.
        Returns: A MaterializedCoefficientSelectionRelation bound to this exact relation.
        Example: ``result = relation.execute()``.
        Constraints: A source branch reevaluates; a fixed branch uses exact retained Artifacts.
        """
        return MaterializedCoefficientSelectionRelation(
            _TOKEN, self._node, self._runtime, dataset=self._run()
        )

    def rank(
        self,
        *,
        order: Literal["ascending", "descending"],
        ties: Literal["ordinal", "dense", "min", "max"],
        partition_by: tuple[CategoryRelation, ...] = (),
    ) -> LogicalRankingResult:
        """Rank current coefficient rows without changing search authority.

        Args: order: ascending or descending. ties: ordinal, dense, min or max. partition_by: Explicit corresponding categories.
        Returns: A LogicalRankingResult with values and ranks.
        Example: ``ranked = coefficients.rank(order="descending", ties="ordinal")``.
        Constraints: No pooling or lag reselection.
        """
        return _rank_relation(self, order, ties, partition_by)


class MaterializedCoefficientSelectionRelation(_MaterializedValue):
    """Fixed strict coefficient selection."""

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Build a fixed-only statistic over the selected coefficient.

        Args:
            method: Closed current-row statistic: mv.sum(), mv.count(), mv.count_defined(), mv.min(), mv.max(), or mv.mean().
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        return self._summarize_rows(method)

    def rank(
        self,
        *,
        order: Literal["ascending", "descending"],
        ties: Literal["ordinal", "dense", "min", "max"],
        partition_by: tuple[CategoryRelation, ...] = (),
    ) -> LogicalRankingResult:
        """Rank current coefficient rows without changing search authority.

        Args: order: ascending or descending. ties: ordinal, dense, min or max. partition_by: Explicit corresponding categories.
        Returns: A LogicalRankingResult with values and ranks.
        Example: ``ranked = coefficients.rank(order="descending", ties="ordinal")``.
        Constraints: No pooling or lag reselection.
        """
        return _rank_relation(self, order, ties, partition_by)


class LogicalTimeRunResult(_Value):
    """Immutable maximal intervals with retained full-grid condition scope."""

    def execute(self) -> MaterializedTimeRunResult:
        """Execute and publish the complete-grid run capture.

        Args: None.
        Returns: The verified MaterializedTimeRunResult.
        Example: ``fixed = segments.execute()``.
        Constraints: Publication is atomic; source and fixed dependencies cannot mix.
        """
        return MaterializedTimeRunResult(_TOKEN, self._node, self._runtime, dataset=self._run())

    def where(self, predicate: BoundPredicate) -> LogicalTimeRunResult:
        """Select intervals without changing segmentation or original scope.

        Args: predicate: A condition on an owned interval field.
        Returns: A LogicalTimeRunResult with synchronized selected views.
        Example: ``selected = segments.where(segments.count.value.gt(1))``.
        Constraints: Ordinary where requires Defined consumed Cells.
        """
        return LogicalTimeRunResult(_TOKEN, self._select(predicate), self._runtime, inputs=(self,))

    @property
    def start(self) -> LogicalTemporalRelation:
        """Read the owned start interval field.

        Args: None.
        Returns: A LogicalTemporalRelation over the current interval keys.
        Example: ``values = segments.start``.
        Constraints: Preserves original segmentation and complete classification scope.
        """
        return LogicalTemporalRelation(
            _TOKEN, self._node.run_field("start"), self._runtime, inputs=(self,)
        )

    @property
    def end(self) -> LogicalTemporalRelation:
        """Read the owned end interval field.

        Args: None.
        Returns: A LogicalTemporalRelation over the current interval keys.
        Example: ``values = segments.end``.
        Constraints: Preserves original segmentation and complete classification scope.
        """
        return LogicalTemporalRelation(
            _TOKEN, self._node.run_field("end"), self._runtime, inputs=(self,)
        )

    @property
    def count(self) -> LogicalNumericRelation:
        """Read the owned count interval field.

        Args: None.
        Returns: A LogicalNumericRelation over the current interval keys.
        Example: ``values = segments.count``.
        Constraints: Preserves original segmentation and complete classification scope.
        """
        return LogicalNumericRelation(
            _TOKEN, self._node.run_field("count"), self._runtime, inputs=(self,)
        )

    @property
    def duration(self) -> LogicalNumericRelation:
        """Read the owned duration interval field.

        Args: None.
        Returns: A LogicalNumericRelation over the current interval keys.
        Example: ``values = segments.duration``.
        Constraints: Preserves original segmentation and complete classification scope.
        """
        return LogicalNumericRelation(
            _TOKEN, self._node.run_field("duration"), self._runtime, inputs=(self,)
        )


class MaterializedTimeRunResult(_MaterializedValue):
    """Immutable maximal intervals with retained full-grid condition scope."""

    def where(self, predicate: BoundPredicate) -> LogicalTimeRunResult:
        """Select intervals without changing segmentation or original scope.

        Args: predicate: A condition on an owned interval field.
        Returns: A LogicalTimeRunResult with synchronized selected views.
        Example: ``selected = segments.where(segments.count.value.gt(1))``.
        Constraints: Ordinary where requires Defined consumed Cells.
        """
        return LogicalTimeRunResult(_TOKEN, self._select(predicate), self._runtime, inputs=(self,))

    @property
    def start(self) -> MaterializedTemporalRelation:
        """Read the owned start interval field.

        Args: None.
        Returns: A MaterializedTemporalRelation over the current interval keys.
        Example: ``values = segments.start``.
        Constraints: Preserves original segmentation and complete classification scope.
        """
        assert self._dataset is not None
        return MaterializedTemporalRelation(
            _TOKEN,
            self._node.run_field("start"),
            self._runtime,
            dataset=replace(self._dataset, projection="start"),
        )

    @property
    def end(self) -> MaterializedTemporalRelation:
        """Read the owned end interval field.

        Args: None.
        Returns: A MaterializedTemporalRelation over the current interval keys.
        Example: ``values = segments.end``.
        Constraints: Preserves original segmentation and complete classification scope.
        """
        assert self._dataset is not None
        return MaterializedTemporalRelation(
            _TOKEN,
            self._node.run_field("end"),
            self._runtime,
            dataset=replace(self._dataset, projection="end"),
        )

    @property
    def count(self) -> MaterializedNumericRelation:
        """Read the owned count interval field.

        Args: None.
        Returns: A MaterializedNumericRelation over the current interval keys.
        Example: ``values = segments.count``.
        Constraints: Preserves original segmentation and complete classification scope.
        """
        assert self._dataset is not None
        return MaterializedNumericRelation(
            _TOKEN,
            self._node.run_field("count"),
            self._runtime,
            dataset=replace(self._dataset, projection="count"),
        )

    @property
    def duration(self) -> MaterializedNumericRelation:
        """Read the owned duration interval field.

        Args: None.
        Returns: A MaterializedNumericRelation over the current interval keys.
        Example: ``values = segments.duration``.
        Constraints: Preserves original segmentation and complete classification scope.
        """
        assert self._dataset is not None
        return MaterializedNumericRelation(
            _TOKEN,
            self._node.run_field("duration"),
            self._runtime,
            dataset=replace(self._dataset, projection="duration"),
        )


class LogicalDeviationResult(_Value):
    """Unexecuted immutable deviation fit with four corresponding owned fields."""

    def execute(self) -> MaterializedDeviationResult:
        """Execute one deviation DAG and atomically publish its fit and fields.

        Args: None.
        Returns: The checked MaterializedDeviationResult.
        Example: ``fixed = scored.execute()``.
        Constraints: Source inputs obtain a new realization; fixed inputs use retained data only.
        """
        return MaterializedDeviationResult(_TOKEN, self._node, self._runtime, dataset=self._run())

    def where(self, predicate: BoundPredicate) -> LogicalDeviationResult:
        """Select all four fields while retaining the original fitted scope.

        Args: predicate: A predicate bound to an owned or exactly corresponding field.
        Returns: A LogicalDeviationResult selecting current output rows.
        Example: ``defined = scored.where(scored.score.value.is_defined())``.
        Constraints: Ordinary numeric predicates require Defined Cells; selection never refits.
        """
        return LogicalDeviationResult(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    @property
    def observed(self) -> LogicalNumericRelation:
        """Read the owned observed numeric field of this fit.

        Args: None.
        Returns: A LogicalNumericRelation over the current result keys.
        Example: ``values = scored.observed``.
        Constraints: Uses the same fitted producer and original fit scope.
        """
        return LogicalNumericRelation(
            _TOKEN, self._node.deviation_field("observed"), self._runtime, inputs=(self,)
        )

    @property
    def reference(self) -> LogicalNumericRelation:
        """Read the owned reference numeric field of this fit.

        Args: None.
        Returns: A LogicalNumericRelation over the current result keys.
        Example: ``values = scored.reference``.
        Constraints: Uses the same fitted producer and original fit scope.
        """
        return LogicalNumericRelation(
            _TOKEN, self._node.deviation_field("reference"), self._runtime, inputs=(self,)
        )

    @property
    def deviation(self) -> LogicalNumericRelation:
        """Read the owned deviation numeric field of this fit.

        Args: None.
        Returns: A LogicalNumericRelation over the current result keys.
        Example: ``values = scored.deviation``.
        Constraints: Uses the same fitted producer and original fit scope.
        """
        return LogicalNumericRelation(
            _TOKEN, self._node.deviation_field("deviation"), self._runtime, inputs=(self,)
        )

    @property
    def score(self) -> LogicalNumericRelation:
        """Read the owned score numeric field of this fit.

        Args: None.
        Returns: A LogicalNumericRelation over the current result keys.
        Example: ``values = scored.score``.
        Constraints: Uses the same fitted producer and original fit scope.
        """
        return LogicalNumericRelation(
            _TOKEN, self._node.deviation_field("score"), self._runtime, inputs=(self,)
        )


class MaterializedDeviationResult(_MaterializedValue):
    """Fixed deviation fit whose four fields share one checked Store 8 Artifact."""

    def where(self, predicate: BoundPredicate) -> LogicalDeviationResult:
        """Select retained deviation rows without changing the fitted scope.

        Args: predicate: A bound retained-field predicate.
        Returns: A LogicalDeviationResult for fixed-only execution.
        Example: ``selected = fixed.where(fixed.score.value.is_defined())``.
        Constraints: Verifies retained parts; never rereads the source or refits.
        """
        return LogicalDeviationResult(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    @property
    def observed(self) -> MaterializedNumericRelation:
        """Read the owned retained observed field.

        Args: None.
        Returns: A MaterializedNumericRelation owned by this Artifact.
        Example: ``values = fixed.observed``.
        Constraints: Reads verified retained fields without numerical refitting.
        """
        assert self._dataset is not None
        return MaterializedNumericRelation(
            _TOKEN,
            self._node.deviation_field("observed"),
            self._runtime,
            dataset=replace(self._dataset, projection="observed"),
        )

    @property
    def reference(self) -> MaterializedNumericRelation:
        """Read the owned retained reference field.

        Args: None.
        Returns: A MaterializedNumericRelation owned by this Artifact.
        Example: ``values = fixed.reference``.
        Constraints: Reads verified retained fields without numerical refitting.
        """
        assert self._dataset is not None
        return MaterializedNumericRelation(
            _TOKEN,
            self._node.deviation_field("reference"),
            self._runtime,
            dataset=replace(self._dataset, projection="reference"),
        )

    @property
    def deviation(self) -> MaterializedNumericRelation:
        """Read the owned retained deviation field.

        Args: None.
        Returns: A MaterializedNumericRelation owned by this Artifact.
        Example: ``values = fixed.deviation``.
        Constraints: Reads verified retained fields without numerical refitting.
        """
        assert self._dataset is not None
        return MaterializedNumericRelation(
            _TOKEN,
            self._node.deviation_field("deviation"),
            self._runtime,
            dataset=replace(self._dataset, projection="deviation"),
        )

    @property
    def score(self) -> MaterializedNumericRelation:
        """Read the owned retained score field.

        Args: None.
        Returns: A MaterializedNumericRelation owned by this Artifact.
        Example: ``values = fixed.score``.
        Constraints: Reads verified retained fields without numerical refitting.
        """
        assert self._dataset is not None
        return MaterializedNumericRelation(
            _TOKEN,
            self._node.deviation_field("score"),
            self._runtime,
            dataset=replace(self._dataset, projection="score"),
        )


class LogicalAssociationResult(_Value):
    """Unexecuted association with immutable original scope and owned views."""

    def execute(self) -> MaterializedAssociationResult:
        """Execute and atomically publish this statistical graph.

        Args: None.
        Returns: A MaterializedAssociationResult bound to this exact graph.
        Example: ``fixed = result.execute()``.
        Constraints: Source execution captures anew; fixed execution verifies retained inputs.
        """
        return MaterializedAssociationResult(_TOKEN, self._node, self._runtime, dataset=self._run())

    def where(self, predicate: BoundPredicate) -> LogicalAssociationResult:
        """Select synchronized result views while retaining original scope.

        Args: predicate: A predicate on owned corresponding fields.
        Returns: A LogicalAssociationResult over the current selected keys.
        Example: ``chosen = result.where(result.selected.value.eq(True))``.
        Constraints: No reestimation, lag reselection or future-grid reconstruction.
        """
        return LogicalAssociationResult(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    @property
    def coefficient(self) -> LogicalCoefficientRelation:
        """Read the owned coefficient view.

        Args: None.
        Returns: A LogicalCoefficientRelation on the current output domain.
        Example: ``values = result.coefficient``.
        Constraints: Preserves original pair/search authority and actual retained parts.
        """
        return LogicalCoefficientRelation(
            _TOKEN, self._node.association_field("coefficient"), self._runtime, inputs=(self,)
        )

    @property
    def selected(self) -> LogicalBooleanRelation:
        """Read the owned selected view.

        Args: None.
        Returns: A LogicalBooleanRelation on the current output domain.
        Example: ``values = result.selected``.
        Constraints: Preserves original pair/search authority and actual retained parts.
        """
        return LogicalBooleanRelation(
            _TOKEN, self._node.association_field("selected"), self._runtime, inputs=(self,)
        )


class MaterializedAssociationResult(_MaterializedValue):
    """Fixed association with immutable original scope and owned views."""

    def where(self, predicate: BoundPredicate) -> LogicalAssociationResult:
        """Select synchronized result views while retaining original scope.

        Args: predicate: A predicate on owned corresponding fields.
        Returns: A LogicalAssociationResult over the current selected keys.
        Example: ``chosen = result.where(result.selected.value.eq(True))``.
        Constraints: No reestimation, lag reselection or future-grid reconstruction.
        """
        return LogicalAssociationResult(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    @property
    def coefficient(self) -> MaterializedCoefficientRelation:
        """Read the owned coefficient view.

        Args: None.
        Returns: A MaterializedCoefficientRelation on the current output domain.
        Example: ``values = result.coefficient``.
        Constraints: Preserves original pair/search authority and actual retained parts.
        """
        assert self._dataset is not None
        from marivo.analysis.core.model import PairInputsPart

        if not any(isinstance(p, PairInputsPart) for p in self._node.root.signature.parts):
            return MaterializedCoefficientRelation(
                _TOKEN, self._node, self._runtime, dataset=self._dataset
            )
        return MaterializedCoefficientRelation(
            _TOKEN,
            self._node.association_field("coefficient"),
            self._runtime,
            dataset=replace(self._dataset, projection="coefficient"),
        )

    @property
    def selected(self) -> MaterializedBooleanRelation:
        """Read the owned selected view.

        Args: None.
        Returns: A MaterializedBooleanRelation on the current output domain.
        Example: ``values = result.selected``.
        Constraints: Preserves original pair/search authority and actual retained parts.
        """
        assert self._dataset is not None
        return MaterializedBooleanRelation(
            _TOKEN,
            self._node.association_field("selected"),
            self._runtime,
            dataset=replace(self._dataset, projection="selected"),
        )


class LogicalForecastResult(_Value):
    """Unexecuted forecast with immutable original scope and owned views."""

    def execute(self) -> MaterializedForecastResult:
        """Execute and atomically publish this statistical graph.

        Args: None.
        Returns: A MaterializedForecastResult bound to this exact graph.
        Example: ``fixed = result.execute()``.
        Constraints: Source execution captures anew; fixed execution verifies retained inputs.
        """
        return MaterializedForecastResult(_TOKEN, self._node, self._runtime, dataset=self._run())

    def where(self, predicate: BoundPredicate) -> LogicalForecastResult:
        """Select synchronized result views while retaining original scope.

        Args: predicate: A predicate on owned corresponding fields.
        Returns: A LogicalForecastResult over the current selected keys.
        Example: ``chosen = result.where(result.prediction.value.gt(0))``.
        Constraints: No reestimation, lag reselection or future-grid reconstruction.
        """
        return LogicalForecastResult(_TOKEN, self._select(predicate), self._runtime, inputs=(self,))

    @property
    def prediction(self) -> LogicalNumericRelation:
        """Read the owned prediction view.

        Args: None.
        Returns: A LogicalNumericRelation on the current output domain.
        Example: ``values = result.prediction``.
        Constraints: Preserves original training/future authority and actual retained parts.
        """
        return LogicalNumericRelation(
            _TOKEN, self._node.forecast_field("prediction"), self._runtime, inputs=(self,)
        )

    @property
    def lower(self) -> LogicalNumericRelation:
        """Read the owned lower view.

        Args: None.
        Returns: A LogicalNumericRelation on the current output domain.
        Example: ``values = result.lower``.
        Constraints: Preserves original training/future authority and actual retained parts.
        """
        return LogicalNumericRelation(
            _TOKEN, self._node.forecast_field("lower"), self._runtime, inputs=(self,)
        )

    @property
    def upper(self) -> LogicalNumericRelation:
        """Read the owned upper view.

        Args: None.
        Returns: A LogicalNumericRelation on the current output domain.
        Example: ``values = result.upper``.
        Constraints: Preserves original training/future authority and actual retained parts.
        """
        return LogicalNumericRelation(
            _TOKEN, self._node.forecast_field("upper"), self._runtime, inputs=(self,)
        )


class MaterializedForecastResult(_MaterializedValue):
    """Fixed forecast with immutable original scope and owned views."""

    def where(self, predicate: BoundPredicate) -> LogicalForecastResult:
        """Select synchronized result views while retaining original scope.

        Args: predicate: A predicate on owned corresponding fields.
        Returns: A LogicalForecastResult over the current selected keys.
        Example: ``chosen = result.where(result.prediction.value.gt(0))``.
        Constraints: No reestimation, lag reselection or future-grid reconstruction.
        """
        return LogicalForecastResult(_TOKEN, self._select(predicate), self._runtime, inputs=(self,))

    @property
    def prediction(self) -> MaterializedNumericRelation:
        """Read the owned prediction view.

        Args: None.
        Returns: A MaterializedNumericRelation on the current output domain.
        Example: ``values = result.prediction``.
        Constraints: Preserves original training/future authority and actual retained parts.
        """
        assert self._dataset is not None
        return MaterializedNumericRelation(
            _TOKEN,
            self._node.forecast_field("prediction"),
            self._runtime,
            dataset=replace(self._dataset, projection="prediction"),
        )

    @property
    def lower(self) -> MaterializedNumericRelation:
        """Read the owned lower view.

        Args: None.
        Returns: A MaterializedNumericRelation on the current output domain.
        Example: ``values = result.lower``.
        Constraints: Preserves original training/future authority and actual retained parts.
        """
        assert self._dataset is not None
        return MaterializedNumericRelation(
            _TOKEN,
            self._node.forecast_field("lower"),
            self._runtime,
            dataset=replace(self._dataset, projection="lower"),
        )

    @property
    def upper(self) -> MaterializedNumericRelation:
        """Read the owned upper view.

        Args: None.
        Returns: A MaterializedNumericRelation on the current output domain.
        Example: ``values = result.upper``.
        Constraints: Preserves original training/future authority and actual retained parts.
        """
        assert self._dataset is not None
        return MaterializedNumericRelation(
            _TOKEN,
            self._node.forecast_field("upper"),
            self._runtime,
            dataset=replace(self._dataset, projection="upper"),
        )


def wrap_materialized(
    node: Relation, runtime: DatasetRuntime, dataset: GraphDataset
) -> PublicMaterialized:
    """Restore the existing public result variant from its checked typed graph."""
    kind = _kind(node)
    if kind == "forecast":
        return MaterializedForecastResult(_TOKEN, node, runtime, dataset=dataset)
    if kind == "association_read":
        return (
            MaterializedBooleanRelation(_TOKEN, node, runtime, dataset=dataset)
            if node.root.value_type == ScalarType("boolean")
            else MaterializedCoefficientRelation(_TOKEN, node, runtime, dataset=dataset)
        )
    if kind == "time_runs":
        return MaterializedTimeRunResult(_TOKEN, node, runtime, dataset=dataset)
    if kind == "deviation":
        return MaterializedDeviationResult(_TOKEN, node, runtime, dataset=dataset)
    if kind == "funnel":
        return MaterializedFunnelResult(_TOKEN, node, runtime, dataset=dataset)
    if kind == "funnel_comparison":
        return MaterializedFunnelComparisonResult(_TOKEN, node, runtime, dataset=dataset)
    if kind == "funnel_read":
        return MaterializedNumericRelation(_TOKEN, node, runtime, dataset=dataset)
    if kind == "history_distribution":
        return MaterializedStateDistributionResult(_TOKEN, node, runtime, dataset=dataset)
    if kind == "history_transitions":
        return MaterializedTransitionSummary(_TOKEN, node, runtime, dataset=dataset)
    if kind == "history_dwell":
        return MaterializedDwellSummary(_TOKEN, node, runtime, dataset=dataset)
    if kind == "history_violations":
        return MaterializedViolationResult(_TOKEN, node, runtime, dataset=dataset)
    if kind == "history_intervals":
        return MaterializedStateIntervalResult(_TOKEN, node, runtime, dataset=dataset)
    if kind in ("history_read", "history_in_state"):
        typ = node.root.value_type
        if typ == ScalarType("boolean"):
            return MaterializedBooleanRelation(_TOKEN, node, runtime, dataset=dataset)
        if typ == ScalarType("string"):
            return MaterializedCategoryRelation(_TOKEN, node, runtime, dataset=dataset)
        if typ == ScalarType("timestamp"):
            return MaterializedTemporalRelation(_TOKEN, node, runtime, dataset=dataset)
        return MaterializedNumericRelation(_TOKEN, node, runtime, dataset=dataset)
    if kind == "retention":
        return MaterializedRetentionResult(_TOKEN, node, runtime, dataset=dataset)
    if kind == "subject_retention":
        return MaterializedSubjectRetentionResult(_TOKEN, node, runtime, dataset=dataset)
    if kind == "anchor":
        return MaterializedAnchorDomain(_TOKEN, node, runtime, dataset=dataset)
    if kind == "history":
        return MaterializedHistoryResult(_TOKEN, node, runtime, dataset=dataset)
    if kind == "journey":
        return MaterializedJourneyResult(_TOKEN, node, runtime, dataset=dataset)
    if kind == "event_duration":
        return MaterializedEventDurationResult(_TOKEN, node, runtime, dataset=dataset)
    if kind == "completed_journeys":
        return MaterializedCompletedJourneys(_TOKEN, node, runtime, dataset=dataset)
    if kind == "journey_read":
        params = node.captured_definition.parameters
        assert isinstance(params, JourneyRead)
        if params.field == "dropout":
            return MaterializedBooleanRelation(_TOKEN, node, runtime, dataset=dataset)
        if params.field == "status":
            return MaterializedCategoryRelation(_TOKEN, node, runtime, dataset=dataset)
        if params.field in ("duration", "observed_duration"):
            return MaterializedNumericRelation(_TOKEN, node, runtime, dataset=dataset)
        return MaterializedTemporalRelation(_TOKEN, node, runtime, dataset=dataset)
    if kind == "attribution":
        return MaterializedAttributionResult(_TOKEN, node, runtime, dataset=dataset)
    if kind == "attribution_view":
        return MaterializedNumericRelation(_TOKEN, node, runtime, dataset=dataset)
    if kind == "ranking":
        return MaterializedRankingResult(_TOKEN, node, runtime, dataset=dataset)
    if kind == "table":
        return MaterializedTable(_TOKEN, node, runtime, dataset=dataset)
    if kind in ("members", "group"):
        return MaterializedAnalysisDomain(_TOKEN, node, runtime, dataset=dataset)
    if kind == "read":
        params = node.captured_definition.parameters
        assert isinstance(params, BindProject)
        if params.ref.kind is SemanticKind.MEASURE:
            return MaterializedNumericRelation(_TOKEN, node, runtime, dataset=dataset)
        if params.ref.kind is SemanticKind.TIME_DIMENSION:
            return MaterializedTemporalRelation(_TOKEN, node, runtime, dataset=dataset)
        if node.root.value_type == ScalarType("boolean"):
            return MaterializedBooleanRelation(_TOKEN, node, runtime, dataset=dataset)
        return MaterializedCategoryRelation(_TOKEN, node, runtime, dataset=dataset)
    if kind == "where":
        if node.root.value_type == ScalarType("boolean"):
            params = node.captured_definition.parameters
            if isinstance(params, PartsTransport) and params.mode == "view":
                return MaterializedBooleanRelation(_TOKEN, node, runtime, dataset=dataset)
            return MaterializedSelectedBooleanRelation(_TOKEN, node, runtime, dataset=dataset)
        if any(isinstance(p, JourneyPart) for p in node.root.signature.parts) and isinstance(
            node.root.value_type, DurationType
        ):
            return MaterializedSelectedNumericRelation(_TOKEN, node, runtime, dataset=dataset)
        if node.root.signature.quantity is None:
            scalar = node.root.value_type
            if scalar == ScalarType("boolean"):
                return MaterializedSelectedBooleanRelation(_TOKEN, node, runtime, dataset=dataset)
            if scalar in (ScalarType("date"), ScalarType("timestamp")):
                return MaterializedSelectedTemporalRelation(_TOKEN, node, runtime, dataset=dataset)
            params = node.captured_definition.parameters
            if isinstance(params, PartsTransport) and params.field_kind == "measure":
                return MaterializedSelectedNumericRelation(_TOKEN, node, runtime, dataset=dataset)
            return MaterializedSelectedCategoryRelation(_TOKEN, node, runtime, dataset=dataset)
        if (
            isinstance(node.root.signature.quantity, (ObservedQuantity, RolledQuantity))
            or node.root.signature.quantity.method_version == "cell.ratio@v1"
            or node.root.signature.quantity.method_version.startswith("reference.")
        ):
            return MaterializedSelectedNumericRelation(_TOKEN, node, runtime, dataset=dataset)
        return MaterializedSelectedDifferenceRelation(_TOKEN, node, runtime, dataset=dataset)
    if kind == "relation_ratio":
        return MaterializedNumericRelation(_TOKEN, node, runtime, dataset=dataset)
    if kind == "compare":
        return MaterializedDifferenceRelation(_TOKEN, node, runtime, dataset=dataset)
    if kind == "correlate":
        return MaterializedAssociationResult(_TOKEN, node, runtime, dataset=dataset)
    if kind == "correlate_where":
        return MaterializedCoefficientSelectionRelation(_TOKEN, node, runtime, dataset=dataset)
    if kind == "summarize":
        return MaterializedStatisticRelation(_TOKEN, node, runtime, dataset=dataset)
    if kind == "ratio_observe":
        return MaterializedRatioRelation(_TOKEN, node, runtime, dataset=dataset)
    if kind == "ratio_rollup":
        return MaterializedRolledRatioRelation(_TOKEN, node, runtime, dataset=dataset)
    if kind == "observe":
        return MaterializedNumericRelation(_TOKEN, node, runtime, dataset=dataset)
    if kind == "rollup":
        if node.root.signature.domain.kind == "group":
            from marivo.analysis.core.rules import (
                EntityObservationTarget,
                ObserveWeightedMean,
                OriginalReduce,
            )
            from marivo.analysis.materialization.graph_snapshot import MethodRecord

            definition = dataset.artifact.validated.root
            if isinstance(definition.parameters, OriginalReduce):
                source = next(
                    record
                    for record in dataset.artifact.validated.document.nodes
                    if record.identity == definition.inputs[0].node
                )
                parameters = source.parameters if isinstance(source, MethodRecord) else None
                if isinstance(parameters, PreparedObservation):
                    parameters = parameters.observation
                if (
                    isinstance(parameters, (ObserveMetric, ObserveCount, ObserveWeightedMean))
                    and isinstance(parameters.target, EntityObservationTarget)
                    and parameters.coordinates
                ):
                    return MaterializedNumericRelation(_TOKEN, node, runtime, dataset=dataset)
            return MaterializedGroupedNumericRelation(_TOKEN, node, runtime, dataset=dataset)
        if node.root.signature.domain.kind == "singleton":
            return MaterializedRolledNumericRelation(_TOKEN, node, runtime, dataset=dataset)
        return MaterializedNumericRelation(_TOKEN, node, runtime, dataset=dataset)
    raise _reject("a qualified public result", kind, "Recover the exact public Artifact.")


def new_members(node: Relation, runtime: DatasetRuntime) -> LogicalAnalysisDomain:
    """Bind a typed member graph to the existing public Session receiver."""
    return LogicalAnalysisDomain(_TOKEN, node, runtime)


class LogicalBooleanRelation(_CountRelation):
    """Unexecuted boolean member attribute relation."""

    def members(
        self, *, through: SubjectBinding | None = None
    ) -> LogicalAnalysisDomain | LogicalFixedAnalysisDomain:
        """Project complete Subject identities from this relation.

        Args: through: Optional exact producer-owned SubjectBinding; Entity projection is implicit.
        Returns: A source member domain or a fixed-only member continuation.
        Example: ``selected_members = relation.members()``.
        Constraints: Requires a retained total Subject map; fixed inputs cannot introduce sources.
        """
        return self._members_domain(through)

    @property
    def value(self) -> BooleanField:
        """Return this relation's bound boolean field.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        return BooleanField(self._node.root, self._node)

    def where(self, predicate: BoundPredicate) -> LogicalSelectedBooleanRelation:
        """Select rows using a predicate bound to this boolean relation.

        Args:
            predicate: Closed typed predicate over exact corresponding inputs.
        Returns: A LogicalSelectedBooleanRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: Every referenced input must cover this complete domain; all children are checked.
        """
        return LogicalSelectedBooleanRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    def execute(self) -> MaterializedBooleanRelation:
        """Evaluate and publish this boolean relation.

        Args:
            None.
        Returns: A MaterializedBooleanRelation bound to this exact relation.
        Example: ``result = relation.execute()``.
        Constraints: A source branch reevaluates; a fixed branch uses exact retained Artifacts.
        """
        return MaterializedBooleanRelation(_TOKEN, self._node, self._runtime, dataset=self._run())


class MaterializedBooleanRelation(_MaterializedValue, _CountRelation):
    """Fixed boolean relation retaining admitted member identity."""

    def members(self, *, through: SubjectBinding | None = None) -> LogicalFixedAnalysisDomain:
        """Project retained complete Subject identities without source access.

        Args: through: Optional exact producer-owned SubjectBinding; Entity projection is implicit.
        Returns: A fixed-only logical member continuation.
        Example: ``members = result.members()``.
        Constraints: Requires the exact retained Subject part and cannot read external attributes.
        """
        return LogicalFixedAnalysisDomain(
            _TOKEN, self._subject_members(through), self._runtime, inputs=(self,)
        )

    @property
    def value(self) -> BooleanField:
        """Return this relation's bound boolean field.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        return BooleanField(self._node.root, self._node)

    def where(self, predicate: BoundPredicate) -> LogicalSelectedBooleanRelation:
        """Build a fixed-only boolean selection.

        Args:
            predicate: Closed typed predicate over exact corresponding inputs.
        Returns: A LogicalSelectedBooleanRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: Every referenced input must cover this complete domain; all children are checked.
        """
        return LogicalSelectedBooleanRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )


class LogicalSelectedBooleanRelation(_CountRelation):
    """Unexecuted boolean selection over one exact read relation."""

    @property
    def value(self) -> BooleanField:
        """Return this relation's bound boolean field.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        return BooleanField(self._node.root, self._node)

    def where(self, predicate: BoundPredicate) -> LogicalSelectedBooleanRelation:
        """Select rows using a predicate bound to this boolean relation.

        Args:
            predicate: Closed typed predicate over exact corresponding inputs.
        Returns: A LogicalSelectedBooleanRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: Every referenced input must cover this complete domain; all children are checked.
        """
        return LogicalSelectedBooleanRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    def members(
        self, *, through: SubjectBinding | None = None
    ) -> LogicalAnalysisDomain | LogicalFixedAnalysisDomain:
        """Project complete Subject identities from this relation.

        Args: through: Optional exact producer-owned SubjectBinding; Entity projection is implicit.
        Returns: A source member domain or a fixed-only member continuation.
        Example: ``selected_members = relation.members()``.
        Constraints: Requires a retained total Subject map; fixed inputs cannot introduce sources.
        """
        return self._members_domain(through)

    def execute(self) -> MaterializedSelectedBooleanRelation:
        """Evaluate and publish this boolean selection.

        Args:
            None.
        Returns: A MaterializedSelectedBooleanRelation bound to this exact relation.
        Example: ``result = relation.execute()``.
        Constraints: A source branch reevaluates; a fixed branch uses exact retained Artifacts.
        """
        return MaterializedSelectedBooleanRelation(
            _TOKEN, self._node, self._runtime, dataset=self._run()
        )


class MaterializedSelectedBooleanRelation(_MaterializedValue, _CountRelation):
    """Fixed boolean selection with an exact retained member projection."""

    @property
    def value(self) -> BooleanField:
        """Return this relation's bound boolean field.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        return BooleanField(self._node.root, self._node)

    def where(self, predicate: BoundPredicate) -> LogicalSelectedBooleanRelation:
        """Select rows using a predicate bound to this boolean relation.

        Args:
            predicate: Closed typed predicate over exact corresponding inputs.
        Returns: A LogicalSelectedBooleanRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: Every referenced input must cover this complete domain; all children are checked.
        """
        return LogicalSelectedBooleanRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    def members(self, *, through: SubjectBinding | None = None) -> LogicalFixedAnalysisDomain:
        """Project selected fixed member identity without a source read.

        Args: through: Optional exact producer-owned SubjectBinding; Entity projection is implicit.
        Returns: A LogicalFixedAnalysisDomain bound to this exact relation.
        Example: ``result = relation.members()``.
        Constraints: Requires a retained total Subject map; fixed inputs cannot introduce sources.
        """
        return LogicalFixedAnalysisDomain(
            _TOKEN, self._subject_members(through), self._runtime, inputs=(self,)
        )


class LogicalTemporalRelation(_CountRelation):
    """Unexecuted temporal member attribute relation."""

    def members(
        self, *, through: SubjectBinding | None = None
    ) -> LogicalAnalysisDomain | LogicalFixedAnalysisDomain:
        """Project complete Subject identities from this relation.

        Args: through: Optional exact producer-owned SubjectBinding; Entity projection is implicit.
        Returns: A source member domain or a fixed-only member continuation.
        Example: ``selected_members = relation.members()``.
        Constraints: Requires a retained total Subject map; fixed inputs cannot introduce sources.
        """
        return self._members_domain(through)

    @property
    def value(self) -> TemporalField:
        """Return this relation's bound temporal field.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        return TemporalField(self._node.root, self._node)

    def where(self, predicate: BoundPredicate) -> LogicalSelectedTemporalRelation:
        """Select rows using a predicate bound to this temporal relation.

        Args:
            predicate: Closed typed predicate over exact corresponding inputs.
        Returns: A LogicalSelectedTemporalRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: Every referenced input must cover this complete domain; all children are checked.
        """
        return LogicalSelectedTemporalRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    def execute(self) -> MaterializedTemporalRelation:
        """Evaluate and publish this temporal relation.

        Args:
            None.
        Returns: A MaterializedTemporalRelation bound to this exact relation.
        Example: ``result = relation.execute()``.
        Constraints: A source branch reevaluates; a fixed branch uses exact retained Artifacts.
        """
        return MaterializedTemporalRelation(_TOKEN, self._node, self._runtime, dataset=self._run())


class MaterializedTemporalRelation(_MaterializedValue, _CountRelation):
    """Fixed temporal relation retaining admitted member identity."""

    def members(self, *, through: SubjectBinding | None = None) -> LogicalFixedAnalysisDomain:
        """Project retained complete Subject identities without source access.

        Args: through: Optional exact producer-owned SubjectBinding; Entity projection is implicit.
        Returns: A fixed-only logical member continuation.
        Example: ``members = result.members()``.
        Constraints: Requires the exact retained Subject part and cannot read external attributes.
        """
        return LogicalFixedAnalysisDomain(
            _TOKEN, self._subject_members(through), self._runtime, inputs=(self,)
        )

    @property
    def value(self) -> TemporalField:
        """Return this relation's bound temporal field.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        return TemporalField(self._node.root, self._node)

    def where(self, predicate: BoundPredicate) -> LogicalSelectedTemporalRelation:
        """Build a fixed-only temporal selection.

        Args:
            predicate: Closed typed predicate over exact corresponding inputs.
        Returns: A LogicalSelectedTemporalRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: Every referenced input must cover this complete domain; all children are checked.
        """
        return LogicalSelectedTemporalRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )


class LogicalSelectedTemporalRelation(_CountRelation):
    """Unexecuted temporal selection over one exact read relation."""

    def members(
        self, *, through: SubjectBinding | None = None
    ) -> LogicalAnalysisDomain | LogicalFixedAnalysisDomain:
        """Project complete Subject identities from this relation.

        Args: through: Optional exact producer-owned SubjectBinding; Entity projection is implicit.
        Returns: A source member domain or a fixed-only member continuation.
        Example: ``selected_members = relation.members()``.
        Constraints: Requires a retained total Subject map; fixed inputs cannot introduce sources.
        """
        return self._members_domain(through)

    @property
    def value(self) -> TemporalField:
        """Return this relation's bound temporal field.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        return TemporalField(self._node.root, self._node)

    def where(self, predicate: BoundPredicate) -> LogicalSelectedTemporalRelation:
        """Select rows using a predicate bound to this temporal relation.

        Args:
            predicate: Closed typed predicate over exact corresponding inputs.
        Returns: A LogicalSelectedTemporalRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: Every referenced input must cover this complete domain; all children are checked.
        """
        return LogicalSelectedTemporalRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    def execute(self) -> MaterializedSelectedTemporalRelation:
        """Evaluate and publish this temporal selection.

        Args:
            None.
        Returns: A MaterializedSelectedTemporalRelation bound to this exact relation.
        Example: ``result = relation.execute()``.
        Constraints: A source branch reevaluates; a fixed branch uses exact retained Artifacts.
        """
        return MaterializedSelectedTemporalRelation(
            _TOKEN, self._node, self._runtime, dataset=self._run()
        )


class MaterializedSelectedTemporalRelation(_MaterializedValue, _CountRelation):
    """Fixed temporal selection with an exact retained member projection."""

    @property
    def value(self) -> TemporalField:
        """Return this relation's bound temporal field.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        return TemporalField(self._node.root, self._node)

    def where(self, predicate: BoundPredicate) -> LogicalSelectedTemporalRelation:
        """Select rows using a predicate bound to this temporal relation.

        Args:
            predicate: Closed typed predicate over exact corresponding inputs.
        Returns: A LogicalSelectedTemporalRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: Every referenced input must cover this complete domain; all children are checked.
        """
        return LogicalSelectedTemporalRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    def members(self, *, through: SubjectBinding | None = None) -> LogicalFixedAnalysisDomain:
        """Project selected fixed member identity without a source read.

        Args: through: Optional exact producer-owned SubjectBinding; Entity projection is implicit.
        Returns: A LogicalFixedAnalysisDomain bound to this exact relation.
        Example: ``result = relation.members()``.
        Constraints: Requires a retained total Subject map; fixed inputs cannot introduce sources.
        """
        return LogicalFixedAnalysisDomain(
            _TOKEN, self._subject_members(through), self._runtime, inputs=(self,)
        )


class LogicalSelectedNumericRelation(_OriginalContinuation):
    """Unexecuted numeric selection over one exact read relation."""

    @property
    def value(self) -> NumericField:
        """Return the bound numeric field for an admitted strict predicate.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        return NumericField(self._node.root, self._node)

    def where(self, predicate: BoundPredicate) -> LogicalSelectedNumericRelation:
        """Select current numeric values using their bound predicate.

        Args: predicate: A comparison from this or an exactly corresponding numeric relation.value.
        Returns: A logical numeric selection preserving any retained Subject part.
        Example: ``selected = relation.where(relation.value.gt(0))``.
        Constraints: Numeric dependencies require exact corresponding keys; non-Defined predicates reject.
        """
        return LogicalSelectedNumericRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    def members(
        self, *, through: SubjectBinding | None = None
    ) -> LogicalAnalysisDomain | LogicalFixedAnalysisDomain:
        """Project complete Subject identities from this relation.

        Args: through: Optional exact producer-owned SubjectBinding; Entity projection is implicit.
        Returns: A source member domain or a fixed-only member continuation.
        Example: ``selected_members = relation.members()``.
        Constraints: Requires a retained total Subject map; fixed inputs cannot introduce sources.
        """
        return self._members_domain(through)

    def execute(self) -> MaterializedSelectedNumericRelation:
        """Evaluate and publish this numeric selection.

        Args:
            None.
        Returns: A MaterializedSelectedNumericRelation bound to this exact relation.
        Example: ``result = relation.execute()``.
        Constraints: A source branch reevaluates; a fixed branch uses exact retained Artifacts.
        """
        return MaterializedSelectedNumericRelation(
            _TOKEN, self._node, self._runtime, dataset=self._run()
        )


class MaterializedSelectedNumericRelation(_MaterializedValue, _OriginalContinuation):
    """Fixed numeric selection preserving any retained Subject map."""

    @property
    def value(self) -> NumericField:
        """Return the bound numeric field for an admitted strict predicate.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        return NumericField(self._node.root, self._node)

    def where(self, predicate: BoundPredicate) -> LogicalSelectedNumericRelation:
        """Select current numeric values using their bound predicate.

        Args: predicate: A comparison from this or an exactly corresponding numeric relation.value.
        Returns: A logical numeric selection preserving any retained Subject part.
        Example: ``selected = relation.where(relation.value.gt(0))``.
        Constraints: Numeric dependencies require exact corresponding keys; non-Defined predicates reject.
        """
        return LogicalSelectedNumericRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    def members(self, *, through: SubjectBinding | None = None) -> LogicalFixedAnalysisDomain:
        """Project selected fixed member identity without a source read.

        Args: through: Optional exact producer-owned SubjectBinding; Entity projection is implicit.
        Returns: A LogicalFixedAnalysisDomain bound to this exact relation.
        Example: ``result = relation.members()``.
        Constraints: Requires a retained total Subject map; fixed inputs cannot introduce sources.
        """
        return LogicalFixedAnalysisDomain(
            _TOKEN, self._subject_members(through), self._runtime, inputs=(self,)
        )


class _Ranking(_Value):
    def _view(
        self, name: Literal["values", "ranks"]
    ) -> LogicalNumericRelation | MaterializedNumericRelation:
        from marivo.analysis.materialization.graph_display import view

        node = view(self._node, name)
        if self._dataset is None:
            return LogicalNumericRelation(_TOKEN, node, self._runtime, inputs=(self,))
        return MaterializedNumericRelation(
            _TOKEN, node, self._runtime, dataset=replace(self._dataset, projection=name)
        )

    def where(self, predicate: BoundPredicate) -> LogicalRankingResult:
        """Restrict both views while preserving original ranks and reference scope.

        Args: predicate: A strict typed predicate from a corresponding relation or view.
        Returns: A logical ranking on the selected keys.
        Example: ``selected = ranking.where(ranking.ranks.value.is_defined())``.
        Constraints: Checks all operands; filtering never reranks or changes fixed denominators.
        """
        bound, dependencies = self._bound_predicate(predicate)
        return LogicalRankingResult(
            _TOKEN,
            self._node.where(bound, dependencies=dependencies),
            self._runtime,
            inputs=(self,),
        )

    def limit(self, count: int) -> LogicalRankingResult:
        """Take the global prefix of the original deterministic display order.

        Args: count: Integer 1..100000, excluding bool.
        Returns: A logical ranking with both views restricted to that prefix.
        Example: ``top = ranking.limit(10)``.
        Constraints: This is a global prefix, not per-partition Top-K; original ranks remain unchanged.
        """
        from marivo.analysis.materialization.graph_display import limit

        return LogicalRankingResult(_TOKEN, limit(self._node, count), self._runtime, inputs=(self,))


class LogicalRankingResult(_Ranking):
    """A logical ranking with typed same-key numeric views."""

    @property
    def values(self) -> LogicalNumericRelation:
        """Return the original quantity on the current selected ranking keys.

        Args: None.
        Returns: A LogicalNumericRelation retaining its actual sufficient parts.
        Example: ``values = ranking.values``.
        Constraints: Selection preserves the original quantity and display order.
        """
        from marivo.analysis.materialization.graph_display import view

        return LogicalNumericRelation(
            _TOKEN, view(self._node, "values"), self._runtime, inputs=(self,)
        )

    @property
    def ranks(self) -> LogicalNumericRelation:
        """Return exact int64 ranks with original non-Defined Cell states.

        Args: None.
        Returns: A LogicalNumericRelation bound to the original ranking domain.
        Example: ``ranks = ranking.ranks``.
        Constraints: No original Metric state or implicit reranking is introduced.
        """
        from marivo.analysis.materialization.graph_display import view

        return LogicalNumericRelation(
            _TOKEN, view(self._node, "ranks"), self._runtime, inputs=(self,)
        )

    def execute(self) -> MaterializedRankingResult:
        """Execute and atomically publish the qualified ranking and retained scope.

        Args: None.
        Returns: The materialized ranking with values and ranks views.
        Example: ``result = ranking.execute()``.
        Constraints: Source execution evaluates anew; fixed execution consumes verified receipts only.
        """
        return MaterializedRankingResult(_TOKEN, self._node, self._runtime, dataset=self._run())


class MaterializedRankingResult(_MaterializedValue, _Ranking):
    """A verified fixed ranking; selection preserves its original domain and order."""

    @property
    def values(self) -> MaterializedNumericRelation:
        """Return the fixed original-quantity view without allocating a new Run.

        Args: None.
        Returns: A MaterializedNumericRelation over verified retained Cells and parts.
        Example: ``values = ranking.values``.
        Constraints: Uses the same selected keys and original ranking order.
        """
        result = self._view("values")
        assert isinstance(result, MaterializedNumericRelation)
        return result

    @property
    def ranks(self) -> MaterializedNumericRelation:
        """Return the fixed int64 rank view without recomputing ranks.

        Args: None.
        Returns: A MaterializedNumericRelation bound to the original ranking domain.
        Example: ``ranks = ranking.ranks``.
        Constraints: Retains non-Defined tags/reasons and allocates no new Run.
        """
        result = self._view("ranks")
        assert isinstance(result, MaterializedNumericRelation)
        return result


class _Table:
    __slots__ = ("_dataset", "_node", "_runtime")
    _node: Relation
    _runtime: DatasetRuntime
    _dataset: GraphDataset | None

    def __init__(
        self,
        token: object,
        node: Relation,
        runtime: DatasetRuntime,
        *,
        dataset: GraphDataset | None = None,
    ) -> None:
        if token is not _TOKEN:
            raise TypeError("Construct terminal tables through mv.table().")
        self._node, self._runtime, self._dataset = node, runtime, dataset

    def __repr__(self) -> str:
        identity = (
            self._dataset.artifact.artifact_ref
            if self._dataset is not None
            else self._node.root.fingerprint[:22]
        )
        return f"<{type(self).__name__} kind=table id={identity}; use {'.show()' if self._dataset is not None else '.execute()'}>"


class LogicalTable(_Table):
    """A terminal same-key table definition with ordered labeled Relations."""

    def execute(self) -> MaterializedTable:
        """Execute all ordered columns through the common scheduler and publication.

        Args: None.
        Returns: A verified terminal MaterializedTable.
        Example: ``result = mv.table(revenue=values).execute()``.
        Constraints: Duplicate or missing keys reject; no external join or analysis continuation exists.
        """
        return MaterializedTable(_TOKEN, self._node, self._runtime, dataset=self._node.execute())


class MaterializedTable(_Table):
    """Terminal verified display/export only; no analysis continuation contract."""

    @property
    def artifact_ref(self) -> ArtifactRef:
        """Return the exact saved terminal Artifact identity for session.artifact().

        Args: None.
        Returns: The ArtifactRef identifying this committed terminal table.
        Example: ``restored = session.artifact(table.artifact_ref)``.
        Constraints: Identifies fixed saved state, without granting analysis continuations.
        """
        assert self._dataset is not None
        return ArtifactRef(ref=self._dataset.artifact.artifact_ref)

    def show(
        self, *, n: int | None = None, max_output_bytes: int | None = _DEFAULT_MAX_OUTPUT_BYTES
    ) -> None:
        """Show saved table rows with exact Cell labels and display omission counts.

        Args:
            n: Maximum displayed rows; None means all and zero means metadata only.
            max_output_bytes: UTF-8 budget including the newline; None removes the budget.
        Returns: None; prints saved values in authored column order.
        Example: ``materialized_table.show(n=20)``.
        Constraints: Saved Cells only; member identities hidden; no analysis continuations.
        """
        assert self._dataset is not None
        self._dataset.show(n=n, max_output_bytes=max_output_bytes, facts=(("kind", "table"),))

    def to_pandas(self) -> pd.DataFrame:
        """Export an isolated table containing keys and authored value columns only.

        Args: None.
        Returns: A complete DataFrame copy preserving scalar precision and column order.
        Example: ``frame = table.to_pandas()``.
        Constraints: Non-Defined Cells become missing values; pandas does not distinguish their tags/reasons. The Artifact and show() retain those facts.
        """
        assert self._dataset is not None
        return self._dataset.to_pandas()


class _Attribution(_Value):
    def where(self, predicate: BoundPredicate) -> LogicalAttributionResult:
        """Select all three views while retaining the original complete reconciliation scope.

        Args: predicate: Strict typed predicate on this result or a corresponding view.
        Returns: A logical selected attribution.
        Example: ``selected = result.where(result.contribution.value.gt(0))``.
        Constraints: Selection always revokes current-subdomain completeness.
        """
        bound, dependencies = self._bound_predicate(predicate)
        return LogicalAttributionResult(
            _TOKEN,
            self._node.where(bound, dependencies=dependencies),
            self._runtime,
            inputs=(self,),
        )


class LogicalAttributionResult(_Attribution):
    """Logical allocated change with original scope and three typed numeric views."""

    @property
    def contribution(self) -> LogicalNumericRelation:
        """Return the contribution view on exactly the current selected allocation keys.

        Args: None.
        Returns: A LogicalNumericRelation retaining the original allocation evidence.
        Example: ``values = result.contribution``.
        Constraints: Side terms are allocated components; selection does not reallocate.
        """
        from marivo.analysis.materialization.graph_attribution import view

        node = view(self._node, "contribution")
        return LogicalNumericRelation(_TOKEN, node, self._runtime, inputs=(self,))

    @property
    def current(self) -> LogicalNumericRelation:
        """Return the current view on exactly the current selected allocation keys.

        Args: None.
        Returns: A LogicalNumericRelation retaining the original allocation evidence.
        Example: ``values = result.current``.
        Constraints: Side terms are allocated components; selection does not reallocate.
        """
        from marivo.analysis.materialization.graph_attribution import view

        node = view(self._node, "current")
        return LogicalNumericRelation(_TOKEN, node, self._runtime, inputs=(self,))

    @property
    def baseline(self) -> LogicalNumericRelation:
        """Return the baseline view on exactly the current selected allocation keys.

        Args: None.
        Returns: A LogicalNumericRelation retaining the original allocation evidence.
        Example: ``values = result.baseline``.
        Constraints: Side terms are allocated components; selection does not reallocate.
        """
        from marivo.analysis.materialization.graph_attribution import view

        node = view(self._node, "baseline")
        return LogicalNumericRelation(_TOKEN, node, self._runtime, inputs=(self,))

    def execute(self) -> MaterializedAttributionResult:
        """Execute registered allocation and atomically publish every required part.

        Args: None.
        Returns: A MaterializedAttributionResult with verified original scope.
        Example: ``result = change.attribute(axes=(channel,)).execute()``.
        Constraints: Each resolution independently reconciles before publication.
        """
        return MaterializedAttributionResult(_TOKEN, self._node, self._runtime, dataset=self._run())


class MaterializedAttributionResult(_MaterializedValue, _Attribution):
    """Materialized allocated change with original scope and three typed numeric views."""

    @property
    def contribution(self) -> MaterializedNumericRelation:
        """Return the contribution view on exactly the current selected allocation keys.

        Args: None.
        Returns: A MaterializedNumericRelation retaining the original allocation evidence.
        Example: ``values = result.contribution``.
        Constraints: Side terms are allocated components; selection does not reallocate.
        """
        from marivo.analysis.materialization.graph_attribution import view

        assert self._dataset is not None
        node = view(self._node, "contribution")
        return MaterializedNumericRelation(
            _TOKEN, node, self._runtime, dataset=replace(self._dataset, projection="contribution")
        )

    @property
    def current(self) -> MaterializedNumericRelation:
        """Return the current view on exactly the current selected allocation keys.

        Args: None.
        Returns: A MaterializedNumericRelation retaining the original allocation evidence.
        Example: ``values = result.current``.
        Constraints: Side terms are allocated components; selection does not reallocate.
        """
        from marivo.analysis.materialization.graph_attribution import view

        assert self._dataset is not None
        node = view(self._node, "current")
        return MaterializedNumericRelation(
            _TOKEN, node, self._runtime, dataset=replace(self._dataset, projection="current")
        )

    @property
    def baseline(self) -> MaterializedNumericRelation:
        """Return the baseline view on exactly the current selected allocation keys.

        Args: None.
        Returns: A MaterializedNumericRelation retaining the original allocation evidence.
        Example: ``values = result.baseline``.
        Constraints: Side terms are allocated components; selection does not reallocate.
        """
        from marivo.analysis.materialization.graph_attribution import view

        assert self._dataset is not None
        node = view(self._node, "baseline")
        return MaterializedNumericRelation(
            _TOKEN, node, self._runtime, dataset=replace(self._dataset, projection="baseline")
        )


class _Journey(_Value):
    def _journey_part(self) -> JourneyPart:
        return next(p for p in self._node.root.signature.parts if isinstance(p, JourneyPart))

    def _step_index(self, step: PatternStep) -> int:
        if type(step) is not PatternStep or step.fingerprint not in self._journey_part().steps:
            raise _reject(
                "an exact retained PatternStep", repr(step), "Use a step from this Journey pattern."
            )
        return self._journey_part().steps.index(step.fingerprint)

    def funnel(self, *, axes: tuple[Ref[DimensionKind], ...] = ()) -> LogicalFunnelResult:
        """Reduce this canonical assignment into exact funnel components.

        Args: axes: Unique ordered governed Dimensions captured at each entry instant.
        Returns: A LogicalFunnelResult over dense steps and actual complete axis tuples.
        Example: ``funnel = journeys.funnel(axes=(channel,))``.
        Constraints: Requires first_per_subject; fixed inputs cannot supply missing axes.
            SQLite admits nonempty unique ordered direct string/int64 axes on an unversioned
            Subject, with no additional count or ordering limit. Direct and historical axes may
            mix through multiple to-one paths; every versioned path Entity must use a UTC
            native DATE snapshot or closed-open validity with NULL open end.
        """
        from marivo.analysis.materialization.graph_funnel import reduce

        return LogicalFunnelResult(_TOKEN, reduce(self._node, axes), self._runtime, inputs=(self,))

    def subjects(self, role: ParticipantRoleHandle) -> SubjectBinding:
        """Return the retained Subject map for an exact participant role.

        Args: role: Participant handle from this retained pattern.
        Returns: The Journey-domain SubjectBinding.
        Example: ``binding = journeys.subjects(buyer)``.
        Constraints: Foreign Events or roles reject; no source rows are read.
        """
        if type(role) is not ParticipantRoleHandle or not any(
            role.event == event.ref and role.name == event.participant
            for event in self._journey_part().preparation.events
        ):
            raise _reject(
                "a retained participant role", repr(role), "Use the exact pattern participant."
            )
        return self.subject_binding

    def time_to_event(
        self, *, from_step: PatternStep, to_step: PatternStep
    ) -> LogicalEventDurationResult:
        """Project elapsed observations for a retained ordered step pair.

        Args: from_step: Exact starting step. to_step: Exact later step.
        Returns: Six typed relations over the original Journey domain.
        Example: ``elapsed = journeys.time_to_event(from_step=start, to_step=finish)``.
        Constraints: Uses canonical assignment; never rematches or reads a new Event source.
        """
        params = JourneyDuration(self._step_index(from_step), self._step_index(to_step))
        node = self._node._with(
            method_node((self._node._edge(),), params, value_type=ScalarType("int64"))
        )
        return LogicalEventDurationResult(_TOKEN, node, self._runtime, inputs=(self,))

    def read(self, field: DroppedBefore) -> LogicalBooleanRelation:
        """Read first-per-subject dropout with coverage Unknown preserved.

        Args: field: dropped_before descriptor with an exact noninitial step.
        Returns: A BooleanRelation over retained Journeys.
        Example: ``dropout = journeys.read(mv.dropped_before(step=finish))``.
        Constraints: every_start does not produce dropout; Unknown is not False.
        """
        if type(field) is not DroppedBefore:
            raise _reject(
                "mv.dropped_before(step=...)", repr(field), "Read a retained dropout descriptor."
            )
        params = JourneyRead(0, self._step_index(field.step), "dropout")
        node = self._node._with(
            method_node((self._node._edge(),), params, value_type=ScalarType("boolean"))
        )
        return LogicalBooleanRelation(_TOKEN, node, self._runtime, inputs=(self,))


class _History(_Value):
    def read(self, field: InState) -> LogicalBooleanRelation:
        """Read checkpoint truth on the complete original Subject domain.

        Args: field: Exact in_state descriptor with an aware checkpoint.
        Returns: A LogicalBooleanRelation preserving Unknown coverage.
        Example: ``truth = history.read(mv.in_state(paid, at=checkpoint))``.
        Constraints: State belongs to the frozen model; end reads its left limit.
        """
        from marivo.analysis.core.history_types import StateAt
        from marivo.analysis.materialization.graph_history import view

        part = next(p for p in self._node.root.signature.parts if isinstance(p, HistoryPart))
        assert part.preparation.model is not None
        if type(field) is not InState or field.state.model != part.preparation.model.ref:
            raise _reject(
                "the exact retained model state",
                repr(field),
                "Use ms.model_state for this History model.",
            )
        node = view(self._node, StateAt(field.state.name, field.at.isoformat()))
        return LogicalBooleanRelation(_TOKEN, node, self._runtime, inputs=(self,))

    def distribution(
        self, *, at: tuple[datetime, ...], axes: tuple[Ref[DimensionKind], ...] = ()
    ) -> LogicalStateDistributionResult:
        """Count known states at exact historical checkpoints.

        Args: at: Unique aware checkpoints inside the report window. axes: Governed Dimensions.
        Returns: A LogicalStateDistributionResult with conditional seeded shares.
        Example: ``result = history.distribution(at=(checkpoint,))``.
        Constraints: Rows have checkpoint, model_state and full axes grain. known_state_count counts the named state. seeded_subject_count and coverage_censored_count are checkpoint/axes Subject pools repeated across model_state rows; do not sum them across states. Zero state cells include alternate states of seeded Subjects; their complement is not a NotStarted or censored Subject pool. Historical axes bind at each checkpoint; fixed missing axes reject.
        """
        from marivo.analysis.core.history_types import Distribution
        from marivo.analysis.lifecycle import instant
        from marivo.analysis.materialization.graph_history import distribution

        if type(at) is not tuple or not at:
            raise _reject(
                "a nonempty tuple of aware checkpoints", repr(at), "Pass at=(checkpoint,)."
            )
        node = distribution(
            self._node, Distribution(tuple(instant(point).isoformat() for point in at)), axes
        )
        return LogicalStateDistributionResult(_TOKEN, node, self._runtime, inputs=(self,))

    def transitions(self) -> LogicalTransitionSummary:
        """Project retained transitions without replaying the origin.

        Args: None.
        Returns: A LogicalTransitionSummary over the exact retained domain.
        Example: ``result = history.transitions()``.
        Constraints: Requires complete method-owned canonical History parts.
        """
        from marivo.analysis.core.history_types import Transitions
        from marivo.analysis.materialization.graph_history import view

        return LogicalTransitionSummary(
            _TOKEN, view(self._node, Transitions()), self._runtime, inputs=(self,)
        )

    def violations(self) -> LogicalViolationResult:
        """Project retained violations without replaying the origin.

        Args: None.
        Returns: A LogicalViolationResult over the exact retained domain.
        Example: ``result = history.violations()``.
        Constraints: Requires complete method-owned canonical History parts.
        """
        from marivo.analysis.core.history_types import Violations
        from marivo.analysis.materialization.graph_history import view

        return LogicalViolationResult(
            _TOKEN, view(self._node, Violations()), self._runtime, inputs=(self,)
        )

    def intervals(self) -> LogicalStateIntervalResult:
        """Project retained intervals without replaying the origin.

        Args: None.
        Returns: A LogicalStateIntervalResult over the exact retained domain.
        Example: ``result = history.intervals()``.
        Constraints: Requires complete method-owned canonical History parts.
        """
        from marivo.analysis.core.history_types import Intervals
        from marivo.analysis.materialization.graph_history import view

        return LogicalStateIntervalResult(
            _TOKEN, view(self._node, Intervals()), self._runtime, inputs=(self,)
        )

    def dwell(self) -> LogicalDwellSummary:
        """Project retained dwell without replaying the origin.

        Args: None.
        Returns: A LogicalDwellSummary over the exact retained domain.
        Example: ``result = history.dwell()``.
        Constraints: Rows have model_state grain. mean_duration, median_duration and p90_duration summarize completed window fragments, including left-clipped completed fragments and excluding censored fragments. Duration ticks use microseconds: seconds = ticks / 1000000; exported pandas timedeltas use total_seconds(). Finished summaries cannot be pooled. Requires complete method-owned canonical History parts.
        """
        from marivo.analysis.core.history_types import Dwell
        from marivo.analysis.materialization.graph_history import view

        return LogicalDwellSummary(_TOKEN, view(self._node, Dwell()), self._runtime, inputs=(self,))


class LogicalHistoryResult(_History):
    """Logical full-Subject canonical Lifecycle History."""

    def execute(self) -> MaterializedHistoryResult:
        """Execute replay and atomically retain the complete canonical History.

        Args: None.
        Returns: A MaterializedHistoryResult with the full Subject ledger and trace.
        Example: ``fixed = history.execute()``.
        Constraints: Source preparation and local replay share one deadline and input capture.
        """
        return MaterializedHistoryResult(_TOKEN, self._node, self._runtime, dataset=self._run())


class MaterializedHistoryResult(_History, _MaterializedValue):
    """Verified source-free canonical History with retained-state projections."""


class LogicalJourneyResult(_Journey):
    """Unexecuted canonical Journey matching in the governed graph."""

    def execute(self) -> MaterializedJourneyResult:
        """Evaluate and publish canonical Journey assignments.

        Args: None.
        Returns: An immutable MaterializedJourneyResult.
        Example: ``fixed = journeys.execute()``.
        Constraints: Source evaluation captures inputs before local matching.
        """
        return MaterializedJourneyResult(_TOKEN, self._node, self._runtime, dataset=self._run())


class MaterializedJourneyResult(_Journey, _MaterializedValue):
    """Fixed Journey assignments with source-free continuations."""


class _Duration(_Value):
    def subjects(self, role: ParticipantRoleHandle) -> SubjectBinding:
        """Return the bound Journey-to-Subject map for a retained role.

        Args: role: An exact participant handle in the saved pattern.
        Returns: The SubjectBinding of this elapsed or completed domain.
        Example: ``binding = completed.subjects(buyer)``.
        Constraints: Complete Journey keys survive projection; this reads no source rows.
        """
        part = next(p for p in self._node.root.signature.parts if isinstance(p, JourneyPart))
        if type(role) is not ParticipantRoleHandle or not any(
            role.event == event.ref and role.name == event.participant
            for event in part.preparation.events
        ):
            raise _reject(
                "a retained participant role", repr(role), "Use the exact pattern participant."
            )
        return self.subject_binding

    def _view(
        self,
        field: Literal[
            "status",
            "started_at",
            "completed_at",
            "duration",
            "observed_duration",
            "followup_until",
        ],
    ) -> Relation:
        params = self._node.definition.parameters
        assert isinstance(params, (JourneyDuration, JourneyCompleted))
        from marivo.analysis.methods.physical import DurationType

        scalar = (
            DurationType("us")
            if field in ("duration", "observed_duration")
            else ScalarType("string" if field == "status" else "timestamp")
        )
        return self._node._with(
            method_node(
                (self._node._edge(),),
                JourneyRead(params.from_step, params.to_step, field),
                value_type=scalar,
            )
        )

    @property
    def status(self) -> LogicalCategoryRelation:
        """Read completion status for the bound step pair.

        Args: None.
        Returns: CategoryRelation with five distinct statuses.
        Example: ``statuses = elapsed.status``.
        Constraints: Retains complete, incomplete, censored, absent and unknown entry.
        """
        return LogicalCategoryRelation(_TOKEN, self._view("status"), self._runtime, inputs=(self,))

    @property
    def started_at(self) -> LogicalTemporalRelation:
        """Read entry instants for the bound starting step.

        Args: None.
        Returns: TemporalRelation with absent and unknown entry Cells.
        Example: ``starts = elapsed.started_at``.
        Constraints: Uses only retained assignment.
        """
        return LogicalTemporalRelation(
            _TOKEN, self._view("started_at"), self._runtime, inputs=(self,)
        )

    @property
    def completed_at(self) -> LogicalTemporalRelation:
        """Read known completion instants.

        Args: None.
        Returns: TemporalRelation with Undefined for uncompleted pairs.
        Example: ``ends = elapsed.completed_at``.
        Constraints: Completion uses the retained canonical assignment.
        """
        return LogicalTemporalRelation(
            _TOKEN, self._view("completed_at"), self._runtime, inputs=(self,)
        )

    @property
    def duration(self) -> LogicalNumericRelation:
        """Read exact elapsed Duration for completed pairs.

        Args: None.
        Returns: NumericRelation carrying integer microsecond Duration.
        Example: ``values = elapsed.completed().duration``.
        Constraints: Uncompleted pairs have Undefined duration.
        """
        return LogicalNumericRelation(_TOKEN, self._view("duration"), self._runtime, inputs=(self,))

    @property
    def observed_duration(self) -> LogicalNumericRelation:
        """Read elapsed observation time through the supported follow-up bound.

        Args: None.
        Returns: NumericRelation with captured Duration or a non-Defined Cell.
        Example: ``observed = elapsed.observed_duration``.
        Constraints: Coverage gaps never become completed durations.
        """
        return LogicalNumericRelation(
            _TOKEN, self._view("observed_duration"), self._runtime, inputs=(self,)
        )

    @property
    def followup_until(self) -> LogicalTemporalRelation:
        """Read the supported observation endpoint.

        Args: None.
        Returns: TemporalRelation retaining unresolved coverage.
        Example: ``bounds = elapsed.followup_until``.
        Constraints: Never extends beyond the captured exclusive follow-up limit.
        """
        return LogicalTemporalRelation(
            _TOKEN, self._view("followup_until"), self._runtime, inputs=(self,)
        )

    def completed(self) -> LogicalCompletedJourneys:
        """Select known completed pairs, retaining Journey statistical units.

        Args: None.
        Returns: A CompletedJourneys subdomain.
        Example: ``completed = elapsed.completed()``.
        Constraints: Selection consumes saved assignment; no rematching.
        """
        params = self._node.definition.parameters
        assert isinstance(params, (JourneyDuration, JourneyCompleted))
        node = self._node._with(
            method_node(
                (self._node._edge(),),
                JourneyCompleted(params.from_step, params.to_step),
                value_type=ScalarType("int64"),
            )
        )
        return LogicalCompletedJourneys(_TOKEN, node, self._runtime, inputs=(self,))


class LogicalEventDurationResult(_Duration):
    """Six elapsed-observation relations over one canonical Journey domain."""

    def execute(self) -> MaterializedEventDurationResult:
        """Publish the bound elapsed-observation view.

        Args: None.
        Returns: A fixed EventDurationResult.
        Example: ``fixed = elapsed.execute()``.
        Constraints: Retains assignment, coverage and exact step pair.
        """
        return MaterializedEventDurationResult(
            _TOKEN, self._node, self._runtime, dataset=self._run()
        )


class MaterializedEventDurationResult(_Duration, _MaterializedValue):
    """Fixed elapsed-observation view over retained Journey assignments."""


class LogicalCompletedJourneys(_Duration):
    """Known completed step pairs retaining Journey multiplicity."""

    def execute(self) -> MaterializedCompletedJourneys:
        """Publish the known-completed Journey subdomain.

        Args: None.
        Returns: A fixed CompletedJourneys view.
        Example: ``fixed = elapsed.completed().execute()``.
        Constraints: Distinct Journey rows remain distinct for statistics.
        """
        return MaterializedCompletedJourneys(_TOKEN, self._node, self._runtime, dataset=self._run())


class MaterializedCompletedJourneys(_Duration, _MaterializedValue):
    """Fixed known-completed Journey subdomain."""


@dataclass(frozen=True, slots=True)
class _FunnelHandle:
    owner: str
    field: FunnelField

    def __repr__(self) -> str:
        return f"<FunnelField field={self.field} owner={self.owner}>"


class _Funnel(_Value):
    def _funnel_part(self) -> FunnelPart | FunnelComparisonPart:
        return next(
            p
            for p in self._node.root.signature.parts
            if isinstance(p, (FunnelPart, FunnelComparisonPart))
        )

    def _target_step(self, target: FunnelLossRate) -> int:
        part = self._funnel_part()
        journey = part.journey if isinstance(part, FunnelPart) else part.current.journey
        if type(target) is not FunnelLossRate or target.step.fingerprint not in journey.steps[1:]:
            raise _reject(
                "an exact noninitial retained PatternStep",
                repr(target),
                "Use mv.funnel_loss_rate(step=...) from this pattern.",
            )
        return journey.steps.index(target.step.fingerprint)

    def read(self, handle: _FunnelHandle | FunnelLossRate) -> LogicalNumericRelation:
        """Read an owned component or the loss into one exact retained step.

        Args: handle: A field handle owned by this receiver, or mv.funnel_loss_rate(step=...).
        Returns: A NumericRelation retaining original components and endpoint evidence.
        Example: ``lost = funnel.read(funnel.lost_count)``.
        Constraints: Foreign handles and initial-step loss targets reject.
        """
        from marivo.analysis.materialization.graph_funnel import read

        if isinstance(handle, FunnelLossRate):
            step = self._target_step(handle)
            field: FunnelField = (
                "loss_rate_from_previous"
                if isinstance(self._funnel_part(), FunnelPart)
                else "loss_rate_delta"
            )
        elif type(handle) is _FunnelHandle and handle.owner == self._node.root.identity:
            step, field = None, handle.field
        else:
            raise _reject(
                "a handle owned by this exact receiver",
                repr(handle),
                "Read a field property from this FunnelResult.",
            )
        return LogicalNumericRelation(
            _TOKEN, read(self._node, field, step), self._runtime, inputs=(self,)
        )


class _FunnelResult(_Funnel):
    def compare(
        self, baseline: LogicalFunnelResult | MaterializedFunnelResult
    ) -> LogicalFunnelComparisonResult:
        """Pair two exact compatible funnel periods over the complete outer axis domain.

        Args: baseline: FunnelResult in this Session with the same explicit population.
        Returns: A LogicalFunnelComparisonResult with exact counts and loss-rate changes.
        Example: ``change = current.compare(baseline)``.
        Constraints: Definitions, axes, elapsed windows, follow-up and complete coverage must agree.
        """
        from marivo.analysis.materialization.graph_funnel import compare

        if not isinstance(baseline, (LogicalFunnelResult, MaterializedFunnelResult)):
            raise _reject(
                "a compatible FunnelResult",
                repr(baseline),
                "Build both endpoints from the same explicit population.",
            )
        return LogicalFunnelComparisonResult(
            _TOKEN, compare(self._node, baseline._node), self._runtime, inputs=(self, baseline)
        )

    @property
    def cohort_count(self) -> _FunnelHandle:
        """Return this receiver's exact cohort_count handle.

        Args: None.
        Returns: An immutable owned field handle.
        Example: ``values = funnel.read(funnel.cohort_count)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "cohort_count")

    @property
    def resolved_cohort_count(self) -> _FunnelHandle:
        """Return this receiver's exact resolved_cohort_count handle.

        Args: None.
        Returns: An immutable owned field handle.
        Example: ``values = funnel.read(funnel.resolved_cohort_count)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "resolved_cohort_count")

    @property
    def entry_count(self) -> _FunnelHandle:
        """Return this receiver's exact entry_count handle.

        Args: None.
        Returns: An immutable owned field handle.
        Example: ``values = funnel.read(funnel.entry_count)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "entry_count")

    @property
    def resolved_entry_count(self) -> _FunnelHandle:
        """Return this receiver's exact resolved_entry_count handle.

        Args: None.
        Returns: An immutable owned field handle.
        Example: ``values = funnel.read(funnel.resolved_entry_count)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "resolved_entry_count")

    @property
    def reached_count(self) -> _FunnelHandle:
        """Return this receiver's exact reached_count handle.

        Args: None.
        Returns: An immutable owned field handle.
        Example: ``values = funnel.read(funnel.reached_count)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "reached_count")

    @property
    def lost_count(self) -> _FunnelHandle:
        """Return this receiver's exact lost_count handle.

        Args: None.
        Returns: An immutable owned field handle.
        Example: ``values = funnel.read(funnel.lost_count)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "lost_count")

    @property
    def coverage_censored_count(self) -> _FunnelHandle:
        """Return this receiver's exact coverage_censored_count handle.

        Args: None.
        Returns: An immutable owned field handle.
        Example: ``values = funnel.read(funnel.coverage_censored_count)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "coverage_censored_count")

    @property
    def conversion_from_first(self) -> _FunnelHandle:
        """Return this receiver's exact conversion_from_first handle.

        Args: None.
        Returns: An immutable owned field handle.
        Example: ``values = funnel.read(funnel.conversion_from_first)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "conversion_from_first")

    @property
    def conversion_from_previous(self) -> _FunnelHandle:
        """Return this receiver's exact conversion_from_previous handle.

        Args: None.
        Returns: An immutable owned field handle.
        Example: ``values = funnel.read(funnel.conversion_from_previous)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "conversion_from_previous")

    @property
    def loss_rate_from_previous(self) -> _FunnelHandle:
        """Return this receiver's exact loss_rate_from_previous handle.

        Args: None.
        Returns: An immutable owned field handle.
        Example: ``values = funnel.read(funnel.loss_rate_from_previous)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "loss_rate_from_previous")


class LogicalFunnelResult(_FunnelResult):
    """Logical exact first-per-subject funnel over original assignments."""

    def execute(self) -> MaterializedFunnelResult:
        """Publish exact funnel components and their frozen original assignment scope.

        Args: None.
        Returns: A MaterializedFunnelResult.
        Example: ``result = journeys.funnel().execute()``.
        Constraints: Source axes are prepared before all local consumers.
        """
        return MaterializedFunnelResult(_TOKEN, self._node, self._runtime, dataset=self._run())


class MaterializedFunnelResult(_FunnelResult, _MaterializedValue):
    """Fixed funnel components with source-free reads and comparisons."""


class _FunnelComparison(_Funnel):
    def attribute(
        self,
        *,
        target: FunnelLossRate,
        axes: tuple[Ref[DimensionKind], ...],
        mode: Literal["joint", "hierarchy"] = "joint",
        top_k: int | None = None,
    ) -> LogicalAttributionResult:
        """Allocate one loss-rate change using the exact shared ratio-mix basis.

        Args: target: Exact noninitial loss step. axes: Unique ordered Dimensions. mode: joint or hierarchy. top_k: Common resolved-entry basis limit 1..1000 or None.
        Returns: An AttributionResult with allocated current, baseline and contribution views.
        Example: ``parts = change.attribute(target=mv.funnel_loss_rate(step=paid), axes=(channel,))``.
        Constraints: Logical missing axes consume the same assignment; fixed missing axes reject.
        """
        from marivo.analysis.materialization.graph_funnel import attribute

        return LogicalAttributionResult(
            _TOKEN,
            attribute(
                self._node, axes=axes, target_step=self._target_step(target), mode=mode, top_k=top_k
            ),
            self._runtime,
            inputs=(self,),
        )

    @property
    def current_cohort_count(self) -> _FunnelHandle:
        """Return this receiver's exact current_cohort_count handle.

        Args: None.
        Returns: An immutable owned comparison field handle.
        Example: ``values = change.read(change.current_cohort_count)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "current_cohort_count")

    @property
    def current_resolved_cohort_count(self) -> _FunnelHandle:
        """Return this receiver's exact current_resolved_cohort_count handle.

        Args: None.
        Returns: An immutable owned comparison field handle.
        Example: ``values = change.read(change.current_resolved_cohort_count)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "current_resolved_cohort_count")

    @property
    def current_entry_count(self) -> _FunnelHandle:
        """Return this receiver's exact current_entry_count handle.

        Args: None.
        Returns: An immutable owned comparison field handle.
        Example: ``values = change.read(change.current_entry_count)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "current_entry_count")

    @property
    def current_resolved_entry_count(self) -> _FunnelHandle:
        """Return this receiver's exact current_resolved_entry_count handle.

        Args: None.
        Returns: An immutable owned comparison field handle.
        Example: ``values = change.read(change.current_resolved_entry_count)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "current_resolved_entry_count")

    @property
    def current_reached_count(self) -> _FunnelHandle:
        """Return this receiver's exact current_reached_count handle.

        Args: None.
        Returns: An immutable owned comparison field handle.
        Example: ``values = change.read(change.current_reached_count)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "current_reached_count")

    @property
    def current_lost_count(self) -> _FunnelHandle:
        """Return this receiver's exact current_lost_count handle.

        Args: None.
        Returns: An immutable owned comparison field handle.
        Example: ``values = change.read(change.current_lost_count)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "current_lost_count")

    @property
    def current_coverage_censored_count(self) -> _FunnelHandle:
        """Return this receiver's exact current_coverage_censored_count handle.

        Args: None.
        Returns: An immutable owned comparison field handle.
        Example: ``values = change.read(change.current_coverage_censored_count)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "current_coverage_censored_count")

    @property
    def baseline_cohort_count(self) -> _FunnelHandle:
        """Return this receiver's exact baseline_cohort_count handle.

        Args: None.
        Returns: An immutable owned comparison field handle.
        Example: ``values = change.read(change.baseline_cohort_count)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "baseline_cohort_count")

    @property
    def baseline_resolved_cohort_count(self) -> _FunnelHandle:
        """Return this receiver's exact baseline_resolved_cohort_count handle.

        Args: None.
        Returns: An immutable owned comparison field handle.
        Example: ``values = change.read(change.baseline_resolved_cohort_count)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "baseline_resolved_cohort_count")

    @property
    def baseline_entry_count(self) -> _FunnelHandle:
        """Return this receiver's exact baseline_entry_count handle.

        Args: None.
        Returns: An immutable owned comparison field handle.
        Example: ``values = change.read(change.baseline_entry_count)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "baseline_entry_count")

    @property
    def baseline_resolved_entry_count(self) -> _FunnelHandle:
        """Return this receiver's exact baseline_resolved_entry_count handle.

        Args: None.
        Returns: An immutable owned comparison field handle.
        Example: ``values = change.read(change.baseline_resolved_entry_count)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "baseline_resolved_entry_count")

    @property
    def baseline_reached_count(self) -> _FunnelHandle:
        """Return this receiver's exact baseline_reached_count handle.

        Args: None.
        Returns: An immutable owned comparison field handle.
        Example: ``values = change.read(change.baseline_reached_count)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "baseline_reached_count")

    @property
    def baseline_lost_count(self) -> _FunnelHandle:
        """Return this receiver's exact baseline_lost_count handle.

        Args: None.
        Returns: An immutable owned comparison field handle.
        Example: ``values = change.read(change.baseline_lost_count)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "baseline_lost_count")

    @property
    def baseline_coverage_censored_count(self) -> _FunnelHandle:
        """Return this receiver's exact baseline_coverage_censored_count handle.

        Args: None.
        Returns: An immutable owned comparison field handle.
        Example: ``values = change.read(change.baseline_coverage_censored_count)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "baseline_coverage_censored_count")

    @property
    def current_loss_rate_from_previous(self) -> _FunnelHandle:
        """Return this receiver's exact current_loss_rate_from_previous handle.

        Args: None.
        Returns: An immutable owned comparison field handle.
        Example: ``values = change.read(change.current_loss_rate_from_previous)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "current_loss_rate_from_previous")

    @property
    def baseline_loss_rate_from_previous(self) -> _FunnelHandle:
        """Return this receiver's exact baseline_loss_rate_from_previous handle.

        Args: None.
        Returns: An immutable owned comparison field handle.
        Example: ``values = change.read(change.baseline_loss_rate_from_previous)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "baseline_loss_rate_from_previous")

    @property
    def loss_rate_delta(self) -> _FunnelHandle:
        """Return this receiver's exact loss_rate_delta handle.

        Args: None.
        Returns: An immutable owned comparison field handle.
        Example: ``values = change.read(change.loss_rate_delta)``.
        Constraints: Only this exact receiver accepts the handle.
        """
        return _FunnelHandle(self._node.root.identity, "loss_rate_delta")


class LogicalFunnelComparisonResult(_FunnelComparison):
    """Logical complete funnel-period pairing with a frozen Finding extractor."""

    def execute(self) -> MaterializedFunnelComparisonResult:
        """Publish the comparison and bounded nonempty eligible Finding collection.

        Args: None.
        Returns: A MaterializedFunnelComparisonResult.
        Example: ``result = current.compare(baseline).execute()``.
        Constraints: Artifact, Evidence, Findings and terminal publish in one transaction.
        """
        return MaterializedFunnelComparisonResult(
            _TOKEN, self._node, self._runtime, dataset=self._run()
        )


class MaterializedFunnelComparisonResult(_FunnelComparison, _MaterializedValue):
    """Fixed period comparison with original counts and source-free attribution."""


class _HistoryViewResult(_Value):
    def _field(self, field: HistoryField) -> Relation:
        from marivo.analysis.methods.history_view_physical import output_type

        if self._dataset is not None:
            self._dataset.verified()
        params = HistoryRead(field)
        return self._node._with(
            method_node((self._node._edge(),), params, value_type=output_type(params))
        )


class _HistoryInstance(_HistoryViewResult):
    def subjects(self) -> SubjectBinding:
        """Bind the instance domain to the sole exact model Subject.

        Args: None.
        Returns: The producer-owned total SubjectBinding.
        Example: ``binding = violations.subjects()``.
        Constraints: The binding retains complete keys and never reads source rows.
        """
        return self.subject_binding

    def members(
        self, *, through: SubjectBinding
    ) -> LogicalAnalysisDomain | LogicalFixedAnalysisDomain:
        """Project the set image of selected instance Subjects.

        Args: through: Exact mapping acquired from subjects().
        Returns: A logical source or fixed AnalysisDomain.
        Example: ``members = selected.members(through=violations.subjects())``.
        Constraints: Instance multiplicity does not multiply Subject identities.
        """
        node = self._subject_members(through)
        if self._has_fixed():
            return LogicalFixedAnalysisDomain(_TOKEN, node, self._runtime, inputs=(self,))
        return LogicalAnalysisDomain(_TOKEN, node, self._runtime, inputs=(self,))


class _StateDistributionResult(_HistoryViewResult):
    """Typed fields of the retained StateDistributionResult domain."""

    @property
    def known_state_count(self) -> LogicalNumericRelation:
        """Read the bound known_state_count relation.

        Args: None.
        Returns: A LogicalNumericRelation counting Subjects in this row's modeled state.
        Example: ``values = result.known_state_count``.
        Constraints: Uses retained method state; this does not read new source facts.
        """
        return LogicalNumericRelation(
            _TOKEN, self._field("known_state_count"), self._runtime, inputs=(self,)
        )

    @property
    def seeded_subject_count(self) -> LogicalNumericRelation:
        """Read the bound seeded_subject_count relation.

        Args: None.
        Returns: A LogicalNumericRelation counting seeded Subjects per checkpoint and full axes tuple.
        Example: ``values = result.seeded_subject_count``.
        Constraints: The same Subject pool repeats on every model_state row; do not sum across states. Uses retained method state without new source reads.
        """
        return LogicalNumericRelation(
            _TOKEN, self._field("seeded_subject_count"), self._runtime, inputs=(self,)
        )

    @property
    def coverage_censored_count(self) -> LogicalNumericRelation:
        """Read the bound coverage_censored_count relation.

        Args: None.
        Returns: A LogicalNumericRelation counting insufficient-coverage Subjects per checkpoint and full axes tuple.
        Example: ``values = result.coverage_censored_count``.
        Constraints: The same Subject pool repeats on every model_state row; do not sum across states. Zero known-state cells do not identify this pool. Uses retained method state without new source reads.
        """
        return LogicalNumericRelation(
            _TOKEN, self._field("coverage_censored_count"), self._runtime, inputs=(self,)
        )

    @property
    def share_among_seeded(self) -> LogicalNumericRelation:
        """Read the bound share_among_seeded relation.

        Args: None.
        Returns: A LogicalNumericRelation over this exact view domain.
        Example: ``values = result.share_among_seeded``.
        Constraints: Uses retained method state; this does not read new source facts.
        """
        return LogicalNumericRelation(
            _TOKEN, self._field("share_among_seeded"), self._runtime, inputs=(self,)
        )

    def where(self, predicate: BoundPredicate) -> LogicalStateDistributionResult:
        """Select exact view rows using owned typed fields.

        Args: predicate: Closed predicate on corresponding field relations.
        Returns: A LogicalStateDistributionResult preserving original scope and sufficient parts.
        Example: ``selected = result.where(predicate)``.
        Constraints: Every predicate input must cover the receiver; Unknown rejects.
        """
        return LogicalStateDistributionResult(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )


class LogicalStateDistributionResult(_StateDistributionResult):
    """Logical retained StateDistributionResult projection."""

    def execute(self) -> MaterializedStateDistributionResult:
        """Evaluate and atomically publish this exact History view.

        Args: None.
        Returns: A MaterializedStateDistributionResult with receipt-bound state.
        Example: ``fixed = result.execute()``.
        Constraints: Fixed execution never reopens Semantic or source connections.
        """
        return MaterializedStateDistributionResult(
            _TOKEN, self._node, self._runtime, dataset=self._run()
        )


class MaterializedStateDistributionResult(_StateDistributionResult, _MaterializedValue):
    """Verified fixed StateDistributionResult with retained continuations."""


class _TransitionSummary(_HistoryViewResult):
    """Typed fields of the retained TransitionSummary domain."""

    @property
    def count(self) -> LogicalNumericRelation:
        """Read the bound count relation.

        Args: None.
        Returns: A LogicalNumericRelation over this exact view domain.
        Example: ``values = result.count``.
        Constraints: Uses retained method state; this does not read new source facts.
        """
        return LogicalNumericRelation(_TOKEN, self._field("count"), self._runtime, inputs=(self,))

    @property
    def share_of_modeled_transitions(self) -> LogicalNumericRelation:
        """Read the bound share_of_modeled_transitions relation.

        Args: None.
        Returns: A LogicalNumericRelation over this exact view domain.
        Example: ``values = result.share_of_modeled_transitions``.
        Constraints: Uses retained method state; this does not read new source facts.
        """
        return LogicalNumericRelation(
            _TOKEN, self._field("share_of_modeled_transitions"), self._runtime, inputs=(self,)
        )

    def where(self, predicate: BoundPredicate) -> LogicalTransitionSummary:
        """Select exact view rows using owned typed fields.

        Args: predicate: Closed predicate on corresponding field relations.
        Returns: A LogicalTransitionSummary preserving original scope and sufficient parts.
        Example: ``selected = result.where(predicate)``.
        Constraints: Every predicate input must cover the receiver; Unknown rejects.
        """
        return LogicalTransitionSummary(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )


class LogicalTransitionSummary(_TransitionSummary):
    """Logical retained TransitionSummary projection."""

    def execute(self) -> MaterializedTransitionSummary:
        """Evaluate and atomically publish this exact History view.

        Args: None.
        Returns: A MaterializedTransitionSummary with receipt-bound state.
        Example: ``fixed = result.execute()``.
        Constraints: Fixed execution never reopens Semantic or source connections.
        """
        return MaterializedTransitionSummary(_TOKEN, self._node, self._runtime, dataset=self._run())


class MaterializedTransitionSummary(_TransitionSummary, _MaterializedValue):
    """Verified fixed TransitionSummary with retained continuations."""


class _DwellSummary(_HistoryViewResult):
    """Typed fields of the retained DwellSummary domain."""

    @property
    def interval_count(self) -> LogicalNumericRelation:
        """Read the bound interval_count relation.

        Args: None.
        Returns: A LogicalNumericRelation over this exact view domain.
        Example: ``values = result.interval_count``.
        Constraints: Uses retained method state; this does not read new source facts.
        """
        return LogicalNumericRelation(
            _TOKEN, self._field("interval_count"), self._runtime, inputs=(self,)
        )

    @property
    def completed_count(self) -> LogicalNumericRelation:
        """Read the bound completed_count relation.

        Args: None.
        Returns: A LogicalNumericRelation over this exact view domain.
        Example: ``values = result.completed_count``.
        Constraints: Uses retained method state; this does not read new source facts.
        """
        return LogicalNumericRelation(
            _TOKEN, self._field("completed_count"), self._runtime, inputs=(self,)
        )

    @property
    def right_censored_count(self) -> LogicalNumericRelation:
        """Read the bound right_censored_count relation.

        Args: None.
        Returns: A LogicalNumericRelation over this exact view domain.
        Example: ``values = result.right_censored_count``.
        Constraints: Uses retained method state; this does not read new source facts.
        """
        return LogicalNumericRelation(
            _TOKEN, self._field("right_censored_count"), self._runtime, inputs=(self,)
        )

    @property
    def coverage_censored_count(self) -> LogicalNumericRelation:
        """Read the bound coverage_censored_count relation.

        Args: None.
        Returns: A LogicalNumericRelation over this exact view domain.
        Example: ``values = result.coverage_censored_count``.
        Constraints: Uses retained method state; this does not read new source facts.
        """
        return LogicalNumericRelation(
            _TOKEN, self._field("coverage_censored_count"), self._runtime, inputs=(self,)
        )

    @property
    def left_clipped_completed_count(self) -> LogicalNumericRelation:
        """Read the bound left_clipped_completed_count relation.

        Args: None.
        Returns: A LogicalNumericRelation over this exact view domain.
        Example: ``values = result.left_clipped_completed_count``.
        Constraints: Uses retained method state; this does not read new source facts.
        """
        return LogicalNumericRelation(
            _TOKEN, self._field("left_clipped_completed_count"), self._runtime, inputs=(self,)
        )

    @property
    def mean_duration(self) -> LogicalNumericRelation:
        """Read the bound mean_duration relation.

        Args: None.
        Returns: A microsecond Duration relation over the modeled-state domain.
        Example: ``values = result.mean_duration``.
        Constraints: Completed window fragments only; seconds = ticks / 1000000. Exported pandas timedeltas use total_seconds(). Finished summary means cannot be pooled. No new source reads.
        """
        return LogicalNumericRelation(
            _TOKEN, self._field("mean_duration"), self._runtime, inputs=(self,)
        )

    @property
    def median_duration(self) -> LogicalNumericRelation:
        """Read the bound median_duration relation.

        Args: None.
        Returns: A microsecond Duration relation over the modeled-state domain.
        Example: ``values = result.median_duration``.
        Constraints: Completed window fragments only; seconds = ticks / 1000000. Exported pandas timedeltas use total_seconds(). Finished quantiles cannot be pooled. No new source reads.
        """
        return LogicalNumericRelation(
            _TOKEN, self._field("median_duration"), self._runtime, inputs=(self,)
        )

    @property
    def p90_duration(self) -> LogicalNumericRelation:
        """Read the bound p90_duration relation.

        Args: None.
        Returns: A microsecond Duration relation over the modeled-state domain.
        Example: ``values = result.p90_duration``.
        Constraints: Completed window fragments only; seconds = ticks / 1000000. Exported pandas timedeltas use total_seconds(). Finished quantiles cannot be pooled. No new source reads.
        """
        return LogicalNumericRelation(
            _TOKEN, self._field("p90_duration"), self._runtime, inputs=(self,)
        )

    def where(self, predicate: BoundPredicate) -> LogicalDwellSummary:
        """Select exact view rows using owned typed fields.

        Args: predicate: Closed predicate on corresponding field relations.
        Returns: A LogicalDwellSummary preserving original scope and sufficient parts.
        Example: ``selected = result.where(predicate)``.
        Constraints: Every predicate input must cover the receiver; Unknown rejects.
        """
        return LogicalDwellSummary(_TOKEN, self._select(predicate), self._runtime, inputs=(self,))


class LogicalDwellSummary(_DwellSummary):
    """Logical retained DwellSummary projection."""

    def execute(self) -> MaterializedDwellSummary:
        """Evaluate and atomically publish this exact History view.

        Args: None.
        Returns: A MaterializedDwellSummary with receipt-bound state.
        Example: ``fixed = result.execute()``.
        Constraints: Fixed execution never reopens Semantic or source connections.
        """
        return MaterializedDwellSummary(_TOKEN, self._node, self._runtime, dataset=self._run())


class MaterializedDwellSummary(_DwellSummary, _MaterializedValue):
    """Verified fixed DwellSummary with retained continuations."""


class _ViolationResult(_HistoryInstance):
    """Typed fields of the retained ViolationResult domain."""

    @property
    def trigger(self) -> LogicalCategoryRelation:
        """Read the bound trigger relation.

        Args: None.
        Returns: A LogicalCategoryRelation over this exact view domain.
        Example: ``values = result.trigger``.
        Constraints: Uses retained method state; this does not read new source facts.
        """
        return LogicalCategoryRelation(
            _TOKEN, self._field("trigger"), self._runtime, inputs=(self,)
        )

    @property
    def occurred_at(self) -> LogicalTemporalRelation:
        """Read the bound occurred_at relation.

        Args: None.
        Returns: A LogicalTemporalRelation over this exact view domain.
        Example: ``values = result.occurred_at``.
        Constraints: Uses retained method state; this does not read new source facts.
        """
        return LogicalTemporalRelation(
            _TOKEN, self._field("occurred_at"), self._runtime, inputs=(self,)
        )

    @property
    def state_at_event(self) -> LogicalCategoryRelation:
        """Read the bound state_at_event relation.

        Args: None.
        Returns: A LogicalCategoryRelation over this exact view domain.
        Example: ``values = result.state_at_event``.
        Constraints: Uses retained method state; this does not read new source facts.
        """
        return LogicalCategoryRelation(
            _TOKEN, self._field("state_at_event"), self._runtime, inputs=(self,)
        )

    @property
    def kind(self) -> LogicalCategoryRelation:
        """Read the bound kind relation.

        Args: None.
        Returns: A LogicalCategoryRelation over this exact view domain.
        Example: ``values = result.kind``.
        Constraints: Uses retained method state; this does not read new source facts.
        """
        return LogicalCategoryRelation(_TOKEN, self._field("kind"), self._runtime, inputs=(self,))

    def where(self, predicate: BoundPredicate) -> LogicalViolationResult:
        """Select exact view rows using owned typed fields.

        Args: predicate: Closed predicate on corresponding field relations.
        Returns: A LogicalViolationResult preserving original scope and sufficient parts.
        Example: ``selected = result.where(predicate)``.
        Constraints: Every predicate input must cover the receiver; Unknown rejects.
        """
        return LogicalViolationResult(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )


class LogicalViolationResult(_ViolationResult):
    """Logical retained ViolationResult projection."""

    def execute(self) -> MaterializedViolationResult:
        """Evaluate and atomically publish this exact History view.

        Args: None.
        Returns: A MaterializedViolationResult with receipt-bound state.
        Example: ``fixed = result.execute()``.
        Constraints: Fixed execution never reopens Semantic or source connections.
        """
        return MaterializedViolationResult(_TOKEN, self._node, self._runtime, dataset=self._run())


class MaterializedViolationResult(_ViolationResult, _MaterializedValue):
    """Verified fixed ViolationResult with retained continuations."""


class _StateIntervalResult(_HistoryInstance):
    """Typed fields of the retained StateIntervalResult domain."""

    @property
    def state(self) -> LogicalCategoryRelation:
        """Read the bound state relation.

        Args: None.
        Returns: A LogicalCategoryRelation over this exact view domain.
        Example: ``values = result.state``.
        Constraints: Uses retained method state; this does not read new source facts.
        """
        return LogicalCategoryRelation(_TOKEN, self._field("state"), self._runtime, inputs=(self,))

    @property
    def start(self) -> LogicalTemporalRelation:
        """Read the bound start relation.

        Args: None.
        Returns: A LogicalTemporalRelation over this exact view domain.
        Example: ``values = result.start``.
        Constraints: Uses retained method state; this does not read new source facts.
        """
        return LogicalTemporalRelation(_TOKEN, self._field("start"), self._runtime, inputs=(self,))

    @property
    def end(self) -> LogicalTemporalRelation:
        """Read the bound end relation.

        Args: None.
        Returns: A LogicalTemporalRelation over this exact view domain.
        Example: ``values = result.end``.
        Constraints: Uses retained method state; this does not read new source facts.
        """
        return LogicalTemporalRelation(_TOKEN, self._field("end"), self._runtime, inputs=(self,))

    @property
    def observed_duration(self) -> LogicalNumericRelation:
        """Read the bound observed_duration relation.

        Args: None.
        Returns: A LogicalNumericRelation over this exact view domain.
        Example: ``values = result.observed_duration``.
        Constraints: Uses retained method state; this does not read new source facts.
        """
        return LogicalNumericRelation(
            _TOKEN, self._field("observed_duration"), self._runtime, inputs=(self,)
        )

    @property
    def left_clipped(self) -> LogicalBooleanRelation:
        """Read the bound left_clipped relation.

        Args: None.
        Returns: A LogicalBooleanRelation over this exact view domain.
        Example: ``values = result.left_clipped``.
        Constraints: Uses retained method state; this does not read new source facts.
        """
        return LogicalBooleanRelation(
            _TOKEN, self._field("left_clipped"), self._runtime, inputs=(self,)
        )

    @property
    def status(self) -> LogicalCategoryRelation:
        """Read the bound status relation.

        Args: None.
        Returns: A LogicalCategoryRelation over this exact view domain.
        Example: ``values = result.status``.
        Constraints: Uses retained method state; this does not read new source facts.
        """
        return LogicalCategoryRelation(_TOKEN, self._field("status"), self._runtime, inputs=(self,))

    def where(self, predicate: BoundPredicate) -> LogicalStateIntervalResult:
        """Select exact view rows using owned typed fields.

        Args: predicate: Closed predicate on corresponding field relations.
        Returns: A LogicalStateIntervalResult preserving original scope and sufficient parts.
        Example: ``selected = result.where(predicate)``.
        Constraints: Every predicate input must cover the receiver; Unknown rejects.
        """
        return LogicalStateIntervalResult(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )


class LogicalStateIntervalResult(_StateIntervalResult):
    """Logical retained StateIntervalResult projection."""

    def execute(self) -> MaterializedStateIntervalResult:
        """Evaluate and atomically publish this exact History view.

        Args: None.
        Returns: A MaterializedStateIntervalResult with receipt-bound state.
        Example: ``fixed = result.execute()``.
        Constraints: Fixed execution never reopens Semantic or source connections.
        """
        return MaterializedStateIntervalResult(
            _TOKEN, self._node, self._runtime, dataset=self._run()
        )


class MaterializedStateIntervalResult(_StateIntervalResult, _MaterializedRead):
    """Verified fixed StateIntervalResult with retained continuations."""


class _AnchorDomain(_Value):
    def observe(
        self,
        metric: Ref[MetricKind] | RuntimeMetricExpr,
        *,
        within: ElapsedWindow | CalendarWindow,
        via: Ref[RelationshipKind] | RootRoutes,
    ) -> LogicalNumericRelation:
        """Observe a governed Metric separately in each relative Anchor window.

        Args:
            metric: Exact Metric or RuntimeMetricExpr.
            within: Positive closed relative window.
            via: Exact route or independently bound contribution-root routes.

        Returns: A LogicalNumericRelation on the complete Anchor instance domain.
        Example: ``values = anchors.observe(revenue, within=mv.elapsed(mv.duration(hours=168)), via=route)``.
        Constraints: Shared overlapping contributions retain use keys; fixed new source input rejects. SQLite admits count and additive int64/float64 sums with zero/null empty policy, filtered slices and ratio/linear compositions. Relative paths have no fixed hop limit and retain version capture. Fold, cumulative, mean, distinct and contribution coordinates remain unqualified.
        """
        from marivo.analysis.materialization.graph_anchors import observe

        if not isinstance(within, (ElapsedWindow, CalendarWindow)):
            raise _reject(
                "a closed relative window", repr(within), "Use mv.elapsed or mv.calendar_days."
            )
        if not isinstance(self._node.binding, LiveBinding):
            raise _reject(
                "a source Anchor with Metric dependencies captured in the same graph",
                f"{type(self).__name__} with fixed starts and no retained Metric input",
                "Observe on the source Anchor before executing it; continue an already observed numeric Artifact through its current contract.",
            )
        declared = via.routes if isinstance(via, RootRoutesValue) else (via,)
        if isinstance(self._node.binding, LiveBinding):
            from marivo.semantic.validator import normalize_target_relationship

            for route in declared:
                if isinstance(route, RootRouteValue):
                    if not route.through:
                        raise _reject(
                            "a nonempty explicit Anchor route",
                            route.root.path,
                            "Bind the existing Anchor contribution route explicitly.",
                        )
                    relationship = normalize_target_relationship(
                        self._node.binding.graph.registry, route.through[0].path
                    )
                    if relationship.from_entity_ref.path != route.root.path:
                        raise _reject(
                            f"contribution root {relationship.from_entity_ref.path}",
                            route.root.path,
                            f"Bind the route root to {relationship.from_entity_ref.path}, declared by {route.through[0].path}.",
                        )
        paths = tuple(
            route.through if isinstance(route, RootRouteValue) else (route,) for route in declared
        )
        return LogicalNumericRelation(
            _TOKEN,
            observe(self._node, metric, window=within, paths=paths),
            self._runtime,
            inputs=(self,),
        )

    def retention(
        self,
        returning: ParticipantRoleHandle,
        *,
        within: ElapsedWindow | CalendarWindow,
        completeness: tuple[
            BoundedCompletenessDeclarationV1 | SourceOriginCompletenessDeclarationV1, ...
        ] = (),
    ) -> LogicalRetentionResult:
        """Classify returns on the fixed complete Anchor instance population.

        Args:
            returning: Exact Event participant role of the Anchor Subject.
            within: Required positive elapsed or calendar window.
            completeness: Exact bound return-Event coverage declarations.
        Returns: A LogicalRetentionResult with retained true, false and unknown status.
        Example: ``result = anchors.retention(buyer, within=mv.elapsed(mv.duration(hours=168)))``.
        Constraints: Unknown stays in Omega; fixed starts cannot introduce live return input.
        """
        from marivo.analysis.materialization.graph_retention import bind

        return LogicalRetentionResult(
            _TOKEN, bind(self._node, returning, within, completeness), self._runtime, inputs=(self,)
        )

    def subjects(self, role: ParticipantRoleHandle) -> SubjectBinding:
        """Return the exact retained Anchor-to-Subject mapping.

        Args: role: The exact source Event participant role.
        Returns: A total SubjectBinding on Anchor instances.
        Example: ``binding = anchors.subjects(buyer)``.
        Constraints: No Subject deduplication of instance observations; foreign roles reject.
        """
        part = next(p for p in self._node.root.signature.parts if isinstance(p, AnchorDomainPart))
        if not any(
            role.event == e.ref and role.name == e.participant for e in part.preparation.events
        ):
            raise _reject(
                "the retained participant", repr(role), "Use the Anchor's exact source participant."
            )
        return self.subject_binding


class LogicalAnchorDomain(_AnchorDomain):
    """Unexecuted exact Event or Journey Anchor instances."""

    def execute(self) -> MaterializedAnchorDomain:
        """Publish complete Anchor instances and their retained authority.

        Args: None.
        Returns: The paired MaterializedAnchorDomain.
        Example: ``result = anchors.execute()``.
        Constraints: One governed graph; source invocation receives a fresh identity.
        """
        return MaterializedAnchorDomain(_TOKEN, self._node, self._runtime, dataset=self._run())


class MaterializedAnchorDomain(_AnchorDomain, _MaterializedValue):
    """Receipt-bound Anchor instances with source-free reads."""


class _Retention(_Value):
    @property
    def status(self) -> LogicalBooleanRelation:
        """Read the exact retained three-valued status relation.

        Args: None.
        Returns: A BooleanRelation on the original full Omega.
        Example: ``status = retention.status``.
        Constraints: Unknown means insufficient follow-up; no implicit truth conversion.
        """
        node = self._node._with(
            method_node(
                (self._node._edge(),),
                PartsTransport(
                    "view", self._node.root.signature.domain, ("subject", "retention"), True
                ),
                value_type=ScalarType("boolean"),
            )
        )
        return LogicalBooleanRelation(_TOKEN, node, self._runtime, inputs=(self,))

    def known_true(self) -> LogicalSelectedBooleanRelation:
        """Select decidable true status while retaining the original population.

        Args: None.
        Returns: A selected BooleanRelation suitable for exact Subject projection.
        Example: ``selected = retention.known_true()``.
        Constraints: Instance members require selected.subject_binding; bounds remain unchanged.
        """
        status = self.status
        known = status.where(status.value.is_defined())
        return known.where(known.value.eq(True))

    def known_false(self) -> LogicalSelectedBooleanRelation:
        """Select decidable false status while retaining the original population.

        Args: None.
        Returns: A selected BooleanRelation of false instances or Subjects.
        Example: ``selected = retention.known_false()``.
        Constraints: This view cannot produce known-true members or redefine bounds.
        """
        status = self.status
        known = status.where(status.value.is_defined())
        return known.where(known.value.eq(False))

    def unknown(self) -> LogicalSelectedBooleanRelation:
        """Select insufficient-follow-up status without dropping it from Omega.

        Args: None.
        Returns: A selected BooleanRelation retaining Unknown Cells.
        Example: ``pending = retention.unknown()``.
        Constraints: Unknown selection cannot produce exact known-true members.
        """
        status = self.status
        return status.where(not_(status.value.is_defined()))


class _InstanceRetention(_Retention):
    def by_subject(self, *, rule: AnyAnchor | EveryAnchor) -> LogicalSubjectRetentionResult:
        """Fix the complete Subject image and explicitly quantify its Anchor fibers.

        Args: rule: Required mv.any_anchor() or mv.every_anchor() rule.
        Returns: A LogicalSubjectRetentionResult on a new Subject-image Omega.
        Example: ``subjects = retention.by_subject(rule=mv.any_anchor())``.
        Constraints: Consumes every retained Anchor status; there is no default rule.
        """
        from marivo.analysis.materialization.graph_retention import by_subject

        return LogicalSubjectRetentionResult(
            _TOKEN, by_subject(self._node, rule), self._runtime, inputs=(self,)
        )


class LogicalRetentionResult(_InstanceRetention):
    """Unexecuted retention on the original full Anchor population."""

    def execute(self) -> MaterializedRetentionResult:
        """Execute and atomically retain the complete instance truth partition.

        Args: None.
        Returns: A MaterializedRetentionResult.
        Example: ``result = retention.execute()``.
        Constraints: Source execution is fresh; no unknown instances leave the denominator.
        """
        return MaterializedRetentionResult(_TOKEN, self._node, self._runtime, dataset=self._run())


class MaterializedRetentionResult(_InstanceRetention, _MaterializedValue):
    """Verified instance truth and complete scope for source-free continuation."""


class LogicalSubjectRetentionResult(_Retention):
    """Unexecuted explicit Subject-image retention."""

    def execute(self) -> MaterializedSubjectRetentionResult:
        """Execute the explicit quantifier on complete retained Anchor fibers.

        Args: None.
        Returns: A MaterializedSubjectRetentionResult.
        Example: ``result = subjects.execute()``.
        Constraints: Fixed execution uses retained instances only; unknown remains in Omega.
        """
        return MaterializedSubjectRetentionResult(
            _TOKEN, self._node, self._runtime, dataset=self._run()
        )


class MaterializedSubjectRetentionResult(_Retention, _MaterializedValue):
    """Verified Subject retention with original instance fibers and explicit rule."""


def new_history(node: Relation, runtime: DatasetRuntime) -> LogicalHistoryResult:
    """Wrap the exact canonical History graph without evaluating it."""
    return LogicalHistoryResult(_TOKEN, node, runtime)


def new_journeys(node: Relation, runtime: DatasetRuntime) -> LogicalJourneyResult:
    """Bind the public Journey receiver to its governed graph."""
    return LogicalJourneyResult(_TOKEN, node, runtime)


PublicMaterialized: TypeAlias = (
    MaterializedBooleanRelation
    | MaterializedCoefficientRelation
    | MaterializedTimeRunResult
    | MaterializedDeviationResult
    | MaterializedRetentionResult
    | MaterializedSubjectRetentionResult
    | MaterializedAnchorDomain
    | MaterializedHistoryResult
    | MaterializedStateDistributionResult
    | MaterializedTransitionSummary
    | MaterializedDwellSummary
    | MaterializedViolationResult
    | MaterializedStateIntervalResult
    | MaterializedFunnelResult
    | MaterializedFunnelComparisonResult
    | MaterializedJourneyResult
    | MaterializedEventDurationResult
    | MaterializedCompletedJourneys
    | MaterializedBooleanRelation
    | MaterializedTemporalRelation
    | MaterializedSelectedBooleanRelation
    | MaterializedSelectedTemporalRelation
    | MaterializedSelectedNumericRelation
    | MaterializedAnalysisDomain
    | MaterializedCategoryRelation
    | MaterializedSelectedCategoryRelation
    | MaterializedNumericRelation
    | MaterializedGroupedNumericRelation
    | MaterializedRolledNumericRelation
    | MaterializedRatioRelation
    | MaterializedRolledRatioRelation
    | MaterializedDifferenceRelation
    | MaterializedSelectedDifferenceRelation
    | MaterializedStatisticRelation
    | MaterializedCoefficientSelectionRelation
    | MaterializedForecastResult
    | MaterializedAssociationResult
    | MaterializedAttributionResult
    | MaterializedRankingResult
    | MaterializedTable
)


NumericRelation: TypeAlias = (
    MaterializedGroupedNumericRelation
    | LogicalNumericRelation
    | MaterializedNumericRelation
    | LogicalRatioRelation
    | MaterializedRatioRelation
    | LogicalRolledNumericRelation
    | MaterializedRolledNumericRelation
    | LogicalRolledRatioRelation
    | MaterializedRolledRatioRelation
    | LogicalDifferenceRelation
    | MaterializedDifferenceRelation
    | LogicalSelectedNumericRelation
    | MaterializedSelectedNumericRelation
    | LogicalSelectedDifferenceRelation
    | MaterializedSelectedDifferenceRelation
    | LogicalStatisticRelation
    | MaterializedStatisticRelation
)


AnalysisDomain: TypeAlias = (
    LogicalAnalysisDomain | MaterializedAnalysisDomain | LogicalFixedAnalysisDomain
)
CategoryRelation: TypeAlias = (
    LogicalCategoryRelation
    | MaterializedCategoryRelation
    | LogicalSelectedCategoryRelation
    | MaterializedSelectedCategoryRelation
)


@dataclass(frozen=True, slots=True, init=False)
class ReferenceWeights:
    """A pure binding of complete dimensionless stratum weights and statistical unit."""

    _values: NumericRelation
    _strata: tuple[CategoryRelation, ...]
    _unit: Ref[EntityKind]
    _identity: str

    def __init__(
        self,
        token: object,
        values: NumericRelation,
        strata: tuple[CategoryRelation, ...],
        unit: Ref[EntityKind],
    ) -> None:
        if token is not _TOKEN:
            raise _reject(
                "mv.reference_weights", "direct constructor", "Use the reference factory."
            )
        object.__setattr__(self, "_values", values)
        object.__setattr__(self, "_strata", strata)
        object.__setattr__(self, "_unit", unit)
        from marivo.analysis.materialization.graph_protocol import digest

        object.__setattr__(
            self,
            "_identity",
            digest(
                repr(
                    (
                        values._node.root.fingerprint,
                        tuple(item._node.definition.fingerprint for item in strata),
                        unit,
                    )
                )
            ),
        )

    def __repr__(self) -> str:
        return (
            f"<ReferenceWeights id={self._identity[:22]} strata={len(self._strata)}; use .show()>"
        )

    def show(self) -> None:
        """Show bounded reference identity, axes and statistical unit.

        Args: None.
        Returns: None; prints the reference binding without reading business rows.
        Example: ``weights.show()``.
        Constraints: The factory is a pure binding and has no standalone execute.
        """
        print(repr(self))
        print("Statistical Entity: " + self._unit.path[:180])
        for index, item in enumerate(self._strata[:8]):
            print(f"Stratum {index}: {item._node.root.fingerprint[:22]}")


def reference_weights(
    values: NumericRelation, *, strata: tuple[CategoryRelation, ...], unit: Ref[EntityKind]
) -> ReferenceWeights:
    """Bind complete stratum weights to their independent fixed reference.

    Args:
        values: Grouped dimensionless NumericRelation of stratum weights.
        strata: Nonempty ordered unique classifications used to group these values.
        unit: Statistical-unit Entity, distinct from the measurement unit.
    Returns: A ReferenceWeights input for NumericRelation.standardize.
    Example: ``weights = mv.reference_weights(shares, strata=(region,), unit=orders)``.
    Constraints: Same Session and source/fixed mode; exact strata, no normalization.
    """
    from marivo.analysis.materialization.graph_reference import invalid
    from marivo.refs import SemanticKind

    if (
        not isinstance(values, _NumericComparison)
        or type(strata) is not tuple
        or not strata
        or any(not isinstance(item, _CountRelation) for item in strata)
        or type(unit) is not Ref
        or unit.kind is not SemanticKind.ENTITY
    ):
        raise invalid(
            "typed values, a nonempty CategoryRelation tuple and an Entity Ref are required"
        )
    axes = tuple(item._node.classification_coordinate() for item in strata)
    quantity = values._node.root.signature.quantity
    if (
        len(set(axes)) != len(axes)
        or axes != values._node.root.signature.domain.instance_key
        or quantity is None
        or quantity.unit not in (None, "1")
    ):
        raise invalid(
            "weights must have exactly the ordered unique grouping axes and no measurement unit"
        )
    for item in strata:
        if (
            item._runtime.session_ref != values._runtime.session_ref
            or item._runtime.store.store_id != values._runtime.store.store_id
            or item._has_fixed() != values._has_fixed()
        ):
            raise invalid("classification dependencies must share the reference Session and mode")
        from marivo.analysis.core.graph import retained_nodes
        from marivo.analysis.materialization.graph_snapshot import same_node_definition

        if not any(
            same_node_definition(node, item._node.definition)
            for node in retained_nodes(values._node.definition)
        ):
            raise invalid(
                "classification is not a retained grouping or inclusion dependency of these weights"
            )
    return ReferenceWeights(_TOKEN, values, strata, unit)


@dataclass(frozen=True, slots=True, init=False)
class OneToOneCorrespondence:
    """A declared relationship bound to two exact ordered numeric nodes.

    Obtain this value from one_to_one; it cannot be reused for equivalent new nodes.
    """

    _left: Relation
    _right: Relation
    _relationship: TargetRelationshipContract
    _time: PeriodChange | None

    def __init__(
        self,
        token: object,
        left: Relation,
        right: Relation,
        relationship: TargetRelationshipContract,
        time: PeriodChange | None,
    ) -> None:
        if token is not _TOKEN:
            raise _reject(
                "one_to_one producer",
                "direct correspondence construction",
                "Call mv.one_to_one(left=..., right=..., via=...).",
            )
        object.__setattr__(self, "_left", left)
        object.__setattr__(self, "_right", right)
        object.__setattr__(self, "_relationship", relationship)
        object.__setattr__(self, "_time", time)

    def __repr__(self) -> str:
        return f"<OneToOneCorrespondence left={self._left.root.identity[:12]} right={self._right.root.identity[:12]}; use .show()>"

    def show(self) -> None:
        """Print the exact ordered binding and declared relationship.

        Args: None.
        Returns: None; prints bounded identity facts.
        Example: ``mv.one_to_one(left=first, right=second, via=relationship).show()``.
        Constraints: Does not read business rows or execute a Run.
        """
        print(
            f"OneToOneCorrespondence: {self._left.root.identity} -> {self._right.root.identity}; via={self._relationship.ref.path}; time={'window_bucket' if self._time else 'exact'}"
        )


def one_to_one(
    *,
    left: NumericRelation,
    right: NumericRelation,
    via: Ref[RelationshipKind],
    time: PeriodChange | None = None,
) -> OneToOneCorrespondence:
    """Bind a declared one-to-one relationship to exact ordered numeric endpoints.

    Args:
        left: Numerator relation from the same Session and mode as right.
        right: Denominator relation with the complete retained relationship keys.
        via: Explicit declared one-to-one Relationship Ref.
        time: Optional complete PeriodChange mapping for different time grids.
    Returns: An immutable OneToOneCorrespondence for these exact nodes only.
    Example: ``left.ratio(right, pairing=mv.one_to_one(left=left, right=right, via=relationship))``.
    Constraints: Rejects many-to-one, UnionKeys, mixed source/fixed modes and unretained relationship keys.
    """
    from marivo.analysis.core.graph import MethodNode, Node
    from marivo.analysis.core.rules import (
        ObserveCount,
        ObserveMetric,
        ObserveWeightedMean,
    )

    if (
        not isinstance(left, _NumericComparison)
        or not isinstance(right, _NumericComparison)
        or not isinstance(via, Ref)
        or via.kind != SemanticKind.RELATIONSHIP
        or (
            time is not None
            and (type(time) is not PeriodChange or type(time.pairing) is not ExactKeys)
        )
    ):
        raise _reject(
            "numeric endpoints, a Relationship Ref and optional exact PeriodChange",
            "invalid correspondence arguments",
            "Use one_to_one with the exact typed endpoints and declared one-to-one relationship.",
        )
    if (
        type(left._node.binding) is not type(right._node.binding)
        or left._runtime.session_ref != right._runtime.session_ref
        or left._runtime.store.store_id != right._runtime.store.store_id
    ):
        raise _reject(
            "one Session and source/fixed mode",
            "foreign or mixed endpoints",
            "Use endpoints from the same Session and execution mode.",
        )
    if isinstance(left._node.binding, LiveBinding):
        relationship = normalize_target_relationship(left._node.binding.graph.registry, via.path)
    else:
        retained: list[TargetRelationshipContract] = []
        pending: list[Node] = [left._node.definition, right._node.definition]
        seen: set[str] = set()
        while pending:
            node = pending.pop()
            if node.identity in seen:
                continue
            seen.add(node.identity)
            if not isinstance(node, MethodNode):
                continue
            pending.extend(edge.node for edge in node.inputs)
            pending.extend(node.sources)
            pending.extend(node.retained_endpoints)
            params = node.parameters
            if isinstance(params, (ObserveMetric, ObserveCount, ObserveWeightedMean)):
                retained.extend(item for item in params.path if item.ref.path == via.path)
            elif isinstance(params, BindProject):
                retained.extend(item for item in params.path_contracts if item.ref.path == via.path)
            elif (
                isinstance(params, CellDerive)
                and params.relationship is not None
                and params.relationship.ref.path == via.path
            ):
                retained.append(params.relationship)
        if not retained or any(item != retained[0] for item in retained):
            raise _reject(
                "one retained exact relationship definition",
                via.path,
                "Use Artifacts retaining this declared correspondence; fixed continuations never reload Semantic.",
            )
        relationship = retained[0]
    # Construction checks the same core correspondence rule without executing its graph.
    left._node.combine(
        right._node,
        "relation_ratio",
        design="period" if time is not None else "time",
        relationship=relationship,
    )
    return OneToOneCorrespondence(_TOKEN, left._node, right._node, relationship, time)


def table(
    **columns: LogicalCoefficientRelation
    | MaterializedCoefficientRelation
    | LogicalCoefficientSelectionRelation
    | MaterializedCoefficientSelectionRelation
    | NumericRelation
    | CategoryRelation
    | LogicalBooleanRelation
    | MaterializedBooleanRelation
    | LogicalSelectedBooleanRelation
    | MaterializedSelectedBooleanRelation
    | LogicalTemporalRelation
    | MaterializedTemporalRelation
    | LogicalSelectedTemporalRelation
    | MaterializedSelectedTemporalRelation,
) -> LogicalTable:
    """Bind an ordered terminal table from complete corresponding scalar Relations.

    Args: columns: Nonempty display labels mapped to typed numeric, categorical, boolean or temporal Relations.
    Returns: A LogicalTable using the shared graph scheduler.
    Example: ``profile = mv.table(revenue=values, region=region).execute()``.
    Constraints: All columns share full typed keys, time meaning, Session and source/fixed mode. Fitted columns and their ranks retain original fit scope and authority. Labels cannot collide with exported key names. The table has no analysis continuation.
    """
    from marivo.analysis.materialization.graph_display import bind, invalid

    if not columns or any(
        not isinstance(value, _Value)
        or not isinstance(
            value,
            (
                _NumericComparison,
                LogicalCoefficientRelation,
                MaterializedCoefficientRelation,
                LogicalCoefficientSelectionRelation,
                MaterializedCoefficientSelectionRelation,
                LogicalCategoryRelation,
                MaterializedCategoryRelation,
                LogicalSelectedCategoryRelation,
                MaterializedSelectedCategoryRelation,
                LogicalBooleanRelation,
                MaterializedBooleanRelation,
                LogicalSelectedBooleanRelation,
                MaterializedSelectedBooleanRelation,
                LogicalTemporalRelation,
                MaterializedTemporalRelation,
                LogicalSelectedTemporalRelation,
                MaterializedSelectedTemporalRelation,
            ),
        )
        for value in columns.values()
    ):
        raise invalid("table requires nonempty scalar Relation columns")
    values = tuple(columns.values())
    first = values[0]
    keys = tuple(
        ("member" if first._node.root.signature.domain.kind == "entity" else "group")
        if i == 0
        else f"coord_{i - 1}"
        for i in range(len(first._node.root.signature.domain.instance_key))
    )
    if any(not label or label in keys for label in columns):
        raise invalid(
            repr(tuple(columns)),
            expected="nonempty display labels distinct from exported key names",
            repair=f"Choose nonempty labels outside the key names {keys!r}.",
        )
    node = bind(
        tuple(value._node for value in values),
        DisplayTable(
            tuple(columns),
            tuple(value._node.root.value_type.name for value in values),
            tuple(value._node.root.fingerprint for value in values),
        ),
    )
    return LogicalTable(_TOKEN, node, first._runtime)
