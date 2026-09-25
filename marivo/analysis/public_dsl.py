"""Typed first-round Analysis DSL over the existing J1 Runtime and Store."""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import dataclass, field, replace
from inspect import Parameter, signature
from io import StringIO
from typing import TYPE_CHECKING, Literal, NoReturn, TypeAlias, overload

import pandas as pd

from marivo._temporal import TimeScope
from marivo.analysis.datasets.descriptors import (
    _DifferenceQuantity,
    _ObservedQuantity,
    _RowStatisticQuantity,
)
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization.dsl_j1_artifact import J1Node
from marivo.analysis.observation.dsl_j1 import (
    J1CategoryField,
    J1Difference,
    J1Group,
    J1Members,
    J1NumericField,
    J1NumericPredicate,
    J1Observed,
    J1Predicate,
    J1Read,
    J1SelectedCategory,
    J1SelectedDifference,
    J1Statistic,
    J3Grouped,
    J3Observed,
    J3Route,
    J3Routes,
    J4Association,
    J4CoefficientSelection,
    J4CoefficientStatistic,
    j3_route,
    j3_routes,
)
from marivo.refs import DimensionKind, EntityKind, MetricKind, Ref, RelationshipKind

if TYPE_CHECKING:
    from marivo.analysis.datasets.state import MaterializedDatasetState
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.observation.dsl_j1_dataset import MaterializedJ1Dataset

RootRoute: TypeAlias = J3Route
RootRoutes: TypeAlias = J3Routes
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
    return j3_route(root, through=through)


def routes(*items: RootRoute) -> RootRoutes:
    """Bind two ordered contribution routes for an admitted ratio.

    Args: items: Exactly two RootRoute values for distinct contribution roots.
    Returns: A closed RootRoutes value for ``observe(..., via=...)``.
    Example: ``pair = mv.routes(line_route, order_route)``.
    Constraints: Route order follows the Metric's declared component order.
    """
    return j3_routes(*items)


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


def _kind(node: J1Node) -> str:
    return node.root.shape_id.local_shape_id


def _actions(node: J1Node, *, fixed: bool) -> tuple[str, ...]:
    if isinstance(node, J1Members):
        return () if fixed else ("read", "group_by", "observe", "execute")
    if isinstance(node, J1Read):
        return ("where",) if fixed else ("where", "group_by", "execute")
    if isinstance(node, J1SelectedCategory):
        return ("members",) if fixed else ("members", "execute")
    if isinstance(node, J1Group):
        return ("observe",) if not fixed else ()
    if isinstance(node, (J1Observed, J3Observed)):
        if node.domain.kind != "entity":
            return ("summarize",) if fixed else ("summarize", "execute")
        methods: tuple[str, ...] = ("group_by", "rollup", "summarize")
        if not fixed:
            methods = (*methods, "execute")
        return (*methods, "compare", "correlate") if isinstance(node, J1Observed) else methods
    if isinstance(node, J1Difference):
        return ("where", "summarize") if fixed else ("where", "summarize", "execute")
    if isinstance(node, J1SelectedDifference):
        return ("members", "summarize") if fixed else ("members", "summarize", "execute")
    if isinstance(node, J4Association):
        return ("coefficient",) if fixed else ("execute",)
    if isinstance(node, (J4CoefficientSelection, J4CoefficientStatistic, J1Statistic)):
        if isinstance(node, J4CoefficientSelection):
            return ("summarize",) if fixed else ("summarize", "execute")
        return () if fixed else ("execute",)
    return () if fixed else ("execute",)


