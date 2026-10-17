"""Private immutable Query graph; no execution or historical lineage traversal."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import NoReturn

from marivo._definition.graph import computation_fingerprint, walk
from marivo._definition.query import ResultQuery
from marivo._definition.relations import ObserveQuery, QueryKeys, QueryNode
from marivo._definition.types import DefinitionGraphError
from marivo.analysis.core.model import BoundQuantity, BoundSignature, ResultQuantity, reject


@dataclass(frozen=True, slots=True)
class BoundGraph:
    """A multi-output Query definition, never a persisted Artifact dependency."""

    root: QueryNode
    signature: BoundSignature

    def __post_init__(self) -> None:
        if (
            not isinstance(self.root, (ObserveQuery, ResultQuery))
            or type(self.signature) is not BoundSignature
        ):
            _fail("an observation definition and multi-output signature", type(self).__name__)
        if (
            self.root.owner != self.signature.owner
            or self.root.by != self.signature.axes
            or (
                isinstance(self.root, ObserveQuery)
                and self.root.metrics
                != tuple(
                    (q.name, q.definition)
                    for q in self.signature.quantities
                    if isinstance(q, BoundQuantity)
                )
            )
        ):
            _fail("matching Query outputs, axes and Session ownership", "inconsistent binding")
        definitions = dict(self.signature.declared_metrics)
        if (
            isinstance(self.root, ObserveQuery)
            and not self.root.metrics
            and self.root.population is None
        ):
            _fail("Metric outputs or a keys-only Population", "an empty Query")
        try:
            walk((self.root,), resolve=definitions.get)
        except DefinitionGraphError as error:
            reject(
                "a closed definition graph from one Session owner",
                str(error),
                "Rebuild the Metric dependencies in the receiving Session.",
                "query.definition",
            )

    @property
    def fingerprint(self) -> str:
        definition = computation_fingerprint(self.root)
        observation = self.signature.observation
        return (
            definition
            if observation is None
            else sha256(f"{definition}:{observation.fingerprint}".encode()).hexdigest()
        )


def capture_query(graph: BoundGraph) -> QueryKeys:
    """Capture the exact logical input, including its completed observation scope."""
    signature = graph.signature
    return QueryKeys(
        graph.root,
        signature.axes,
        signature.observation,
        signature.declared_metrics,
        tuple(item.unit for item in signature.quantities),
        tuple(item.name for item in signature.quantities),
        tuple(
            item.kind if isinstance(item, ResultQuantity) else "numeric"
            for item in signature.quantities
        ),
    )


def input_graph(captured: QueryKeys) -> BoundGraph:
    """Recover an invocation input from immutable definition records."""
    owner = captured.query.owner
    if owner is None:
        _fail("a Session-owned Query", "unbound owner")
    quantities: tuple[BoundQuantity | ResultQuantity, ...] = (
        tuple(
            BoundQuantity(name, definition, unit)
            for (name, definition), unit in zip(captured.query.metrics, captured.units, strict=True)
        )
        if isinstance(captured.query, ObserveQuery)
        else tuple(
            ResultQuantity(name, unit, kind)
            for name, unit, kind in zip(captured.names, captured.units, captured.kinds, strict=True)
        )
    )
    return BoundGraph(
        captured.query,
        BoundSignature(
            owner,
            quantities,
            captured.axes,
            captured.declared,
            observation=captured.observation,
        ),
    )


def _fail(expected: str, received: str) -> NoReturn:
    reject(expected, received, "Rebuild the exact bound definition graph.", "analysis.graph")
