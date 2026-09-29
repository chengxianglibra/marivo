"""Existing public Analysis receivers over one typed graph Runtime and Store 7."""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from inspect import Parameter, signature
from io import StringIO
from typing import TYPE_CHECKING, Literal, NoReturn, TypeAlias, overload

import pandas as pd

from marivo._temporal import BeforeEndBoundary, TimeScope
from marivo.analysis.core.graph import FixedLeaf
from marivo.analysis.core.model import (
    DerivedQuantity,
    ObservedQuantity,
    RolledQuantity,
    RowStatisticQuantity,
    part_role,
)
from marivo.analysis.core.predicates import TemporalLiteral, ValuePredicate
from marivo.analysis.core.rules import (
    AssociationScore,
    BindProject,
    CellDerive,
    MapCorrespond,
    PartsTransport,
    RowState,
)
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization.graph_dataset import GraphDataset
from marivo.analysis.materialization.graph_fields import (
    BooleanField,
    CategoryField,
    CategoryPredicate,
    NumericField,
    NumericPredicate,
    RootRoutesValue,
    RootRouteValue,
    ScalarPredicate,
    TemporalField,
    root_route,
    root_routes,
)
from marivo.analysis.materialization.graph_relation import FrozenBinding, Relation
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
from marivo.semantic.runtime_metric import RuntimeMetricExpr
from marivo.semantic.validator import normalize_target_relationship

if TYPE_CHECKING:
    from marivo.analysis.datasets.state import MaterializedDatasetState
    from marivo.analysis.materialization.admission import DatasetRuntime

RootRoute: TypeAlias = RootRouteValue
RootRoutes: TypeAlias = RootRoutesValue
MetricInputValue: TypeAlias = Ref[MetricKind] | RuntimeMetricExpr
_TOKEN = object()


def _reject(expected: str, received: str, repair: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected=expected, received=received, repair=repair, location="analysis.dsl"
    )


@dataclass(frozen=True, slots=True)
class RowMethod:
    """Closed current-row statistic selected by the caller."""

    kind: Literal["sum", "count", "mean"]

    def __post_init__(self) -> None:
        if self.kind not in ("sum", "count", "mean"):
            raise _reject("sum, count or mean", str(self.kind), "Use mv.sum/count/mean().")


def sum() -> RowMethod:
    """Select the current-row sum method.

    Args: None.
    Returns: A closed RowMethod value.
    Example: ``result = relation.summarize(mv.sum())``.
    Constraints: Sum uses the relation's current rows and rejects unsupported Cells.
    """
    return RowMethod("sum")


def count() -> RowMethod:
    """Select the current-row count method.

    Args: None.
    Returns: A closed RowMethod value.
    Example: ``result = relation.summarize(mv.count())``.
    Constraints: Count includes current rows with non-Defined Cells.
    """
    return RowMethod("count")


