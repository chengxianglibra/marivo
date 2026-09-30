"""Existing public Analysis receivers over one typed graph Runtime and Store 7."""

from __future__ import annotations

import builtins
from contextlib import redirect_stdout
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from inspect import Parameter, signature
from io import StringIO
from typing import TYPE_CHECKING, Literal, NoReturn, TypeAlias, overload

import pandas as pd

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
from marivo.analysis._time_grid import GridWindow as GridWindow
from marivo.analysis._time_grid import TimeGrid as TimeGrid
from marivo.analysis._time_grid import time_grid as time_grid
from marivo.analysis.core.graph import FixedLeaf
from marivo.analysis.core.model import (
    Coordinate,
    DerivedQuantity,
    ObservedQuantity,
    OriginalStatePart,
    RolledQuantity,
    RowStatisticQuantity,
    SubjectPart,
    part_role,
)
from marivo.analysis.core.predicates import TemporalLiteral, ValuePredicate
from marivo.analysis.core.rules import (
    AssociationScore,
    BindProject,
    CellDerive,
    CompleteGroups,
    MapCorrespond,
    PartsTransport,
    RowState,
    TimeProduct,
)
from marivo.analysis.core.time_grid import BoundTimeGrid, GridPoint, bind_grid
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization.graph_dataset import GraphDataset
from marivo.analysis.materialization.graph_fields import (
    BooleanField,
    BoundPredicate,
    CategoryField,
    CategoryPredicate,
    CompositePredicate,
    NumericField,
    NumericPredicate,
    RootRoutesValue,
    RootRouteValue,
    ScalarPredicate,
    StatePredicate,
    TemporalField,
    root_route,
    root_routes,
)
from marivo.analysis.materialization.graph_relation import FrozenBinding, LiveBinding, Relation
from marivo.analysis.methods.physical import ScalarType
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
from marivo.semantic.ir import TargetRelationshipContract
from marivo.semantic.runtime_metric import RuntimeMetricExpr
from marivo.semantic.validator import normalize_target_relationship

if TYPE_CHECKING:
    from marivo.analysis.datasets.state import MaterializedDatasetState
    from marivo.analysis.materialization.admission import DatasetRuntime

