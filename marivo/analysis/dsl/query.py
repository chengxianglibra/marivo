"""Immutable multi-Metric Query construction, separate from terminal data."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from marivo._definition.expressions import MetricNode
from marivo._definition.relations import ObserveQuery
from marivo._definition.types import ExactRef, Owner
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import BoundQuantity, BoundSignature, reject
from marivo.analysis.dsl.artifact import Artifact
from marivo.analysis.public_dsl import AnalysisContract
from marivo.semantic.expressions import Metric


@dataclass(frozen=True, slots=True, init=False, repr=False)
class Query:
    """A lazy named Metric definition owned by one Session.

    Acquire through session.observe(metrics). This R2.1 surface captures definitions;
    ordinary observation binding and execution are delivered by R2.2 through R2.5.
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
        result = object.__new__(cls)
        object.__setattr__(result, "_graph", BoundGraph(root, signature))
        object.__setattr__(result, "_execute", execute)
        return result

    def __repr__(self) -> str:
        signature = self._graph.signature
        return f"<Query id={self._graph.fingerprint[:16]} session={signature.owner.session_id[:80]} outputs={len(signature.quantities)}; use .contract().show()>"

    def contract(self) -> AnalysisContract:
        """Describe captured outputs and the pending observation binding.

        Args: None.
        Returns: A bounded AnalysisContract with known definition facts.
        Example: ``query.contract().show()``.
        Constraints: No business or schema reads, Run admission or capability guessing.
        """
        quantities = self._graph.signature.quantities
        return AnalysisContract(
            kind="Query",
            phase="logical",
            actions=(),
            domain="observation context not yet bound",
            quantity=f"{len(quantities)} named Metric outputs",
            required_parts=(),
            retained_parts=(),
            _facts=(
                ("definition", self._graph.fingerprint),
                ("physical_dtype", "unresolved; prepared at execute after R2.2 binding"),
                ("pending", "R2.2 binding, R2.3/R2.4 execution and R2.5 publication"),
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
        Constraints: R2.1 raises a structured unavailable-stage error before reading data or admitting a Run; never falls back to the old graph executor.
        """
        return self._execute(self._graph)

    def __bool__(self) -> bool:
        reject(
            "an explicitly executed Query",
            "implicit truth conversion",
            "Call query.execute() explicitly when execution is available.",
            "Query.__bool__",
        )
