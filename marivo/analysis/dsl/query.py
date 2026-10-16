"""Immutable multi-Metric Query construction, separate from terminal data."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from marivo._definition.expressions import MetricNode
from marivo._definition.relations import ObserveQuery
from marivo._definition.types import AxisBinding, ExactRef, Owner
from marivo._temporal import TimeScope
from marivo.analysis.binding.observe import bind_observation
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import BoundQuantity, BoundSignature, reject
from marivo.analysis.dsl.artifact import Artifact
from marivo.analysis.dsl.temporal import CalendarAxis
from marivo.analysis.public_dsl import AnalysisContract
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic.expressions import Field, Metric, Predicate


@dataclass(frozen=True, slots=True, init=False, repr=False)
class Query:
    """A lazy named Metric definition owned by one Session.

    Acquire through session.observe(metrics). This surface captures definitions and ordinary observation bindings;
    execute() prepares schema and plans stages, while numeric routes (R2.4) and publication (R2.5) are pending.
    """

    _graph: BoundGraph
    _execute: Callable[[BoundGraph], Artifact]

    def __init__(self) -> None:
        raise TypeError("Use session.observe(metrics).")

    @classmethod
    def _from_metrics(
        cls,
        metrics: Mapping[str, Metric],
        owner: Owner,
        execute: Callable[[BoundGraph], Artifact],
        *,
        during: TimeScope | CalendarAxis | None = None,
        by: tuple[Field, ...] = (),
        where: Predicate | None = None,
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
            if not isinstance(metric, Metric):
                reject(
                    "a Metric expression for each output",
                    type(metric).__name__,
                    "Resolve a Metric expression; Artifact objects and IDs are terminal outputs, not computation inputs.",
                    "session.observe",
                )
            quantities.append(BoundQuantity(name, metric.node, metric.unit))
            definitions.update(metric._declared)
        signature = BoundSignature(
            owner, tuple(quantities), declared_metrics=tuple(definitions.items())
        )
        root = ObserveQuery(tuple((q.name, q.definition) for q in quantities), (), owner)
        BoundGraph(root, signature)
        binding = bind_observation(
            root.metrics,
            definitions,
            during=during,
            by=by,
            where=where,
            timezone=timezone,
            state=state,
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
                ("pending", "R2.4 numeric routes and R2.5 publication"),
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
        Returns: A terminal Artifact after execution and atomic publication are available.
        Example: ``artifact = query.execute()``.
        Constraints: Runs schema-only preparation and stage planning, then raises a structured unavailable-stage error before any business read or Run; never falls back to the old graph executor.
        """
        return self._execute(self._graph)

    def __bool__(self) -> bool:
        reject(
            "an explicitly executed Query",
            "implicit truth conversion",
            "Call query.execute() explicitly when execution is available.",
            "Query.__bool__",
        )