RootRoute: TypeAlias = RootRouteValue
RootRoutes: TypeAlias = RootRoutesValue
MetricInputValue: TypeAlias = Ref[MetricKind] | RuntimeMetricExpr
_TOKEN = object()
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
        through: Ordered nonempty Relationship Ref path to the member Entity.
    Returns: A closed RootRoute for an admitted ratio observation.
    Example: ``path = mv.route(order, through=(buyer,))``.
    Constraints: The route is validated against the selected member and Metric.
    """
    return root_route(root, through=through)


def routes(*items: RootRoute) -> RootRoutes:
    """Bind ordered explicit Entity routes.

    Args: items: One or more RootRoute values for distinct roots.
    Returns: A closed RootRoutes value for read or observe.
    Example: ``pair = mv.routes(line_route, order_route)``.
    Constraints: Read requires one member-rooted route; observations validate their own root order.
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
    definition = node.definition
    params, quantity = definition.parameters, definition.signature.quantity
    if isinstance(params, TimeProduct):
        return "members"
    if isinstance(params, CompleteGroups):
        quantity = node.root.signature.quantity
        return (
            "group"
            if quantity is None
            else "summarize"
            if isinstance(quantity, RowStatisticQuantity)
            else "ratio_rollup"
            if quantity is not None and quantity.method_version == "ratio@v1"
            else "rollup"
        )
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
            or (
                isinstance(node.root, FixedLeaf)
                and node.root.artifact.ref == dataset.artifact.artifact_ref
            )
            else Relation.restore(dataset)
        )

    def contract(self) -> AnalysisContract:
        """Return the verified relation state and its mechanically valid next calls.

        Args: None.
        Returns: An AnalysisContract for this exact relation.
        Example: ``relation.contract().show()``.
        Constraints: Fixed results verify their Store 7 files without opening sources.
        """
        if self._dataset is not None:
            self._dataset.verified()
        signature = self._node.root.signature
        kind, fixed = _kind(self._node), self._dataset is not None
        roles = tuple(part_role(part) for part in signature.parts)
        names: tuple[str, ...]
        if isinstance(self, MaterializedCoefficientRelation):
            names = ("where", "summarize")
        elif kind == "members":
            names = (
                (("cohort",) if signature.domain.time_grid is None else ())
                if fixed
                else (("cohort", "execute") if signature.domain.time_grid is None else ("execute",))
                if self._has_fixed()
                else ("read", "group_by", "observe", "execute")
                if signature.domain.time_grid is not None
                else ("each", "cohort", "read", "group_by", "observe", "execute")
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
            names = ("observe",)
        elif kind == "correlate":
            names = ("coefficient",) if fixed else ("execute",)
        elif kind == "correlate_where":
            names = ("summarize",)
        elif kind in ("compare", "relation_ratio"):
            names = ("where", "summarize")
        elif kind == "where":
            names = ("members", "summarize") if signature.quantity is not None else ("members",)
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
        if kind == "where":
            names = ("where", *names)
        state = next((p for p in signature.parts if isinstance(p, OriginalStatePart)), None)
        if state is not None and state.temporal_policy == "repeated":
            names = tuple(name for name in names if name != "rollup")
        if isinstance(self, _CountRelation):
            names = tuple(dict.fromkeys((*names, "group_by", "summarize")))
        if not fixed and kind not in ("members", "read", "group", "correlate"):
            names = (*names, "execute")
        if isinstance(self, _NumericComparison):
            if self._node.comparison_error is None:
                names = tuple(dict.fromkeys((*names, "compare", "ratio")))
            else:
                names = tuple(name for name in names if name not in ("compare", "ratio"))
        if (
            "subject" in roles
            and callable(getattr(type(self), "members", None))
            and "members" not in names
        ):
            names = (*names, "members")
        required = tuple(role for role in roles if role != "subject")
        return AnalysisContract(
            kind,
            "materialized" if fixed else "logical",
            self._action_contract(names),
            signature.domain.kind,
            None if signature.quantity is None else signature.quantity.kind,
            required,
            roles if fixed else (),
            self._contract_facts(),
        )

    def _contract_facts(self) -> tuple[tuple[str, str], ...]:
        signature = self._node.root.signature
        quantity = signature.quantity
        facts: list[tuple[str, str]] = []
        if self._node.comparison_error is not None:
            facts.append(("comparison_unavailable", self._node.comparison_error))
        params = self._node.definition.parameters
        if isinstance(params, BindProject):
            facts.extend((("field", params.ref.path), ("field_kind", params.ref.kind.value)))
        if self._dataset is not None:
            schema = self._dataset.artifact.descriptor
            from marivo.analysis.materialization.graph_protocol import schema_from

            physical = schema_from(schema.realized_schema)
            if "value" in physical.names:
                facts.append(("value_type", str(physical.field("value").type)))
        if _kind(self._node) in ("ratio_observe", "ratio_rollup"):
            facts.append(("weighting", "original numerator and denominator components"))
        if quantity is not None:
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
            if quantity.method_version == "ratio@v1":
                facts.append(("weighting", "original numerator and denominator components"))
            if (
                isinstance(quantity, RowStatisticQuantity)
                and quantity.method_version == "row.mean@v1"
            ):
                facts.append(("weighting", "equal current rows"))
        if self._dataset is not None:
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
        else:
            facts.append(("source", "logical definition; no business rows read"))
        return tuple(facts)

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
                literal: int | float | str | bool | TemporalLiteral | Decimal = 0
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

    def _subject_members(self, through: SubjectBinding | None) -> Relation:
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
        references: list[Ref[DimensionKind] | Ref[EntityKind] | BoundTimeGrid] = []
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
                node = node.attach_category(key._node)
                from marivo.refs import ref

                references.append(ref.dimension(key._node.classification_coordinate().field))
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
                if name == "coefficient":
                    actions.append(
                        AnalysisAction(
                            "relation.coefficient", "analysis.MaterializedCoefficientRelation"
                        )
                    )
                continue
            if not callable(member):
                continue
            bound = getattr(self, name)
            descriptor = REGISTRY.by_callable(bound)
            arguments = tuple(
                f"*{parameter.name}"
                if parameter.kind is Parameter.VAR_POSITIONAL
                else f"{parameter.name}={parameter.name}"
                if parameter.kind is Parameter.KEYWORD_ONLY
                else parameter.name
                for parameter in signature(bound).parameters.values()
                if parameter.default is Parameter.empty
            )
            actions.append(
                AnalysisAction(
                    f"relation.{name}({', '.join(arguments)})",
                    f"analysis.{descriptor.canonical_id}",
                )
            )
        return tuple(actions)


class _NumericComparison(_Value):
    """Shared numeric composition without granting original Metric reductions."""

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
        Constraints: Time/period comparisons share target captures; cohorts share Group/Singleton coordinates and time.
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
            )
        else:
            node = self._node.combine(other._node, "relation_ratio")
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
                if not matches:
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