def mean() -> RowMethod:
    """Select the equal-current-row mean method.

    Args: None.
    Returns: A closed RowMethod value.
    Example: ``result = relation.summarize(mv.mean())``.
    Constraints: Non-Defined participating Cells reject the strict mean.
    """
    return RowMethod("mean")


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
    if isinstance(params, RowState):
        return "summarize"
    if isinstance(params, AssociationScore):
        return "correlate"
    if isinstance(params, CellDerive):
        return "compare"
    if isinstance(params, BindProject):
        return "read"
    if isinstance(params, MapCorrespond):
        return "group" if params.mode == "group" else "members"
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
                ()
                if fixed
                else ("execute",)
                if self._has_fixed()
                else ("read", "group_by", "observe", "execute")
            )
        elif kind == "read":
            names = ("where", "members") if fixed else ("where", "members", "group_by", "execute")
        elif kind == "group":
            names = ("observe",)
        elif kind == "correlate":
            names = ("coefficient",) if fixed else ("execute",)
        elif kind == "correlate_where":
            names = ("summarize",)
        elif kind == "compare":
            names = ("where", "summarize")
        elif kind == "where":
            names = ("members", "summarize") if signature.quantity is not None else ("members",)
        elif kind in ("observe", "ratio_observe", "rollup", "ratio_rollup"):
            names = (
                *(("group_by",) if "coordinate_state" in roles else ()),
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
        if not fixed and kind not in ("members", "read", "group", "correlate"):
            names = (*names, "execute")
        if self._node.root.value_type != ScalarType("int64"):
            names = tuple(name for name in names if name != "compare")
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

    def _select(
        self, predicate: CategoryPredicate | NumericPredicate | ScalarPredicate
    ) -> Relation:
        if (
            not isinstance(predicate, (CategoryPredicate, NumericPredicate, ScalarPredicate))
            or predicate.root is not self._node.root
        ):
            raise _reject(
                "a predicate on this exact relation",
                "foreign predicate",
                "Build it from this relation.value.",
            )
        if isinstance(predicate, ScalarPredicate):
            value = ValuePredicate(
                self._node.root.signature.domain.binding,
                predicate.operator,
                TemporalLiteral(
                    "timestamp" if isinstance(predicate.value, datetime) else "date",
                    predicate.value.isoformat(),
                )
                if isinstance(predicate.value, date)
                else predicate.value,
            )
        elif isinstance(predicate, CategoryPredicate):
            value = ValuePredicate(self._node.root.signature.domain.binding, "eq", predicate.value)
        else:
            operations: dict[str, Literal["lt", "le", "gt", "ge", "eq"]] = {
                "lt": "lt",
                "lte": "le",
                "gt": "gt",
                "gte": "ge",
                "eq": "eq",
            }
            value = ValuePredicate(
                self._node.root.signature.domain.binding,
                operations[predicate.operation],
                predicate.threshold,
            )
        return self._node.where(value)

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
                f"{parameter.name}={parameter.name}"
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
        limit = 8192 if max_output_bytes is None else min(8192, max_output_bytes)
        print(
            buffer.getvalue().encode("utf-8")[: max(0, limit - 1)].decode("utf-8", errors="ignore")
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


class LogicalAnalysisDomain(_Value):
    """Unexecuted governed Entity membership and its selected subdomains."""

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
        at: datetime | BeforeEndBoundary | None = None,
        via: Ref[RelationshipKind] | RootRoutes | None = None,
    ) -> LogicalNumericRelation: ...

    @overload
    def read(
        self,
        field: Ref[DimensionKind],
        *,
        at: datetime | BeforeEndBoundary | None = None,
        via: Ref[RelationshipKind] | RootRoutes | None = None,
    ) -> LogicalCategoryRelation | LogicalBooleanRelation: ...

    @overload
    def read(
        self,
        field: Ref[TimeDimensionKind],
        *,
        at: datetime | BeforeEndBoundary | None = None,
        via: Ref[RelationshipKind] | RootRoutes | None = None,
    ) -> LogicalTemporalRelation: ...

    def read(
        self,
        field: Ref[MeasureKind] | Ref[DimensionKind] | Ref[TimeDimensionKind],
        *,
        at: datetime | BeforeEndBoundary | None = None,
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
            at: Independent explicit attribute version; None for unversioned fields.
            via: Exact single-valued member-to-owner relationship or route.
        Returns: Numeric, Category, Boolean or Temporal relation according to field kind.
        Example: ``values = members.read(field, at=scope.before_end)``.
        Constraints: Missing coverage, multivalued mappings and mixed fixed/source inputs reject.
        """
        node = self._node.read(field, at=at, via=via)
        if field.kind is SemanticKind.MEASURE:
            return LogicalNumericRelation(_TOKEN, node, self._runtime, inputs=(self,))
        if field.kind is SemanticKind.TIME_DIMENSION:
            return LogicalTemporalRelation(_TOKEN, node, self._runtime, inputs=(self,))
        if node.root.value_type == ScalarType("boolean"):
            return LogicalBooleanRelation(_TOKEN, node, self._runtime, inputs=(self,))
        return LogicalCategoryRelation(_TOKEN, node, self._runtime, inputs=(self,))

    def group_by(self, dimension: Ref[DimensionKind]) -> GroupedAnalysisDomain:
        """Group current members by their declared categorical Dimension.

        Args:
            dimension: Declared Dimension Ref for the current member or contribution domain.
        Returns: A GroupedAnalysisDomain bound to this exact relation.
        Example: ``result = relation.group_by(dimension)``.
        Constraints: Only declared member Dimensions or retained coordinates are admitted.
        """
        return GroupedAnalysisDomain(
            _TOKEN, self._node.group_members(dimension), self._runtime, inputs=(self,)
        )

    def observe(
        self,
        metric: MetricInputValue,
        *,
        during: TimeScope | None = None,
        via: Ref[RelationshipKind] | RootRoutes,
        coordinates: tuple[Ref[DimensionKind], ...] = (),
    ) -> LogicalNumericRelation | LogicalRatioRelation:
        """Observe one governed Metric or runtime expression over this member domain.

        Args:
            metric: Declared Metric Ref or closed runtime Metric expression to observe.
            during: Explicit fixed TimeScope, or None for no added time restriction.
            via: Admitted relationship Ref or an ordered closed route list.
            coordinates: Optional declared contribution coordinate Dimension Refs.
        Returns: A LogicalNumericRelation | LogicalRatioRelation bound to this exact relation.
        Example: ``result = relation.observe(metric, during=during, via=via, coordinates=coordinates)``.
        Constraints: The Metric, window, path, and member binding must be admitted.
        """
        live = self._node._live()
        declared = via.routes if isinstance(via, RootRoutesValue) else (via,)
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
        )
        if self._node.resolves_multiple_components(metric):
            observed = self._node.observe_routes(
                metric, during=during, paths=paths, coordinates=coordinates
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
            self._node.observe(metric, during=during, via=single[0], coordinates=coordinates)
            if len(single) == 1
            else self._node.observe(metric, during=during, via=single, coordinates=coordinates)
        )
        return LogicalNumericRelation(_TOKEN, observed, self._runtime, inputs=(self,))


class MaterializedAnalysisDomain(_MaterializedValue):
    """Exact fixed Entity membership; it cannot introduce a new live observation."""


class LogicalFixedAnalysisDomain(_Value):
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

    def observe(
        self, metric: Ref[MetricKind], *, during: TimeScope, via: Ref[RelationshipKind]
    ) -> GroupedNumericRelation:
        """Observe a Metric grouped by the bound member attribute.

        Args:
            metric: Declared Metric Ref to observe.
            during: Explicit fixed TimeScope for the observation.
            via: Admitted relationship Ref or closed route pair.
        Returns: A GroupedNumericRelation bound to this exact relation.
        Example: ``result = relation.observe(metric, during=during, via=via)``.
        Constraints: The Metric, window, path, and member binding must be admitted.
        """
        return GroupedNumericRelation(
            _TOKEN,
            self._node.observe(metric, during=during, via=via),
            self._runtime,
            inputs=(self,),
        )


class LogicalCategoryRelation(_Value):
    """Unexecuted categorical member attribute relation."""

    def members(self) -> LogicalAnalysisDomain | LogicalFixedAnalysisDomain:
        """Project complete Subject identities from this relation.

        Args: None.
        Returns: A source member domain or a fixed-only member continuation.
        Example: ``selected_members = relation.members()``.
        Constraints: Requires a retained total Subject map; fixed inputs cannot introduce sources.
        """
        node = self._node.selected_members()
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
        return CategoryField(self._node.root)

    def where(self, predicate: CategoryPredicate) -> LogicalSelectedCategoryRelation:
        """Select rows using a predicate bound to this category relation.

        Args:
            predicate: Predicate bound to this relation's exact value field.
        Returns: A LogicalSelectedCategoryRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: The predicate must be bound to this exact relation field.
        """
        return LogicalSelectedCategoryRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    def group_by(self) -> GroupedAnalysisDomain:
        """Group by this bound categorical value.

        Args:
            None.
        Returns: A GroupedAnalysisDomain bound to this exact relation.
        Example: ``result = relation.group_by()``.
        Constraints: Only declared member Dimensions or retained coordinates are admitted.
        """
        return GroupedAnalysisDomain(_TOKEN, self._node.group_read(), self._runtime, inputs=(self,))

    def execute(self) -> MaterializedCategoryRelation:
        """Evaluate and publish this categorical relation.

        Args:
            None.
        Returns: A MaterializedCategoryRelation bound to this exact relation.
        Example: ``result = relation.execute()``.
        Constraints: A source branch reevaluates; a fixed branch uses exact retained Artifacts.
        """
        return MaterializedCategoryRelation(_TOKEN, self._node, self._runtime, dataset=self._run())


class MaterializedCategoryRelation(_MaterializedValue):
    """Fixed categorical relation retaining admitted member identity."""

    def members(self) -> LogicalFixedAnalysisDomain:
        """Project retained complete Subject identities without source access.

        Args: None.
        Returns: A fixed-only logical member continuation.
        Example: ``members = result.members()``.
        Constraints: Requires the exact retained Subject part and cannot read external attributes.
        """
        return LogicalFixedAnalysisDomain(
            _TOKEN, self._node.selected_members(), self._runtime, inputs=(self,)
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
        return CategoryField(self._node.root)

    def where(self, predicate: CategoryPredicate) -> LogicalSelectedCategoryRelation:
        """Build a fixed-only categorical selection.

        Args:
            predicate: Predicate bound to this relation's exact value field.
        Returns: A LogicalSelectedCategoryRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: The predicate must be bound to this exact relation field.
        """
        return LogicalSelectedCategoryRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )


class LogicalSelectedCategoryRelation(_Value):
    """Unexecuted category selection over one exact read relation."""

    def members(self) -> LogicalAnalysisDomain | LogicalFixedAnalysisDomain:
        """Project complete Subject identities from this relation.

        Args: None.
        Returns: A source member domain or a fixed-only member continuation.
        Example: ``selected_members = relation.members()``.
        Constraints: Requires a retained total Subject map; fixed inputs cannot introduce sources.
        """
        node = self._node.selected_members()
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


class MaterializedSelectedCategoryRelation(_MaterializedValue):
    """Fixed categorical selection with an exact retained member projection."""

    def members(self) -> LogicalFixedAnalysisDomain:
        """Project selected fixed member identity without a source read.

        Args:
            None.
        Returns: A LogicalFixedAnalysisDomain bound to this exact relation.
        Example: ``result = relation.members()``.
        Constraints: Projects exact selected keys and cannot introduce a new source into fixed state.
        """
        return LogicalFixedAnalysisDomain(
            _TOKEN, self._node.selected_members(), self._runtime, inputs=(self,)
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
    ) -> None:
        super().__init__(token, grouped, runtime, inputs=inputs)

    def contract(self) -> AnalysisContract:
        """Report the grouped observation's one admitted continuation.

        Args:
            None.
        Returns: An AnalysisContract describing the current shape and valid continuations.
        Example: ``result = relation.contract()``.
        Constraints: This reads local contract metadata and does not execute a source.
        """
        return replace(super().contract(), actions=self._action_contract(("execute",)))

    def execute(self) -> MaterializedGroupedNumericRelation:
        """Publish the contribution-coordinate group with its original state.

        Args:
            None.
        Returns: A MaterializedGroupedNumericRelation bound to this exact relation.
        Example: ``result = relation.execute()``.
        Constraints: A source branch reevaluates; a fixed branch uses exact retained Artifacts.
        """
        return MaterializedGroupedNumericRelation(
            _TOKEN, self._node, self._runtime, dataset=self._run()
        )


class GroupedRatioRelation(_Value):
    """Grouped original ratio components awaiting a target-domain rollup."""

    __slots__ = ()

    def __init__(
        self,
        token: object,
        grouped: Relation,
        runtime: DatasetRuntime,
        *,
        inputs: tuple[_Value, ...],
    ) -> None:
        super().__init__(token, grouped, runtime, inputs=inputs)

    def contract(self) -> AnalysisContract:
        """Report the ratio group's one admitted continuation.

        Args:
            None.
        Returns: An AnalysisContract describing the current shape and valid continuations.
        Example: ``result = relation.contract()``.
        Constraints: This reads local contract metadata and does not execute a source.
        """
        return replace(super().contract(), actions=self._action_contract(("rollup",)))

    def rollup(self) -> LogicalRolledRatioRelation:
        """Merge original numerator and denominator state by the selected coordinate.

        Args:
            None.
        Returns: A LogicalRolledRatioRelation bound to this exact relation.
        Example: ``result = relation.rollup()``.
        Constraints: Requires original retained components; subgroup values are not averaged.
        """
        return LogicalRolledRatioRelation(_TOKEN, self._node, self._runtime, inputs=(self,))


class LogicalNumericRelation(_Value):
    """One unexecuted original Metric observation over an Entity domain."""

    def members(self) -> LogicalAnalysisDomain | LogicalFixedAnalysisDomain:
        """Project complete Subject identities from this relation.

        Args: None.
        Returns: A source member domain or a fixed-only member continuation.
        Example: ``selected_members = relation.members()``.
        Constraints: Requires a retained total Subject map; fixed inputs cannot introduce sources.
        """
        node = self._node.selected_members()
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
        return NumericField(self._node.root)

    def where(self, predicate: NumericPredicate) -> LogicalSelectedNumericRelation:
        """Select current numeric values using their bound predicate.

        Args: predicate: A comparison built from this relation.value.
        Returns: A logical numeric selection preserving the Subject part.
        Example: ``selected = relation.where(relation.value.gt(0))``.
        Constraints: Foreign predicates and non-Defined inputs reject.
        """
        return LogicalSelectedNumericRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    def compare(self, baseline: LogicalNumericRelation) -> LogicalDifferenceRelation:
        """Construct an exact same-member absolute time difference.

        Args:
            baseline: Comparison endpoint over the same exact member implementation.
        Returns: A LogicalDifferenceRelation bound to this exact relation.
        Example: ``result = relation.compare(baseline)``.
        Constraints: Endpoints need the same member, Metric, route, and distinct windows.
        """
        return LogicalDifferenceRelation(
            _TOKEN,
            self._node.combine(baseline._node, "difference"),
            self._runtime,
            inputs=(self, baseline),
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

    def group_by(self, dimension: Ref[DimensionKind]) -> GroupedNumericRelation:
        """Select one already retained contribution coordinate for rollup.

        Args:
            dimension: Declared Dimension Ref for the current member or contribution domain.
        Returns: A GroupedNumericRelation bound to this exact relation.
        Example: ``result = relation.group_by(dimension)``.
        Constraints: Only declared member Dimensions or retained coordinates are admitted.
        """
        return GroupedNumericRelation(
            _TOKEN, self._node.rollup(dimension), self._runtime, inputs=(self,)
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
                "mv.sum/count/mean()", type(method).__name__, "Select a closed row method."
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


class MaterializedNumericRelation(_MaterializedValue):
    """Exact fixed original Metric observation with retained components."""

    def members(self) -> LogicalFixedAnalysisDomain:
        """Project retained complete Subject identities without source access.

        Args: None.
        Returns: A fixed-only logical member continuation.
        Example: ``members = result.members()``.
        Constraints: Requires the exact retained Subject part and cannot read external attributes.
        """
        return LogicalFixedAnalysisDomain(
            _TOKEN, self._node.selected_members(), self._runtime, inputs=(self,)
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
        return NumericField(self._node.root)

    def where(self, predicate: NumericPredicate) -> LogicalSelectedNumericRelation:
        """Select current numeric values using their bound predicate.

        Args: predicate: A comparison built from this relation.value.
        Returns: A logical numeric selection preserving the Subject part.
        Example: ``selected = relation.where(relation.value.gt(0))``.
        Constraints: Foreign predicates and non-Defined inputs reject.
        """
        return LogicalSelectedNumericRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )

    def compare(self, baseline: MaterializedNumericRelation) -> LogicalDifferenceRelation:
        """Compare exact retained observed endpoints after binding checks.

        Args:
            baseline: Comparison endpoint over the same exact member implementation.
        Returns: A LogicalDifferenceRelation bound to this exact relation.
        Example: ``result = relation.compare(baseline)``.
        Constraints: Endpoints need the same member, Metric, route, and distinct windows.
        """
        return LogicalDifferenceRelation(
            _TOKEN,
            self._node.combine(baseline._node, "difference"),
            self._runtime,
            inputs=(self, baseline),
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

    def group_by(self, dimension: Ref[DimensionKind]) -> GroupedNumericRelation:
        """Group one coordinate retained by this fixed observation.

        Args:
            dimension: Declared Dimension Ref for the current member or contribution domain.
        Returns: A GroupedNumericRelation bound to this exact relation.
        Example: ``result = relation.group_by(dimension)``.
        Constraints: Only declared member Dimensions or retained coordinates are admitted.
        """
        return GroupedNumericRelation(
            _TOKEN, self._node.rollup(dimension), self._runtime, inputs=(self,)
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
                "mv.sum/count/mean()", type(method).__name__, "Select a closed row method."
            )
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )


class MaterializedGroupedNumericRelation(_MaterializedValue):
    """Fixed member or contribution group with retained Metric components."""

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Build a current-row statistic over this exact group.

        Args:
            method: Closed row statistic method or admitted correlation method.
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        if not isinstance(method, RowMethod):
            raise _reject(
                "mv.sum/count/mean()", type(method).__name__, "Select a closed row method."
            )
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )


class LogicalRolledNumericRelation(_Value):
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
                "mv.sum/count/mean()", type(method).__name__, "Select a closed row method."
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


class MaterializedRolledNumericRelation(_MaterializedValue):
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
                "mv.sum/count/mean()", type(method).__name__, "Select a closed row method."
            )
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )


class LogicalRatioRelation(_Value):
    """Unexecuted ratio observation with its original component state."""

    def group_by(self, dimension: Ref[DimensionKind]) -> GroupedRatioRelation:
        """Group one already retained contribution coordinate.

        Args:
            dimension: Declared Dimension Ref for the current member or contribution domain.
        Returns: A GroupedRatioRelation bound to this exact relation.
        Example: ``result = relation.group_by(dimension)``.
        Constraints: Only declared member Dimensions or retained coordinates are admitted.
        """
        return GroupedRatioRelation(
            _TOKEN, self._node.rollup(dimension), self._runtime, inputs=(self,)
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
                "mv.sum/count/mean()", type(method).__name__, "Select a closed row method."
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


class MaterializedRatioRelation(_MaterializedValue):
    """Fixed ratio observation with retained numerator and denominator parts."""

    def group_by(self, dimension: Ref[DimensionKind]) -> GroupedRatioRelation:
        """Group one retained contribution coordinate.

        Args:
            dimension: Declared Dimension Ref for the current member or contribution domain.
        Returns: A GroupedRatioRelation bound to this exact relation.
        Example: ``result = relation.group_by(dimension)``.
        Constraints: Only declared member Dimensions or retained coordinates are admitted.
        """
        return GroupedRatioRelation(
            _TOKEN, self._node.rollup(dimension), self._runtime, inputs=(self,)
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
                "mv.sum/count/mean()", type(method).__name__, "Select a closed row method."
            )
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )


class LogicalRolledRatioRelation(_Value):
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
                "mv.sum/count/mean()", type(method).__name__, "Select a closed row method."
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


class MaterializedRolledRatioRelation(_MaterializedValue):
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
                "mv.sum/count/mean()", type(method).__name__, "Select a closed row method."
            )
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )


class LogicalDifferenceRelation(_Value):
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
        return NumericField(self._node.root)

    def where(self, predicate: NumericPredicate) -> LogicalSelectedDifferenceRelation:
        """Select Defined Difference rows through this exact field.

        Args:
            predicate: Predicate bound to this relation's exact value field.
        Returns: A LogicalSelectedDifferenceRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: The predicate must be bound to this exact relation field.
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
                "mv.sum/count/mean()", type(method).__name__, "Select a closed row method."
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


class MaterializedDifferenceRelation(_MaterializedValue):
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
        return NumericField(self._node.root)

    def where(self, predicate: NumericPredicate) -> LogicalSelectedDifferenceRelation:
        """Build a fixed-only Difference selection.

        Args:
            predicate: Predicate bound to this relation's exact value field.
        Returns: A LogicalSelectedDifferenceRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: The predicate must be bound to this exact relation field.
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
                "mv.sum/count/mean()", type(method).__name__, "Select a closed row method."
            )
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )


class LogicalSelectedDifferenceRelation(_Value):
    """Unexecuted selected Difference with admitted member projection."""

    def members(self) -> LogicalAnalysisDomain | LogicalFixedAnalysisDomain:
        """Project complete Subject identities from this relation.

        Args: None.
        Returns: A source member domain or a fixed-only member continuation.
        Example: ``selected_members = relation.members()``.
        Constraints: Requires a retained total Subject map; fixed inputs cannot introduce sources.
        """
        node = self._node.selected_members()
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
                "mv.sum/count/mean()", type(method).__name__, "Select a closed row method."
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


class MaterializedSelectedDifferenceRelation(_MaterializedValue):
    """Fixed selected Difference with exact member projection."""

    def members(self) -> LogicalFixedAnalysisDomain:
        """Project fixed selected members without source access.

        Args:
            None.
        Returns: A LogicalFixedAnalysisDomain bound to this exact relation.
        Example: ``result = relation.members()``.
        Constraints: Projects exact selected keys and cannot introduce a new source into fixed state.
        """
        return LogicalFixedAnalysisDomain(
            _TOKEN, self._node.selected_members(), self._runtime, inputs=(self,)
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
                "mv.sum/count/mean()", type(method).__name__, "Select a closed row method."
            )
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )


class LogicalStatisticRelation(_Value):
    """Unexecuted current-row statistic with no original-state rollup."""

    def execute(self) -> MaterializedStatisticRelation:
        """Evaluate and publish this current-row statistic.

        Args:
            None.
        Returns: A MaterializedStatisticRelation bound to this exact relation.
        Example: ``result = relation.execute()``.
        Constraints: A source branch reevaluates; a fixed branch uses exact retained Artifacts.
        """
        return MaterializedStatisticRelation(_TOKEN, self._node, self._runtime, dataset=self._run())


class MaterializedStatisticRelation(_MaterializedValue):
    """Fixed terminal current-row statistic."""


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
        return NumericField(self._node.root)

    def where(self, predicate: NumericPredicate) -> LogicalCoefficientSelectionRelation:
        """Select a Defined coefficient on this Association.

        Args:
            predicate: Predicate bound to this relation's exact value field.
        Returns: A LogicalCoefficientSelectionRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: The predicate must be bound to this exact relation field.
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
                "mv.sum/count/mean()", type(method).__name__, "Select a closed row method."
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
                "mv.sum/count/mean()", type(method).__name__, "Select a closed row method."
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
                "mv.sum/count/mean()", type(method).__name__, "Select a closed row method."
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
    if kind == "members":
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
        return MaterializedSelectedDifferenceRelation(_TOKEN, node, runtime, dataset=dataset)
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


class LogicalBooleanRelation(_Value):
    """Unexecuted boolean member attribute relation."""

    def members(self) -> LogicalAnalysisDomain | LogicalFixedAnalysisDomain:
        """Project complete Subject identities from this relation.

        Args: None.
        Returns: A source member domain or a fixed-only member continuation.
        Example: ``selected_members = relation.members()``.
        Constraints: Requires a retained total Subject map; fixed inputs cannot introduce sources.
        """
        node = self._node.selected_members()
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
        return BooleanField(self._node.root)

    def where(self, predicate: ScalarPredicate) -> LogicalSelectedBooleanRelation:
        """Select rows using a predicate bound to this boolean relation.

        Args:
            predicate: Predicate bound to this relation's exact value field.
        Returns: A LogicalSelectedBooleanRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: The predicate must be bound to this exact relation field.
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


class MaterializedBooleanRelation(_MaterializedValue):
    """Fixed boolean relation retaining admitted member identity."""

    def members(self) -> LogicalFixedAnalysisDomain:
        """Project retained complete Subject identities without source access.

        Args: None.
        Returns: A fixed-only logical member continuation.
        Example: ``members = result.members()``.
        Constraints: Requires the exact retained Subject part and cannot read external attributes.
        """
        return LogicalFixedAnalysisDomain(
            _TOKEN, self._node.selected_members(), self._runtime, inputs=(self,)
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
        return BooleanField(self._node.root)

    def where(self, predicate: ScalarPredicate) -> LogicalSelectedBooleanRelation:
        """Build a fixed-only boolean selection.

        Args:
            predicate: Predicate bound to this relation's exact value field.
        Returns: A LogicalSelectedBooleanRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: The predicate must be bound to this exact relation field.
        """
        return LogicalSelectedBooleanRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )


class LogicalSelectedBooleanRelation(_Value):
    """Unexecuted boolean selection over one exact read relation."""

    def members(self) -> LogicalAnalysisDomain | LogicalFixedAnalysisDomain:
        """Project complete Subject identities from this relation.

        Args: None.
        Returns: A source member domain or a fixed-only member continuation.
        Example: ``selected_members = relation.members()``.
        Constraints: Requires a retained total Subject map; fixed inputs cannot introduce sources.
        """
        node = self._node.selected_members()
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


class MaterializedSelectedBooleanRelation(_MaterializedValue):
    """Fixed boolean selection with an exact retained member projection."""

    def members(self) -> LogicalFixedAnalysisDomain:
        """Project selected fixed member identity without a source read.

        Args:
            None.
        Returns: A LogicalFixedAnalysisDomain bound to this exact relation.
        Example: ``result = relation.members()``.
        Constraints: Projects exact selected keys and cannot introduce a new source into fixed state.
        """
        return LogicalFixedAnalysisDomain(
            _TOKEN, self._node.selected_members(), self._runtime, inputs=(self,)
        )


class LogicalTemporalRelation(_Value):
    """Unexecuted temporal member attribute relation."""

    def members(self) -> LogicalAnalysisDomain | LogicalFixedAnalysisDomain:
        """Project complete Subject identities from this relation.

        Args: None.
        Returns: A source member domain or a fixed-only member continuation.
        Example: ``selected_members = relation.members()``.
        Constraints: Requires a retained total Subject map; fixed inputs cannot introduce sources.
        """
        node = self._node.selected_members()
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
        return TemporalField(self._node.root)

    def where(self, predicate: ScalarPredicate) -> LogicalSelectedTemporalRelation:
        """Select rows using a predicate bound to this temporal relation.

        Args:
            predicate: Predicate bound to this relation's exact value field.
        Returns: A LogicalSelectedTemporalRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: The predicate must be bound to this exact relation field.
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


class MaterializedTemporalRelation(_MaterializedValue):
    """Fixed temporal relation retaining admitted member identity."""

    def members(self) -> LogicalFixedAnalysisDomain:
        """Project retained complete Subject identities without source access.

        Args: None.
        Returns: A fixed-only logical member continuation.
        Example: ``members = result.members()``.
        Constraints: Requires the exact retained Subject part and cannot read external attributes.
        """
        return LogicalFixedAnalysisDomain(
            _TOKEN, self._node.selected_members(), self._runtime, inputs=(self,)
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
        return TemporalField(self._node.root)

    def where(self, predicate: ScalarPredicate) -> LogicalSelectedTemporalRelation:
        """Build a fixed-only temporal selection.

        Args:
            predicate: Predicate bound to this relation's exact value field.
        Returns: A LogicalSelectedTemporalRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: The predicate must be bound to this exact relation field.
        """
        return LogicalSelectedTemporalRelation(
            _TOKEN, self._select(predicate), self._runtime, inputs=(self,)
        )


class LogicalSelectedTemporalRelation(_Value):
    """Unexecuted temporal selection over one exact read relation."""

    def members(self) -> LogicalAnalysisDomain | LogicalFixedAnalysisDomain:
        """Project complete Subject identities from this relation.

        Args: None.
        Returns: A source member domain or a fixed-only member continuation.
        Example: ``selected_members = relation.members()``.
        Constraints: Requires a retained total Subject map; fixed inputs cannot introduce sources.
        """
        node = self._node.selected_members()
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


class MaterializedSelectedTemporalRelation(_MaterializedValue):
    """Fixed temporal selection with an exact retained member projection."""

    def members(self) -> LogicalFixedAnalysisDomain:
        """Project selected fixed member identity without a source read.

        Args:
            None.
        Returns: A LogicalFixedAnalysisDomain bound to this exact relation.
        Example: ``result = relation.members()``.
        Constraints: Projects exact selected keys and cannot introduce a new source into fixed state.
        """
        return LogicalFixedAnalysisDomain(
            _TOKEN, self._node.selected_members(), self._runtime, inputs=(self,)
        )


class LogicalSelectedNumericRelation(_Value):
    """Unexecuted numeric selection over one exact read relation."""

    def members(self) -> LogicalAnalysisDomain | LogicalFixedAnalysisDomain:
        """Project complete Subject identities from this relation.

        Args: None.
        Returns: A source member domain or a fixed-only member continuation.
        Example: ``selected_members = relation.members()``.
        Constraints: Requires a retained total Subject map; fixed inputs cannot introduce sources.
        """
        node = self._node.selected_members()
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


class MaterializedSelectedNumericRelation(_MaterializedValue):
    """Fixed numeric selection with an exact retained member projection."""

    def members(self) -> LogicalFixedAnalysisDomain:
        """Project selected fixed member identity without a source read.

        Args:
            None.
        Returns: A LogicalFixedAnalysisDomain bound to this exact relation.
        Example: ``result = relation.members()``.
        Constraints: Projects exact selected keys and cannot introduce a new source into fixed state.
        """
        return LogicalFixedAnalysisDomain(
            _TOKEN, self._node.selected_members(), self._runtime, inputs=(self,)
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