class _Value:
    __slots__ = ("_dataset", "_inputs", "_node", "_runtime")

    def __init__(
        self,
        token: object,
        node: J1Node,
        runtime: DatasetRuntime,
        *,
        inputs: tuple[_Value, ...] = (),
        dataset: MaterializedJ1Dataset | None = None,
    ) -> None:
        if token is not _TOKEN:
            raise TypeError(
                "Construct Analysis values through a Session or another Analysis value."
            )
        self._node = node
        self._runtime = runtime
        self._inputs = inputs
        self._dataset = dataset

    def contract(self) -> AnalysisContract:
        """Return current, non-executing kind and valid continuation names.

        Args:
            None.
        Returns: An AnalysisContract describing the current shape and valid continuations.
        Example: ``result = relation.contract()``.
        Constraints: This reads local contract metadata and does not execute a source.
        """
        from marivo.analysis.materialization.dsl_j1_artifact import _meaning

        fixed = self._dataset is not None
        domain, quantity = _meaning(self._node)
        retained_parts: tuple[str, ...] = ()
        if self._dataset is not None:
            record = self._runtime.store.artifact(self._dataset.state.artifact_ref.ref)
            if record is not None:
                retained_parts = tuple(
                    sorted(part.role for part in record.descriptor.retained_parts)
                )
        required_parts = (
            quantity.required_parts
            if isinstance(quantity, (_ObservedQuantity, _RowStatisticQuantity, _DifferenceQuantity))
            else ()
        )
        names = (
            ("execute",)
            if not fixed and self._has_fixed() and isinstance(self._node, J1Members)
            else _actions(self._node, fixed=fixed)
        )
        if fixed and not set(required_parts).issubset(retained_parts):
            names = ()
        return AnalysisContract(
            _kind(self._node),
            "materialized" if fixed else "logical",
            self._action_contract(names),
            domain.kind,
            None if quantity is None else quantity.kind,
            required_parts,
            retained_parts,
            self._contract_facts(),
        )

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

    def _contract_facts(self) -> tuple[tuple[str, str], ...]:
        node = self._node
        facts: list[tuple[str, str]] = []
        if isinstance(node, (J1Observed, J3Observed)):
            unit = "not declared" if node.plan.unit is None else node.plan.unit
            observation_unit = {
                "entity": "one Entity member",
                "group": "one current group",
                "singleton": "one selected domain",
            }[node.domain.kind]
            facts.extend(
                (
                    ("metric", node.metric.path),
                    ("method", node.plan.method),
                    ("unit", unit),
                    ("null_policy", node.plan.null_rule),
                    ("empty_policy", node.plan.empty_rule),
                    ("statistical_unit", observation_unit),
                    ("source_assumption", "governed member Entity and explicit observation scope"),
                )
            )
            if node.plan.method == "ratio":
                facts.append(("weighting", "original numerator and denominator components"))
        elif isinstance(node, J1Difference):
            facts.extend(
                (
                    ("metric", node.current.metric.path),
                    (
                        "unit",
                        "not declared"
                        if node.current.plan.unit is None
                        else node.current.plan.unit,
                    ),
                    ("method", "ordered absolute difference"),
                    ("statistical_unit", "one paired Entity member"),
                )
            )
        elif isinstance(node, J1Statistic):
            facts.extend((("method", node.method), ("statistical_unit", "one current row")))
            if node.method == "mean":
                facts.append(("weighting", "equal current rows"))
        elif isinstance(node, J4Association):
            facts.extend(
                (
                    ("method", "spearman"),
                    ("statistical_unit", "one complete Entity member pair"),
                    ("source_assumption", "same members and observation scope"),
                )
            )
        if self._dataset is not None:
            facts.append(("rows", str(self._dataset.state.realized_row_count)))
            cell_fields = tuple(
                column.name for column in self._dataset.schema.columns if column.role_id == "cell"
            )
            if cell_fields:
                facts.append(("cell_state_fields", ",".join(cell_fields[:4])))
            if set(self._contract_required_parts()).difference(self._contract_retained_parts()):
                facts.append(("continuation", "required retained components unavailable"))
            facts.append(("source", "exact retained Artifact; no source reconnect"))
        else:
            facts.append(("source", "logical definition; no business rows read"))
        return tuple(facts)

    def _contract_required_parts(self) -> tuple[str, ...]:
        from marivo.analysis.materialization.dsl_j1_artifact import _meaning

        _, quantity = _meaning(self._node)
        return (
            quantity.required_parts
            if isinstance(quantity, (_ObservedQuantity, _RowStatisticQuantity, _DifferenceQuantity))
            else ()
        )

    def _contract_retained_parts(self) -> tuple[str, ...]:
        if self._dataset is None:
            return ()
        record = self._runtime.store.artifact(self._dataset.state.artifact_ref.ref)
        return (
            () if record is None else tuple(part.role for part in record.descriptor.retained_parts)
        )

    def __repr__(self) -> str:
        identity = (
            self._dataset.state.artifact_ref.ref[:40]
            if self._dataset is not None
            else self._node.root.definition_fingerprint[:22]
        )
        detail = ".show()" if self._dataset is not None else ".contract().show()"
        return f"<{type(self).__name__} kind={_kind(self._node)} id={identity}; use {detail}>"

    def _is_live(self) -> bool:
        return self._dataset is None and (
            not self._inputs or any(item._is_live() for item in self._inputs)
        )

    def _has_fixed(self) -> bool:
        return self._dataset is not None or any(item._has_fixed() for item in self._inputs)

    def _run(self) -> MaterializedJ1Dataset:
        if self._dataset is not None:
            return self._dataset
        if self._is_live() and self._has_fixed():
            raise _reject(
                "source-only or fixed-only inputs",
                "mixed live and materialized dependencies",
                "Keep the member and observation graph logical, or use only exact saved Artifacts.",
            )
        from marivo.analysis.materialization.dsl_public_snapshot import encode_public_node

        snapshot = encode_public_node(self._node)
        if self._is_live():
            from marivo.analysis.materialization.dsl_public_source import public_j1_source

            return self._runtime.execute_j1(
                self._node,
                source=lambda: public_j1_source(self._node, str(self._runtime.store.project_root)),
                public_snapshot=snapshot,
            )
        if len(self._inputs) == 1:
            parent = self._inputs[0]
            saved = parent._run()
            return self._runtime.execute_j1(
                self._node,
                input_node=parent._node,
                input_artifact_ref=saved.state.artifact_ref.ref,
                public_snapshot=snapshot,
            )
        if len(self._inputs) == 2:
            left, right = self._inputs
            left_saved, right_saved = left._run(), right._run()
            return self._runtime.execute_j1(
                self._node,
                input_nodes=(left._node, right._node),
                input_artifact_refs=(
                    left_saved.state.artifact_ref.ref,
                    right_saved.state.artifact_ref.ref,
                ),
                public_snapshot=snapshot,
            )
        raise _reject(
            "one or two retained predecessors",
            "unbound continuation",
            "Rebuild the relation from exact inputs.",
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

    def read(self, dimension: Ref[DimensionKind]) -> LogicalCategoryRelation:
        """Read an admitted single-valued categorical member Dimension.

        Args:
            dimension: Declared Dimension Ref for the current member or contribution domain.
        Returns: A LogicalCategoryRelation bound to this exact relation.
        Example: ``result = relation.read(dimension)``.
        Constraints: The Dimension must be declared and single valued.
        """
        if self._has_fixed():
            raise _reject(
                "logical source members",
                "fixed selected members",
                "Read before materializing the selection.",
            )
        if not isinstance(self._node, J1Members):
            raise _reject("Entity members", _kind(self._node), "Select a member domain first.")
        return LogicalCategoryRelation(
            _TOKEN, self._node.read(dimension), self._runtime, inputs=(self,)
        )

    def group_by(self, dimension: Ref[DimensionKind]) -> GroupedAnalysisDomain:
        """Group current members by their declared categorical Dimension.

        Args:
            dimension: Declared Dimension Ref for the current member or contribution domain.
        Returns: A GroupedAnalysisDomain bound to this exact relation.
        Example: ``result = relation.group_by(dimension)``.
        Constraints: Only declared member Dimensions or retained coordinates are admitted.
        """
        if self._has_fixed():
            raise _reject(
                "logical source members",
                "fixed selected members",
                "Group before materializing the selection.",
            )
        if not isinstance(self._node, J1Members):
            raise _reject("Entity members", _kind(self._node), "Select a member domain first.")
        return GroupedAnalysisDomain(
            _TOKEN, self._node.group_by(dimension), self._runtime, inputs=(self,)
        )

    @overload
    def observe(
        self,
        metric: Ref[MetricKind],
        *,
        during: TimeScope,
        via: Ref[RelationshipKind],
        coordinates: tuple[Ref[DimensionKind], ...] = (),
    ) -> LogicalNumericRelation: ...

    @overload
    def observe(
        self,
        metric: Ref[MetricKind],
        *,
        during: TimeScope,
        via: RootRoutes,
        coordinates: tuple[Ref[DimensionKind], ...] = (),
    ) -> LogicalRatioRelation: ...

    def observe(
        self,
        metric: Ref[MetricKind],
        *,
        during: TimeScope,
        via: Ref[RelationshipKind] | RootRoutes,
        coordinates: tuple[Ref[DimensionKind], ...] = (),
    ) -> LogicalNumericRelation | LogicalRatioRelation:
        """Observe one governed Metric over this logical member domain.

        Args:
            metric: Declared Metric Ref to observe.
            during: Explicit fixed TimeScope for the observation.
            via: Admitted relationship Ref or closed route pair.
            coordinates: Optional declared contribution coordinate Dimension Refs.
        Returns: A LogicalNumericRelation | LogicalRatioRelation bound to this exact relation.
        Example: ``result = relation.observe(metric, during=during, via=via, coordinates=coordinates)``.
        Constraints: The Metric, window, path, and member binding must be admitted.
        """
        if self._has_fixed():
            raise _reject(
                "source-only or fixed-only inputs",
                "fixed selected members plus live Metric",
                "Observe before materializing the selection.",
            )
        if not isinstance(self._node, J1Members):
            raise _reject("Entity members", _kind(self._node), "Select a member domain first.")
        observed = self._node.observe(metric, during=during, via=via, coordinates=coordinates)
        if isinstance(observed, J3Observed):
            return LogicalRatioRelation(_TOKEN, observed, self._runtime, inputs=(self,))
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
        if not isinstance(self._node, J1Group):
            raise _reject("member grouping", _kind(self._node), "Group members by a Dimension.")
        return GroupedNumericRelation(
            _TOKEN,
            self._node.observe(metric, during=during, via=via),
            self._runtime,
            inputs=(self,),
        )


class LogicalCategoryRelation(_Value):
    """Unexecuted categorical member attribute relation."""

    @property
    def value(self) -> J1CategoryField:
        """Return this relation's bound categorical field.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        if not isinstance(self._node, J1Read):
            raise _reject(
                "category read", _kind(self._node), "Use read() before selecting a category."
            )
        return self._node.value

    def where(self, predicate: J1Predicate) -> LogicalSelectedCategoryRelation:
        """Select rows using a predicate bound to this category relation.

        Args:
            predicate: Predicate bound to this relation's exact value field.
        Returns: A LogicalSelectedCategoryRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: The predicate must be bound to this exact relation field.
        """
        if not isinstance(self._node, J1Read):
            raise _reject(
                "category read", _kind(self._node), "Build the predicate from this relation.value."
            )
        selected = self._node.where(predicate)
        return LogicalSelectedCategoryRelation(_TOKEN, selected, self._runtime, inputs=(self,))

    def group_by(self) -> GroupedAnalysisDomain:
        """Group by this bound categorical value.

        Args:
            None.
        Returns: A GroupedAnalysisDomain bound to this exact relation.
        Example: ``result = relation.group_by()``.
        Constraints: Only declared member Dimensions or retained coordinates are admitted.
        """
        if not isinstance(self._node, J1Read):
            raise _reject(
                "category read", _kind(self._node), "Read the categorical Dimension first."
            )
        return GroupedAnalysisDomain(_TOKEN, self._node.group_by(), self._runtime, inputs=(self,))

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

    @property
    def value(self) -> J1CategoryField:
        """Return this relation's bound categorical field.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        if not isinstance(self._node, J1Read):
            raise _reject("category read", _kind(self._node), "Select the original read relation.")
        return self._node.value

    def where(self, predicate: J1Predicate) -> LogicalSelectedCategoryRelation:
        """Build a fixed-only categorical selection.

        Args:
            predicate: Predicate bound to this relation's exact value field.
        Returns: A LogicalSelectedCategoryRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: The predicate must be bound to this exact relation field.
        """
        if not isinstance(self._node, J1Read):
            raise _reject("category read", _kind(self._node), "Select the original read relation.")
        return LogicalSelectedCategoryRelation(
            _TOKEN, self._node.where(predicate), self._runtime, inputs=(self,)
        )


class LogicalSelectedCategoryRelation(_Value):
    """Unexecuted category selection over one exact read relation."""

    def members(self) -> LogicalAnalysisDomain:
        """Project selected logical member identity.

        Args:
            None.
        Returns: A LogicalAnalysisDomain bound to this exact relation.
        Example: ``result = relation.members()``.
        Constraints: Projects exact selected keys and cannot introduce a new source into fixed state.
        """
        if not isinstance(self._node, J1SelectedCategory):
            raise _reject("selected category", _kind(self._node), "Select category rows first.")
        return LogicalAnalysisDomain(_TOKEN, self._node.members(), self._runtime, inputs=(self,))

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
        if not isinstance(self._node, J1SelectedCategory):
            raise _reject("selected category", _kind(self._node), "Recover the selected category.")
        return LogicalFixedAnalysisDomain(
            _TOKEN, self._node.members(), self._runtime, inputs=(self,)
        )


class GroupedNumericRelation(_Value):
    """Logical contribution-coordinate group with original Metric state."""

    def __init__(
        self,
        token: object,
        grouped: J1Observed,
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

    __slots__ = ("_grouped",)

    def __init__(
        self,
        token: object,
        grouped: J3Grouped,
        runtime: DatasetRuntime,
        *,
        inputs: tuple[_Value, ...],
    ) -> None:
        self._grouped = grouped
        super().__init__(token, grouped.observed, runtime, inputs=inputs)

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
        return LogicalRolledRatioRelation(
            _TOKEN, self._grouped.rollup(), self._runtime, inputs=self._inputs
        )


class LogicalNumericRelation(_Value):
    """One unexecuted original Metric observation over an Entity domain."""

    @property
    def value(self) -> J1NumericField:
        """Return the bound numeric field for an admitted strict predicate.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        return J1NumericField(self._node.root)

    def compare(self, baseline: LogicalNumericRelation) -> LogicalDifferenceRelation:
        """Construct an exact same-member absolute time difference.

        Args:
            baseline: Comparison endpoint over the same exact member implementation.
        Returns: A LogicalDifferenceRelation bound to this exact relation.
        Example: ``result = relation.compare(baseline)``.
        Constraints: Endpoints need the same member, Metric, route, and distinct windows.
        """
        if not isinstance(self._node, J1Observed) or not isinstance(baseline._node, J1Observed):
            raise _reject(
                "two observed Metrics",
                "incompatible comparison",
                "Compare two same-member observations.",
            )
        return LogicalDifferenceRelation(
            _TOKEN, self._node.compare(baseline._node), self._runtime, inputs=(self, baseline)
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
        if not isinstance(self._node, J1Observed) or not isinstance(other._node, J1Observed):
            raise _reject(
                "two observed Metrics",
                "incompatible association",
                "Correlate two same-member observations.",
            )
        return LogicalAssociationResult(
            _TOKEN,
            self._node.correlate(other._node, method=method),
            self._runtime,
            inputs=(self, other),
        )

    def group_by(self, dimension: Ref[DimensionKind]) -> GroupedNumericRelation:
        """Select one already retained contribution coordinate for rollup.

        Args:
            dimension: Declared Dimension Ref for the current member or contribution domain.
        Returns: A GroupedNumericRelation bound to this exact relation.
        Example: ``result = relation.group_by(dimension)``.
        Constraints: Only declared member Dimensions or retained coordinates are admitted.
        """
        if isinstance(self._node, J1Observed):
            grouped = self._node.group_by(dimension)
            return GroupedNumericRelation(_TOKEN, grouped, self._runtime, inputs=(self,))
        raise _reject(
            "observed relation with retained coordinate",
            _kind(self._node),
            "Observe with coordinates first.",
        )

    def rollup(self) -> LogicalRolledNumericRelation:
        """Merge original Metric components into a Singleton result.

        Args:
            None.
        Returns: A LogicalRolledNumericRelation bound to this exact relation.
        Example: ``result = relation.rollup()``.
        Constraints: Requires original retained components; subgroup values are not averaged.
        """
        if not isinstance(self._node, J1Observed):
            raise _reject(
                "observation with original state",
                _kind(self._node),
                "Use summarize for current-row statistics.",
            )
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
                "mv.sum/count/mean()", type(method).__name__, "Select a closed current-row method."
            )
        node = self._node
        if isinstance(node, J1Observed):
            return LogicalStatisticRelation(
                _TOKEN, node.summarize(method.kind), self._runtime, inputs=(self,)
            )
        raise _reject(
            "numeric rows with admitted statistic", _kind(node), "Use a retained numeric relation."
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

    @property
    def value(self) -> J1NumericField:
        """Return the bound numeric field for a fixed-only predicate.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        return J1NumericField(self._node.root)

    def compare(self, baseline: MaterializedNumericRelation) -> LogicalDifferenceRelation:
        """Compare exact retained observed endpoints after binding checks.

        Args:
            baseline: Comparison endpoint over the same exact member implementation.
        Returns: A LogicalDifferenceRelation bound to this exact relation.
        Example: ``result = relation.compare(baseline)``.
        Constraints: Endpoints need the same member, Metric, route, and distinct windows.
        """
        if not isinstance(self._node, J1Observed) or not isinstance(baseline._node, J1Observed):
            raise _reject(
                "two observed Metrics", "incompatible comparison", "Compare retained observations."
            )
        return LogicalDifferenceRelation(
            _TOKEN, self._node.compare(baseline._node), self._runtime, inputs=(self, baseline)
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
        if not isinstance(self._node, J1Observed) or not isinstance(other._node, J1Observed):
            raise _reject(
                "two observed Metrics",
                "incompatible association",
                "Correlate retained observations.",
            )
        return LogicalAssociationResult(
            _TOKEN,
            self._node.correlate(other._node, method=method),
            self._runtime,
            inputs=(self, other),
        )

    def group_by(self, dimension: Ref[DimensionKind]) -> GroupedNumericRelation:
        """Group one coordinate retained by this fixed observation.

        Args:
            dimension: Declared Dimension Ref for the current member or contribution domain.
        Returns: A GroupedNumericRelation bound to this exact relation.
        Example: ``result = relation.group_by(dimension)``.
        Constraints: Only declared member Dimensions or retained coordinates are admitted.
        """
        if isinstance(self._node, J1Observed):
            return GroupedNumericRelation(
                _TOKEN, self._node.group_by(dimension), self._runtime, inputs=(self,)
            )
        raise _reject(
            "observed relation with retained coordinate",
            _kind(self._node),
            "Use an original observation.",
        )

    def rollup(self) -> LogicalRolledNumericRelation:
        """Build fixed-only original-state rollup.

        Args:
            None.
        Returns: A LogicalRolledNumericRelation bound to this exact relation.
        Example: ``result = relation.rollup()``.
        Constraints: Requires original retained components; subgroup values are not averaged.
        """
        if not isinstance(self._node, J1Observed):
            raise _reject(
                "observation with original state",
                _kind(self._node),
                "Use summarize for current rows.",
            )
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
        return LogicalStatisticRelation(
            _TOKEN, self._summarize_node(method), self._runtime, inputs=(self,)
        )

    def _summarize_node(self, method: RowMethod) -> J1Node:
        if not isinstance(method, RowMethod):
            raise _reject(
                "mv.sum/count/mean()", type(method).__name__, "Select a closed current-row method."
            )
        node = self._node
        if isinstance(node, J1Observed):
            return node.summarize(method.kind)
        raise _reject(
            "numeric rows with admitted statistic", _kind(node), "Use a retained numeric relation."
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
        if not isinstance(method, RowMethod) or not isinstance(self._node, J1Observed):
            raise _reject(
                "group observation and RowMethod", _kind(self._node), "Use mv.sum/count/mean()."
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
        if not isinstance(method, RowMethod) or not isinstance(self._node, J1Observed):
            raise _reject(
                "rolled observation and RowMethod", _kind(self._node), "Use mv.sum/count/mean()."
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
        if not isinstance(method, RowMethod) or not isinstance(self._node, J1Observed):
            raise _reject(
                "rolled observation and RowMethod", _kind(self._node), "Use mv.sum/count/mean()."
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
        if not isinstance(self._node, J3Observed):
            raise _reject("ratio observation", _kind(self._node), "Observe a governed ratio.")
        return GroupedRatioRelation(
            _TOKEN, self._node.group_by(dimension), self._runtime, inputs=(self,)
        )

    def rollup(self) -> LogicalRolledRatioRelation:
        """Reobserve the target domain from the original ratio components.

        Args:
            None.
        Returns: A LogicalRolledRatioRelation bound to this exact relation.
        Example: ``result = relation.rollup()``.
        Constraints: Requires original retained components; subgroup values are not averaged.
        """
        if not isinstance(self._node, J3Observed):
            raise _reject("ratio observation", _kind(self._node), "Observe a governed ratio.")
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
        if not isinstance(method, RowMethod) or not isinstance(self._node, J3Observed):
            raise _reject(
                "ratio observation and RowMethod", _kind(self._node), "Use mv.sum/count/mean()."
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
        if not isinstance(self._node, J3Observed):
            raise _reject("ratio observation", _kind(self._node), "Recover the original ratio.")
        return GroupedRatioRelation(
            _TOKEN, self._node.group_by(dimension), self._runtime, inputs=(self,)
        )

    def rollup(self) -> LogicalRolledRatioRelation:
        """Build a fixed-only original-component rollup.

        Args:
            None.
        Returns: A LogicalRolledRatioRelation bound to this exact relation.
        Example: ``result = relation.rollup()``.
        Constraints: Requires original retained components; subgroup values are not averaged.
        """
        if not isinstance(self._node, J3Observed):
            raise _reject("ratio observation", _kind(self._node), "Recover the original ratio.")
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
        if not isinstance(method, RowMethod) or not isinstance(self._node, J3Observed):
            raise _reject(
                "ratio observation and RowMethod", _kind(self._node), "Use mv.sum/count/mean()."
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
        if not isinstance(method, RowMethod) or not isinstance(self._node, J3Observed):
            raise _reject(
                "rolled ratio and RowMethod", _kind(self._node), "Use mv.sum/count/mean()."
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
        if not isinstance(method, RowMethod) or not isinstance(self._node, J3Observed):
            raise _reject(
                "rolled ratio and RowMethod", _kind(self._node), "Use mv.sum/count/mean()."
            )
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )


class LogicalDifferenceRelation(_Value):
    """Unexecuted exact same-member absolute Difference."""

    @property
    def value(self) -> J1NumericField:
        """Return the Difference-bound strict numeric field.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        return J1NumericField(self._node.root)

    def where(self, predicate: J1NumericPredicate) -> LogicalSelectedDifferenceRelation:
        """Select Defined Difference rows through this exact field.

        Args:
            predicate: Predicate bound to this relation's exact value field.
        Returns: A LogicalSelectedDifferenceRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: The predicate must be bound to this exact relation field.
        """
        if not isinstance(self._node, J1Difference):
            raise _reject("Difference", _kind(self._node), "Compare observations first.")
        return LogicalSelectedDifferenceRelation(
            _TOKEN, self._node.where(predicate), self._runtime, inputs=(self,)
        )

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Calculate a statistic over current Difference rows.

        Args:
            method: Closed row statistic method or admitted correlation method.
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        if not isinstance(method, RowMethod) or not isinstance(self._node, J1Difference):
            raise _reject("Difference and RowMethod", _kind(self._node), "Use mv.sum/count/mean().")
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
    def value(self) -> J1NumericField:
        """Return the bound strict numeric field.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        return J1NumericField(self._node.root)

    def where(self, predicate: J1NumericPredicate) -> LogicalSelectedDifferenceRelation:
        """Build a fixed-only Difference selection.

        Args:
            predicate: Predicate bound to this relation's exact value field.
        Returns: A LogicalSelectedDifferenceRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: The predicate must be bound to this exact relation field.
        """
        if not isinstance(self._node, J1Difference):
            raise _reject("Difference", _kind(self._node), "Recover the original Difference.")
        return LogicalSelectedDifferenceRelation(
            _TOKEN, self._node.where(predicate), self._runtime, inputs=(self,)
        )

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Build a fixed-only current-row statistic.

        Args:
            method: Closed row statistic method or admitted correlation method.
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        if not isinstance(method, RowMethod) or not isinstance(self._node, J1Difference):
            raise _reject("Difference and RowMethod", _kind(self._node), "Use mv.sum/count/mean().")
        return LogicalStatisticRelation(
            _TOKEN, self._node.summarize(method.kind), self._runtime, inputs=(self,)
        )


class LogicalSelectedDifferenceRelation(_Value):
    """Unexecuted selected Difference with admitted member projection."""

    def members(self) -> LogicalAnalysisDomain:
        """Project exact selected member identity.

        Args:
            None.
        Returns: A LogicalAnalysisDomain bound to this exact relation.
        Example: ``result = relation.members()``.
        Constraints: Projects exact selected keys and cannot introduce a new source into fixed state.
        """
        if not isinstance(self._node, J1SelectedDifference):
            raise _reject("selected Difference", _kind(self._node), "Select Difference rows first.")
        return LogicalAnalysisDomain(_TOKEN, self._node.members(), self._runtime, inputs=(self,))

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Calculate a statistic over selected current rows.

        Args:
            method: Closed row statistic method or admitted correlation method.
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        if not isinstance(method, RowMethod) or not isinstance(self._node, J1SelectedDifference):
            raise _reject(
                "selected Difference and RowMethod", _kind(self._node), "Use mv.sum/count/mean()."
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
        if not isinstance(self._node, J1SelectedDifference):
            raise _reject(
                "selected Difference", _kind(self._node), "Recover the selected Difference."
            )
        return LogicalFixedAnalysisDomain(
            _TOKEN, self._node.members(), self._runtime, inputs=(self,)
        )

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Build a fixed-only statistic over selected rows.

        Args:
            method: Closed row statistic method or admitted correlation method.
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        if not isinstance(method, RowMethod) or not isinstance(self._node, J1SelectedDifference):
            raise _reject(
                "selected Difference and RowMethod", _kind(self._node), "Use mv.sum/count/mean()."
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
    def value(self) -> J1NumericField:
        """Return the bound coefficient field.

        Args:
            None.
        Returns: The predicate field bound to this exact relation.
        Example: ``result = relation.value``.
        Constraints: Predicates built from this field remain bound to its relation.
        """
        return J1NumericField(self._node.root)

    def where(self, predicate: J1NumericPredicate) -> LogicalCoefficientSelectionRelation:
        """Select a Defined coefficient on this Association.

        Args:
            predicate: Predicate bound to this relation's exact value field.
        Returns: A LogicalCoefficientSelectionRelation bound to this exact relation.
        Example: ``result = relation.where(predicate)``.
        Constraints: The predicate must be bound to this exact relation field.
        """
        if not isinstance(self._node, J4Association):
            raise _reject("Association", _kind(self._node), "Recover the original Association.")
        return LogicalCoefficientSelectionRelation(
            _TOKEN, self._node.coefficient.where(predicate), self._runtime, inputs=(self,)
        )

    def summarize(self, method: RowMethod) -> LogicalStatisticRelation:
        """Calculate a statistic over current coefficient rows.

        Args:
            method: Closed row statistic method or admitted correlation method.
        Returns: A LogicalStatisticRelation bound to this exact relation.
        Example: ``result = relation.summarize(mv.mean())``.
        Constraints: Calculates over current rows using the selected Cell policy.
        """
        if not isinstance(method, RowMethod) or not isinstance(self._node, J4Association):
            raise _reject(
                "Association and RowMethod", _kind(self._node), "Use mv.sum/count/mean()."
            )
        return LogicalStatisticRelation(
            _TOKEN, self._node.coefficient.summarize(method.kind), self._runtime, inputs=(self,)
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
        if not isinstance(method, RowMethod) or not isinstance(self._node, J4CoefficientSelection):
            raise _reject(
                "coefficient selection and RowMethod", _kind(self._node), "Use mv.sum/count/mean()."
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
        if not isinstance(method, RowMethod) or not isinstance(self._node, J4CoefficientSelection):
            raise _reject(
                "coefficient selection and RowMethod", _kind(self._node), "Use mv.sum/count/mean()."
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
        if not isinstance(self._node, J4Association):
            raise _reject("Association", _kind(self._node), "Recover the original Association.")
        assert self._dataset is not None
        return MaterializedCoefficientRelation(
            _TOKEN, self._node, self._runtime, dataset=self._dataset
        )


PublicMaterialized: TypeAlias = (
    MaterializedAnalysisDomain
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


def wrap_materialized(
    node: J1Node, runtime: DatasetRuntime, dataset: MaterializedJ1Dataset
) -> PublicMaterialized:
    """Choose the closed public materialized variant for an admitted J1 root."""
    if isinstance(node, J1Members):
        return MaterializedAnalysisDomain(_TOKEN, node, runtime, dataset=dataset)
    if isinstance(node, J1Read):
        return MaterializedCategoryRelation(_TOKEN, node, runtime, dataset=dataset)
    if isinstance(node, J1SelectedCategory):
        return MaterializedSelectedCategoryRelation(_TOKEN, node, runtime, dataset=dataset)
    if isinstance(node, J4Association):
        return MaterializedAssociationResult(_TOKEN, node, runtime, dataset=dataset)
    if isinstance(node, J3Observed):
        if node.root.operator_id == "dsl.j1.ratio_rollup":
            return MaterializedRolledRatioRelation(_TOKEN, node, runtime, dataset=dataset)
        return MaterializedRatioRelation(_TOKEN, node, runtime, dataset=dataset)
    if isinstance(node, J1Difference):
        return MaterializedDifferenceRelation(_TOKEN, node, runtime, dataset=dataset)
    if isinstance(node, J1SelectedDifference):
        return MaterializedSelectedDifferenceRelation(_TOKEN, node, runtime, dataset=dataset)
    if isinstance(node, (J1Statistic, J4CoefficientStatistic)):
        return MaterializedStatisticRelation(_TOKEN, node, runtime, dataset=dataset)
    if isinstance(node, J4CoefficientSelection):
        return MaterializedCoefficientSelectionRelation(_TOKEN, node, runtime, dataset=dataset)
    if isinstance(node, J1Observed):
        if node.domain.kind == "group":
            return MaterializedGroupedNumericRelation(_TOKEN, node, runtime, dataset=dataset)
        if node.domain.kind == "singleton":
            return MaterializedRolledNumericRelation(_TOKEN, node, runtime, dataset=dataset)
        return MaterializedNumericRelation(_TOKEN, node, runtime, dataset=dataset)
    raise _reject(
        "admitted materialized J1–J4 variant",
        type(node).__name__,
        "Recover the exact public Artifact.",
    )


def new_members(node: J1Members, runtime: DatasetRuntime) -> LogicalAnalysisDomain:
    """Bind a source-free private member node to its public Session owner."""
    return LogicalAnalysisDomain(_TOKEN, node, runtime)