class _MaterializedValue(_Value):
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

    def show(self, *, max_output_bytes: int | None = None) -> None:
        """Show bounded contract facts and a preview of the exact committed result.

        Args:
            max_output_bytes: Optional bound for the displayed preview.
        Returns: None; prints a bounded result preview.
        Example: ``result = relation.show(max_output_bytes=max_output_bytes)``.
        Constraints: Reads only the exact committed result, redacts member keys,
            and bounds the combined output.
        """
        assert self._dataset is not None
        buffer = StringIO()
        with redirect_stdout(buffer):
            self.contract().show()
            self._dataset.show(max_output_bytes=max_output_bytes)
        limit = 8192 if max_output_bytes is None else builtins.min(8192, max_output_bytes)
        print(
            buffer.getvalue()
            .encode("utf-8")[: builtins.max(0, limit - 1)]
            .decode("utf-8", errors="ignore")
        )

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


class _CohortDomain(_Value):
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

    def each(self, grid: TimeGrid) -> LogicalTimeAnalysisDomain:
        """Bind the bounded product of these members and one time grid.

        Args: grid: Finite grid constructed with mv.time_grid().
        Returns: A LogicalTimeAnalysisDomain retaining every member/time cell.
        Example: ``product = members.each(grid)``.
        Constraints: One time axis only; report and certified boundary authorities bind exactly.
        """
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
        return LogicalTimeAnalysisDomain(
            _TOKEN, self._node.each(bound), self._runtime, inputs=(self,)
        )

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
        via: Ref[RelationshipKind] | RootRoutes | None = None,
    ) -> LogicalNumericRelation: ...

    @overload
    def read(
        self,
        field: Ref[DimensionKind],
        *,
        at: datetime | BeforeEndBoundary | GridEndpoint | None = None,
        via: Ref[RelationshipKind] | RootRoutes | None = None,
    ) -> LogicalCategoryRelation | LogicalBooleanRelation: ...

    @overload
    def read(
        self,
        field: Ref[TimeDimensionKind],
        *,
        at: datetime | BeforeEndBoundary | GridEndpoint | None = None,
        via: Ref[RelationshipKind] | RootRoutes | None = None,
    ) -> LogicalTemporalRelation: ...

    def read(
        self,
        field: Ref[MeasureKind] | Ref[DimensionKind] | Ref[TimeDimensionKind],
        *,
        at: datetime | BeforeEndBoundary | GridEndpoint | None = None,
        via: Ref[RelationshipKind] | RootRoutes | None = None,
    ) -> (
        LogicalNumericRelation
        | LogicalCategoryRelation
        | LogicalBooleanRelation
        | LogicalTemporalRelation
    ):
        """Read one typed attribute for every current complete member identity.

        Args:
            field: Declared Measure, Dimension or TimeDimension Ref.
            at: Independent aware attribute instant or this product grid endpoint; None for unversioned fields.
            via: Exact single-valued member-to-owner relationship or route.
        Returns: Numeric, Category, Boolean or Temporal relation according to field kind.
        Example: ``values = members.read(field, at=scope.before_end)``.
        Constraints: Missing coverage, multivalued mappings and foreign grid endpoints reject. before_end is a symbolic left limit.
        """
        point: datetime | BeforeEndBoundary | GridPoint | None = (
            at if not isinstance(at, GridEndpoint) else None
        )
        if isinstance(at, GridEndpoint):
            bound = at._grid._bound
            if bound is None or bound != self._node.root.signature.domain.time_grid:
                raise _reject(
                    "the receiver's grid endpoint",
                    "foreign or unbound grid",
                    "Use the same grid as each(grid).",
                )
            point = GridPoint(bound, at._side)
        node = self._node.read(field, at=point, via=via)
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
        Example: ``result = members.group_by(region).observe(metric, during=window, via=buyer)``.
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
            member_source=self,
            categories=tuple(categories),
            target_groups=groups,
        )

    def observe(
        self,
        metric: MetricInputValue,
        *,
        during: TimeScope | GridWindow | None = None,
        at: datetime | GridEndpoint | None = None,
        via: Ref[RelationshipKind] | RootRoutes | None = None,
        coordinates: tuple[Ref[DimensionKind], ...] = (),
    ) -> LogicalNumericRelation | LogicalRatioRelation:
        """Observe one governed Metric or runtime expression over this member domain.

        Args:
            metric: Declared Metric Ref or closed runtime Metric expression to observe.
            during: Fixed TimeScope, the exact grid.window, or None for no added restriction.
            at: Explicit cumulative endpoint, bound grid endpoint or aware datetime.
            via: Admitted relationship Ref or ordered routes; omit only for the same Entity root.
            coordinates: Optional declared contribution coordinate Dimension Refs.
        Returns: A LogicalNumericRelation | LogicalRatioRelation bound to this exact relation.
        Example: ``result = relation.observe(metric, during=during, via=via, coordinates=coordinates)``.
        Constraints: The Metric, window, path, and member binding must be admitted.
        """
        point: datetime | GridPoint | None = at if not isinstance(at, GridEndpoint) else None
        if isinstance(at, GridEndpoint):
            bound_point = at._grid._bound
            if bound_point is None or bound_point != self._node.root.signature.domain.time_grid:
                raise _reject(
                    "the receiver's grid endpoint",
                    "foreign or unbound grid",
                    "Use the same grid as each(grid).",
                )
            point = GridPoint(bound_point, at._side)
        live = self._node._live()
        if isinstance(during, GridWindow):
            bound = during._grid._bound
            if bound is None or bound != self._node.root.signature.domain.time_grid:
                raise _reject(
                    "the receiver's grid.window",
                    "foreign or unbound grid",
                    "Use the same grid as members.each(grid).",
                )
        window = bound if isinstance(during, GridWindow) else during
        declared = (
            via.routes if isinstance(via, RootRoutesValue) else (via,) if via is not None else ()
        )
        for route in declared:
            if isinstance(route, RootRouteValue):
                relationship = normalize_target_relationship(
                    live.graph.registry, route.through[0].path
                )
                if relationship.from_entity_ref.path != route.root.path:
                    raise _reject(
                        "the declared contribution root",
                        route.root.path,
                        "Bind the exact route root.",
                    )
        paths = tuple(
            route.through if isinstance(route, RootRouteValue) else (route,) for route in declared
        ) or ((),)
        if self._node.resolves_multiple_components(metric):
            observed = self._node.observe_routes(
                metric, during=window, at=point, paths=paths, coordinates=coordinates
            )
            if (
                observed.root.signature.quantity is not None
                and observed.root.signature.quantity.method_version == "linear@v1"
            ):
                return LogicalNumericRelation(_TOKEN, observed, self._runtime, inputs=(self,))
            return LogicalRatioRelation(_TOKEN, observed, self._runtime, inputs=(self,))
        if len(paths) != 1:
            raise _reject(
                "one route for the single contribution root",
                f"{len(paths)} routes",
                "Pass exactly the route of the observed contribution root.",
            )
        single = paths[0]
        observed = (
            self._node.observe(
                metric, during=window, at=point, via=single[0], coordinates=coordinates
            )
            if len(single) == 1
            else self._node.observe(
                metric, during=window, at=point, via=single, coordinates=coordinates
            )
        )
        if coordinates:
            subject = next(p for p in observed.root.signature.parts if isinstance(p, SubjectPart))
            observed = observed.rollup(subject.entity_ref, *coordinates)
        return LogicalNumericRelation(_TOKEN, observed, self._runtime, inputs=(self,))


