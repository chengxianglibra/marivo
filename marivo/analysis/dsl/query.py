"""Immutable multi-Metric Query construction, separate from terminal data."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from ibis.common.deferred import Deferred

from marivo._definition.expressions import MetricNode
from marivo._definition.relations import ObserveQuery, PopulationProgram, QueryKeys
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    ExactRef,
    FieldRead,
    IdentityRead,
    Owner,
)
from marivo._temporal import TimeScope
from marivo.analysis.binding.observe import bind_observation
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import BoundQuantity, BoundSignature, reject
from marivo.analysis.dsl.artifact import Artifact
from marivo.analysis.dsl.population import MembershipAxis, Population, _axes, _owner
from marivo.analysis.dsl.temporal import CalendarAxis
from marivo.analysis.public_dsl import AnalysisContract
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic._deferred_semantics import (
    consume_metric,
    consume_metric_in_state,
    consume_predicate,
)


@dataclass(frozen=True, slots=True, init=False, repr=False)
class Query:
    """A lazy named Metric definition owned by one Session.

    Acquire through session.observe(metrics). This surface captures definitions and ordinary observation bindings;
    execute() evaluates ordinary numeric stages and atomically publishes terminal output in Store 10; committed output reads require no current source or Catalog.
    """

    _graph: BoundGraph
    _execute: Callable[[BoundGraph], Artifact]

    def __init__(self) -> None:
        raise TypeError("Use session.observe(metrics).")

    @classmethod
    def _from_metrics(
        cls,
        metrics: Mapping[str, Deferred],
        owner: Owner,
        execute: Callable[[BoundGraph], Artifact],
        *,
        during: TimeScope | CalendarAxis | None = None,
        by: tuple[Deferred | MembershipAxis, ...] | Population = (),
        where: Deferred | None = None,
        timezone: str = "UTC",
        state: CompiledSemanticState | None = None,
    ) -> Query:
        if not isinstance(metrics, Mapping) or not metrics:
            reject(
                "a nonempty named mapping of Metric expressions",
                type(metrics).__name__,
                "Pass session.observe({'revenue': revenue}).",
                "session.observe",
            )
        quantities: list[BoundQuantity] = []
        definitions: dict[ExactRef, MetricNode] = {}
        for name, metric in metrics.items():
            if not isinstance(metric, Deferred):
                reject(
                    "a Metric expression for each output",
                    type(metric).__name__,
                    "Resolve a Metric expression; Artifact objects and IDs are terminal outputs, not computation inputs.",
                    "session.observe",
                )
            facts = (
                consume_metric_in_state(metric, state)
                if state is not None
                else consume_metric(metric)
            )
            quantities.append(BoundQuantity(name, facts.node, facts.unit))
            definitions.update(facts._declared)
        if not isinstance(by, (tuple, Population)):
            reject(
                "an ordered tuple of semantic fields",
                type(by).__name__,
                "Pass by=(field,) using resolved semantic fields.",
                "session.observe",
            )
        if isinstance(by, Population):
            if _owner(by.definition) not in (None, owner):
                reject(
                    "a Population from this Session",
                    "different Session owner",
                    "Rebuild the domain in this Session.",
                    "session.observe",
                )
            axes, memberships = by.axes, by.memberships
            population = by.definition
        else:
            axes, memberships = _axes(by)
            population = None
        reads = tuple(
            axis
            if isinstance(axis, CalendarCoordinate)
            else FieldRead(axis.field, axis.entity, axis.path, axis.at)
            if axis.field is not None
            else IdentityRead(axis.entity, axis.path, axis.at)
            for axis in axes
        )
        condition = consume_predicate(where).node if where is not None else None
        signature = BoundSignature(
            owner, tuple(quantities), declared_metrics=tuple(definitions.items())
        )
        root = ObserveQuery(tuple((q.name, q.definition) for q in quantities), (), owner)
        BoundGraph(root, signature)
        binding = bind_observation(
            root.metrics,
            definitions,
            during=during,
            by=reads,
            where=condition,
            timezone=timezone,
            state=state,
            memberships=memberships,
            population=population,
        )
        signature = BoundSignature(
            owner,
            tuple(quantities),
            binding.coordinates.axes,
            tuple(definitions.items()),
            observation=binding,
        )
        root = ObserveQuery(root.metrics, signature.axes, owner)
        result = object.__new__(cls)
        object.__setattr__(result, "_graph", BoundGraph(root, signature))
        object.__setattr__(result, "_execute", execute)
        return result

    def __repr__(self) -> str:
        signature = self._graph.signature
        contributions = signature.observation.contributions if signature.observation else ()
        sources = len({item.root.ref for item in contributions})
        return f"<Query id={self._graph.fingerprint[:16]} session={signature.owner.session_id[:80]} outputs={len(signature.quantities)} sources={sources} contributions={len(contributions)}; use .contract().show()>"

    @classmethod
    def _from_population(
        cls, population: Population, owner: Owner, execute: Callable[[BoundGraph], Artifact]
    ) -> Query:
        from marivo._definition.observations import CoordinateBinding, ObservationBinding

        existing_owner = _owner(population.definition)
        if existing_owner is not None and existing_owner != owner:
            reject(
                "a Population from this Session",
                str(existing_owner),
                "Rebuild the Population in this Session.",
                "session.query",
            )
        axes = population.definition.axes
        binding = ObservationBinding(
            None,
            None,
            CoordinateBinding(axes, "grouped", population.definition, population.memberships),
            (),
        )
        signature = BoundSignature(owner, (), axes, observation=binding)
        root = ObserveQuery((), axes, owner, population.definition)
        result = object.__new__(cls)
        object.__setattr__(result, "_graph", BoundGraph(root, signature))
        object.__setattr__(result, "_execute", execute)
        return result

    def population(self) -> Population:
        """Capture this Query's complete output coordinates as a lazy domain.

        Returns: A Population retaining this Query's selection, period and Session.
        Example: ``cohort = query.population()``.
        Constraints: This does not execute or publish the Query.
        """
        signature = self._graph.signature
        if not signature.axes:
            reject(
                "a Query with coordinate axes",
                "a scalar Query",
                "Group the Query before selecting coordinates.",
                "Query.population",
            )
        return Population(
            PopulationProgram(
                QueryKeys(
                    self._graph.root,
                    signature.axes,
                    signature.observation,
                    signature.declared_metrics,
                    tuple(item.unit for item in signature.quantities),
                ),
                signature.owner,
                signature.axes,
                signature.observation.coordinates.memberships if signature.observation else (),
            )
        )

    def contract(self) -> AnalysisContract:
        """Describe captured outputs, bound context and unresolved physical types.

        Args: None.
        Returns: A bounded AnalysisContract with known definition facts.
        Example: ``query.contract().show()``.
        Constraints: No business or schema reads, Run admission or capability guessing.
        """
        quantities = self._graph.signature.quantities
        observation = self._graph.signature.observation
        axes = self._graph.signature.axes
        return AnalysisContract(
            kind="Query",
            phase="logical",
            actions=(),
            domain=self._graph.signature.observation.coordinates.mode
            if self._graph.signature.observation
            else "unbound",
            quantity=f"{len(quantities)} named Metric outputs",
            required_parts=(),
            retained_parts=(),
            _facts=(
                ("definition", self._graph.fingerprint),
                ("physical_dtype", "unresolved; schema-only preparation belongs to execution"),
                ("execution", "Query.execute() atomically publishes terminal output"),
                ("output", "terminal Artifact; preview, complete pandas export and saved facts"),
                (
                    "period",
                    repr(self._graph.signature.observation.period)
                    if self._graph.signature.observation
                    and self._graph.signature.observation.period
                    else "None; no additional time filter",
                ),
                ("axes", str(len(self._graph.signature.axes))),
                (
                    "where",
                    repr(observation.condition)[:400]
                    if observation and observation.condition is not None
                    else "None",
                ),
                *(
                    (
                        f"axis[{i}]",
                        (
                            axis.field.ref.path[:100]
                            if axis.field is not None
                            else axis.entity.ref.path[:100]
                        )
                        if isinstance(axis, AxisBinding)
                        else f"calendar={axis.grain[:80]} timezone={axis.timezone} buckets={len(axis.cells)}",
                    )
                    for i, axis in enumerate(axes[:6])
                ),
                ("axes_omitted", str(max(0, len(axes) - 6))),
                (
                    "contributions",
                    str(len(self._graph.signature.observation.contributions))
                    if self._graph.signature.observation
                    else "0",
                ),
                *(
                    (
                        f"source[{i}]",
                        f"{c.root.ref.path[:100]} occurrence={c.occurrence} intrinsic_conditions={len(c.intrinsic_conditions)} reads={len(c.reads)}",
                    )
                    for i, c in enumerate(
                        self._graph.signature.observation.contributions[:6]
                        if self._graph.signature.observation
                        else ()
                    )
                ),
                (
                    "sources_omitted",
                    str(max(0, len(observation.contributions) - 6)) if observation else "0",
                ),
                *(
                    (f"output[{index}]", f"{q.name[:100]} unit={q.unit or 'undeclared'}")
                    for index, q in enumerate(quantities[:8])
                ),
                ("outputs_omitted", str(max(0, len(quantities) - 8))),
            ),
        )

    def execute(self) -> Artifact:
        """Explicitly request evaluation and committed terminal output.

        Args: None.
        Returns: A terminal Artifact after checked execution and atomic publication.
        Example: ``artifact = query.execute()``.
        Constraints: Preparation failures create no Run. Each admitted call is a new evaluation; only committed Parquet and output records survive publication. The returned Artifact reads committed output without current-source or Catalog access.
        """
        return self._execute(self._graph)

    def __bool__(self) -> bool:
        reject(
            "an explicitly executed Query",
            "implicit truth conversion",
            "Call query.execute() explicitly when execution is available.",
            "Query.__bool__",
        )