class MaterializedAnalysisDomain(_MaterializedValue, _CohortDomain):
    """Exact fixed Entity membership; it cannot introduce a new live observation."""


class LogicalTimeAnalysisDomain(LogicalAnalysisDomain):
    """Unexecuted bounded Entity/time product with exact grid authority."""

    def execute(self) -> MaterializedTimeAnalysisDomain:
        """Publish the complete member/time product through the unified graph.

        Args: None.
        Returns: A MaterializedTimeAnalysisDomain with retained temporal authority.
        Example: ``result = members.each(grid).execute()``.
        Constraints: Source evaluation retains empty time cells and exact member identities.
        """
        return MaterializedTimeAnalysisDomain(
            _TOKEN, self._node, self._runtime, dataset=self._run()
        )


class MaterializedTimeAnalysisDomain(MaterializedAnalysisDomain):
    """Committed member/time product with retained grid identity."""


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
        member_source: LogicalAnalysisDomain | None = None,
        categories: tuple[LogicalCategoryRelation | LogicalSelectedCategoryRelation, ...] = (),
        target_groups: LogicalAnalysisDomain
        | GroupedAnalysisDomain
        | MaterializedAnalysisDomain
        | None = None,
        row_node: Relation | None = None,
    ) -> None:
        if target_groups is not None:
            node = node.complete_groups(target_groups._node)
        super().__init__(token, node, runtime, inputs=inputs)
        self._member_source = member_source
        self._categories = categories
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
        if self._member_source is not None:
            names = (*names, "observe")
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

    def observe(
        self,
        metric: MetricInputValue,
        *,
        during: TimeScope | None = None,
        via: Ref[RelationshipKind] | RootRoutes,
    ) -> GroupedNumericRelation | GroupedRatioRelation:
        """Observe a Metric grouped by the bound member attribute.

        Args:
            metric: Declared Metric Ref to observe.
            during: Explicit fixed TimeScope for the observation.
            via: Admitted relationship Ref or closed route pair.
        Returns: A GroupedNumericRelation bound to this exact relation.
        Example: ``result = relation.observe(metric, during=during, via=via)``.
        Constraints: The Metric, window, path, and member binding must be admitted.
        """
        if self._member_source is not None:
            observed = self._member_source.observe(metric, during=during, via=via)
            return observed.group_by(*self._categories, groups=self._target_groups)
        if not isinstance(via, Ref):
            raise _reject(
                "a grouped member observation",
                "target-only group domain",
                "Group the member domain before observing multiple contribution roots.",
            )
        return GroupedNumericRelation(
            _TOKEN,
            self._node.observe(metric, during=during, via=via),
            self._runtime,
            inputs=(self,),
        )


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
        node = self._subject_members(through)
        if self._has_fixed():
            return LogicalFixedAnalysisDomain(_TOKEN, node, self._runtime, inputs=(self,))
        return LogicalAnalysisDomain(_TOKEN, node, self._runtime, inputs=(self,))

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
        node = self._subject_members(through)
        if self._has_fixed():
            return LogicalFixedAnalysisDomain(_TOKEN, node, self._runtime, inputs=(self,))
        return LogicalAnalysisDomain(_TOKEN, node, self._runtime, inputs=(self,))

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

        Args:
            None.
        Returns: A LogicalFixedAnalysisDomain bound to this exact relation.
        Example: ``result = relation.members()``.
        Constraints: Projects exact selected keys and cannot introduce a new source into fixed state.
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
        node = self._subject_members(through)
        if self._has_fixed():
            return LogicalFixedAnalysisDomain(_TOKEN, node, self._runtime, inputs=(self,))
        return LogicalAnalysisDomain(_TOKEN, node, self._runtime, inputs=(self,))

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
        Returns: A logical numeric selection preserving the Subject part.
        Example: ``selected = relation.where(relation.value.gt(0))``.
        Constraints: Numeric dependencies require exact corresponding keys; non-Defined predicates reject.
        """
        return LogicalSelectedNumericRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    def correlate(
        self,
        other: LogicalNumericRelation,
        *,
        method: Literal["spearman"] = "spearman",
    ) -> LogicalAssociationResult:
        """Construct the admitted same-Entity no-lag Spearman association.

        Args:
            other: Association endpoint over the same exact member implementation.
            method: Closed row statistic method or admitted correlation method.
        Returns: A LogicalAssociationResult bound to this exact relation.
        Example: ``result = relation.correlate(other, method=method)``.
        Constraints: Only same-member, no-lag Spearman is admitted.
        """
        if method != "spearman":
            raise _reject("spearman", str(method), "Use the qualified Spearman method.")
        return LogicalAssociationResult(
            _TOKEN, self._node.combine(other._node, "spearman"), self._runtime, inputs=(self, other)
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
        """
        return LogicalRolledNumericRelation(
            _TOKEN, self._node.rollup(), self._runtime, inputs=(self,)
        )

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Construct a new current-row sum, count or equal-row mean.

        Args:
            method: Closed row statistic method or admitted correlation method.
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        if not isinstance(method, RowMethod):
            raise _reject(
                "mv.sum/count/count_defined/min/max/mean()",
                type(method).__name__,
                "Select a closed row method.",
            )
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )

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
        Returns: A logical numeric selection preserving the Subject part.
        Example: ``selected = relation.where(relation.value.gt(0))``.
        Constraints: Numeric dependencies require exact corresponding keys; non-Defined predicates reject.
        """
        return LogicalSelectedNumericRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    def correlate(
        self, other: MaterializedNumericRelation, *, method: Literal["spearman"] = "spearman"
    ) -> LogicalAssociationResult:
        """Correlate exact retained observed endpoints when pairing is admitted.

        Args:
            other: Association endpoint over the same exact member implementation.
            method: Closed row statistic method or admitted correlation method.
        Returns: A LogicalAssociationResult bound to this exact relation.
        Example: ``result = relation.correlate(other, method=method)``.
        Constraints: Only same-member, no-lag Spearman is admitted.
        """
        if method != "spearman":
            raise _reject("spearman", str(method), "Use the qualified Spearman method.")
        return LogicalAssociationResult(
            _TOKEN, self._node.combine(other._node, "spearman"), self._runtime, inputs=(self, other)
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
            method: Closed row statistic method or admitted correlation method.
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        if not isinstance(method, RowMethod):
            raise _reject(
                "mv.sum/count/count_defined/min/max/mean()",
                type(method).__name__,
                "Select a closed row method.",
            )
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )


class MaterializedGroupedNumericRelation(MaterializedNumericRelation):
    """Fixed member or contribution group with retained Metric components."""

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Build a current-row statistic over this exact group.

        Args:
            method: Closed row statistic method or admitted correlation method.
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
            method: Closed row statistic method or admitted correlation method.
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        if not isinstance(method, RowMethod):
            raise _reject(
                "mv.sum/count/count_defined/min/max/mean()",
                type(method).__name__,
                "Select a closed row method.",
            )
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )

    def execute(self) -> MaterializedRolledNumericRelation:
        """Evaluate and publish this original-state Singleton.

        Args:
            None.
        Returns: A MaterializedRolledNumericRelation bound to this exact relation.
        Example: ``result = relation.execute()``.
        Constraints: A source branch reevaluates; a fixed branch uses exact retained Artifacts.
        """
        return MaterializedRolledNumericRelation(
            _TOKEN, self._node, self._runtime, dataset=self._run()
        )


class MaterializedRolledNumericRelation(_MaterializedValue, _OriginalContinuation):
    """Fixed original-state Singleton observation."""

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Build a fixed-only statistic over this Singleton row.

        Args:
            method: Closed row statistic method or admitted correlation method.
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        if not isinstance(method, RowMethod):
            raise _reject(
                "mv.sum/count/count_defined/min/max/mean()",
                type(method).__name__,
                "Select a closed row method.",
            )
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )


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
            method: Closed row statistic method or admitted correlation method.
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        if not isinstance(method, RowMethod):
            raise _reject(
                "mv.sum/count/count_defined/min/max/mean()",
                type(method).__name__,
                "Select a closed row method.",
            )
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )

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
            method: Closed row statistic method or admitted correlation method.
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        if not isinstance(method, RowMethod):
            raise _reject(
                "mv.sum/count/count_defined/min/max/mean()",
                type(method).__name__,
                "Select a closed row method.",
            )
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )


class LogicalRolledRatioRelation(_OriginalContinuation):
    """Unexecuted ratio reobserved from original numerator and denominator state."""

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Calculate a statistic over current rolled ratio rows.

        Args:
            method: Closed row statistic method or admitted correlation method.
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        if not isinstance(method, RowMethod):
            raise _reject(
                "mv.sum/count/count_defined/min/max/mean()",
                type(method).__name__,
                "Select a closed row method.",
            )
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )

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
            method: Closed row statistic method or admitted correlation method.
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        if not isinstance(method, RowMethod):
            raise _reject(
                "mv.sum/count/count_defined/min/max/mean()",
                type(method).__name__,
                "Select a closed row method.",
            )
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )


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
            method: Closed row statistic method or admitted correlation method.
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        if not isinstance(method, RowMethod):
            raise _reject(
                "mv.sum/count/count_defined/min/max/mean()",
                type(method).__name__,
                "Select a closed row method.",
            )
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )

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
            method: Closed row statistic method or admitted correlation method.
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        if not isinstance(method, RowMethod):
            raise _reject(
                "mv.sum/count/count_defined/min/max/mean()",
                type(method).__name__,
                "Select a closed row method.",
            )
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )


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
        node = self._subject_members(through)
        if self._has_fixed():
            return LogicalFixedAnalysisDomain(_TOKEN, node, self._runtime, inputs=(self,))
        return LogicalAnalysisDomain(_TOKEN, node, self._runtime, inputs=(self,))

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Calculate a statistic over selected current rows.

        Args:
            method: Closed row statistic method or admitted correlation method.
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        if not isinstance(method, RowMethod):
            raise _reject(
                "mv.sum/count/count_defined/min/max/mean()",
                type(method).__name__,
                "Select a closed row method.",
            )
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )

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

        Args:
            None.
        Returns: A LogicalFixedAnalysisDomain bound to this exact relation.
        Example: ``result = relation.members()``.
        Constraints: Projects exact selected keys and cannot introduce a new source into fixed state.
        """
        return LogicalFixedAnalysisDomain(
            _TOKEN, self._subject_members(through), self._runtime, inputs=(self,)
        )

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Build a fixed-only statistic over selected rows.

        Args:
            method: Closed row statistic method or admitted correlation method.
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        if not isinstance(method, RowMethod):
            raise _reject(
                "mv.sum/count/count_defined/min/max/mean()",
                type(method).__name__,
                "Select a closed row method.",
            )
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )


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
        *keys: Ref[DimensionKind] | Ref[EntityKind],
        groups: GroupedAnalysisDomain | MaterializedAnalysisDomain | None = None,
    ) -> GroupedStatisticRelation:
        """Select retained axes for a partial row-state merge.

        Args:
            keys: Retained complete coordinates to preserve.
            groups: Optional explicit target including valid empty groups.
        Returns: A grouped statistic awaiting rollup.
        Example: ``result = statistic.group_by(dimension).rollup().execute()``.
        Constraints: Merges this statistic's state; does not summarize finished values.
        """
        node = self._node.rollup_statistic(*keys)
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
            method: Closed row statistic method or admitted correlation method.
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        if not isinstance(method, RowMethod):
            raise _reject(
                "mv.sum/count/count_defined/min/max/mean()",
                type(method).__name__,
                "Select a closed row method.",
            )
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )


class LogicalCoefficientSelectionRelation(_Value):
    """Unexecuted strict coefficient selection."""

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Calculate a statistic over the selected coefficient.

        Args:
            method: Closed row statistic method or admitted correlation method.
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        if not isinstance(method, RowMethod):
            raise _reject(
                "mv.sum/count/count_defined/min/max/mean()",
                type(method).__name__,
                "Select a closed row method.",
            )
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )

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


class MaterializedCoefficientSelectionRelation(_MaterializedValue):
    """Fixed strict coefficient selection."""

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Build a fixed-only statistic over the selected coefficient.

        Args:
            method: Closed row statistic method or admitted correlation method.
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        if not isinstance(method, RowMethod):
            raise _reject(
                "mv.sum/count/count_defined/min/max/mean()",
                type(method).__name__,
                "Select a closed row method.",
            )
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )


class LogicalAssociationResult(_Value):
    """Unexecuted same-Entity Spearman result."""

    def execute(self) -> MaterializedAssociationResult:
        """Evaluate and publish this Association with paired-state evidence.

        Args:
            None.
        Returns: A MaterializedAssociationResult bound to this exact relation.
        Example: ``result = relation.execute()``.
        Constraints: A source branch reevaluates; a fixed branch uses exact retained Artifacts.
        """
        return MaterializedAssociationResult(_TOKEN, self._node, self._runtime, dataset=self._run())


class MaterializedAssociationResult(_MaterializedValue):
    """Fixed Spearman Association with its coefficient view and pair counts."""

    @property
    def coefficient(self) -> MaterializedCoefficientRelation:
        """Return the coefficient relation bound to this exact Association Artifact.

        Args:
            None.
        Returns: A coefficient view of the exact Association.
        Example: ``result = relation.coefficient``.
        Constraints: Uses the exact retained Association Artifact.
        """
        return MaterializedCoefficientRelation(
            _TOKEN, self._node, self._runtime, dataset=self._dataset
        )


def wrap_materialized(
    node: Relation, runtime: DatasetRuntime, dataset: GraphDataset
) -> PublicMaterialized:
    """Restore the existing public result variant from its checked typed graph."""
    kind = _kind(node)
    if kind in ("members", "group") and node.root.signature.domain.time_grid is not None:
        return MaterializedTimeAnalysisDomain(_TOKEN, node, runtime, dataset=dataset)
    if kind in ("members", "group"):
        return MaterializedAnalysisDomain(_TOKEN, node, runtime, dataset=dataset)
    if kind == "read":
        params = node.definition.parameters
        assert isinstance(params, BindProject)
        if params.ref.kind is SemanticKind.MEASURE:
            return MaterializedNumericRelation(_TOKEN, node, runtime, dataset=dataset)
        if params.ref.kind is SemanticKind.TIME_DIMENSION:
            return MaterializedTemporalRelation(_TOKEN, node, runtime, dataset=dataset)
        if node.root.value_type == ScalarType("boolean"):
            return MaterializedBooleanRelation(_TOKEN, node, runtime, dataset=dataset)
        return MaterializedCategoryRelation(_TOKEN, node, runtime, dataset=dataset)
    if kind == "where":
        if node.root.signature.quantity is None:
            scalar = node.root.value_type
            if scalar == ScalarType("boolean"):
                return MaterializedSelectedBooleanRelation(_TOKEN, node, runtime, dataset=dataset)
            if scalar in (ScalarType("date"), ScalarType("timestamp")):
                return MaterializedSelectedTemporalRelation(_TOKEN, node, runtime, dataset=dataset)
            params = node.definition.parameters
            if isinstance(params, PartsTransport) and params.field_kind == "measure":
                return MaterializedSelectedNumericRelation(_TOKEN, node, runtime, dataset=dataset)
            return MaterializedSelectedCategoryRelation(_TOKEN, node, runtime, dataset=dataset)
        if (
            isinstance(node.root.signature.quantity, (ObservedQuantity, RolledQuantity))
            or node.root.signature.quantity.method_version == "cell.ratio@v1"
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
    if kind in ("observe", "rollup"):
        if node.root.signature.domain.kind == "group":
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
        node = self._subject_members(through)
        if self._has_fixed():
            return LogicalFixedAnalysisDomain(_TOKEN, node, self._runtime, inputs=(self,))
        return LogicalAnalysisDomain(_TOKEN, node, self._runtime, inputs=(self,))

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
        node = self._subject_members(through)
        if self._has_fixed():
            return LogicalFixedAnalysisDomain(_TOKEN, node, self._runtime, inputs=(self,))
        return LogicalAnalysisDomain(_TOKEN, node, self._runtime, inputs=(self,))

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

        Args:
            None.
        Returns: A LogicalFixedAnalysisDomain bound to this exact relation.
        Example: ``result = relation.members()``.
        Constraints: Projects exact selected keys and cannot introduce a new source into fixed state.
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
        node = self._subject_members(through)
        if self._has_fixed():
            return LogicalFixedAnalysisDomain(_TOKEN, node, self._runtime, inputs=(self,))
        return LogicalAnalysisDomain(_TOKEN, node, self._runtime, inputs=(self,))

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

    def members(
        self, *, through: SubjectBinding | None = None
    ) -> LogicalAnalysisDomain | LogicalFixedAnalysisDomain:
        """Project complete Subject identities from this relation.

        Args: through: Optional exact producer-owned SubjectBinding; Entity projection is implicit.
        Returns: A source member domain or a fixed-only member continuation.
        Example: ``selected_members = relation.members()``.
        Constraints: Requires a retained total Subject map; fixed inputs cannot introduce sources.
        """
        node = self._subject_members(through)
        if self._has_fixed():
            return LogicalFixedAnalysisDomain(_TOKEN, node, self._runtime, inputs=(self,))
        return LogicalAnalysisDomain(_TOKEN, node, self._runtime, inputs=(self,))

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

        Args:
            None.
        Returns: A LogicalFixedAnalysisDomain bound to this exact relation.
        Example: ``result = relation.members()``.
        Constraints: Projects exact selected keys and cannot introduce a new source into fixed state.
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
        Returns: A logical numeric selection preserving the Subject part.
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
        node = self._subject_members(through)
        if self._has_fixed():
            return LogicalFixedAnalysisDomain(_TOKEN, node, self._runtime, inputs=(self,))
        return LogicalAnalysisDomain(_TOKEN, node, self._runtime, inputs=(self,))

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
    """Fixed numeric selection with an exact retained member projection."""

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
        Returns: A logical numeric selection preserving the Subject part.
        Example: ``selected = relation.where(relation.value.gt(0))``.
        Constraints: Numeric dependencies require exact corresponding keys; non-Defined predicates reject.
        """
        return LogicalSelectedNumericRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    def members(self, *, through: SubjectBinding | None = None) -> LogicalFixedAnalysisDomain:
        """Project selected fixed member identity without a source read.

        Args:
            None.
        Returns: A LogicalFixedAnalysisDomain bound to this exact relation.
        Example: ``result = relation.members()``.
        Constraints: Projects exact selected keys and cannot introduce a new source into fixed state.
        """
        return LogicalFixedAnalysisDomain(
            _TOKEN, self._subject_members(through), self._runtime, inputs=(self,)
        )


PublicMaterialized: TypeAlias = (
    MaterializedBooleanRelation
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
    | MaterializedAssociationResult
)


NumericRelation: TypeAlias = (
    LogicalNumericRelation
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
