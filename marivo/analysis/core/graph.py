"""Private immutable Query graph; no execution or historical lineage traversal."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import NoReturn

from marivo._definition.graph import computation_fingerprint, walk
from marivo._definition.query import ResultQuery
from marivo._definition.relations import ObserveQuery, QueryNode, ReadQuery
from marivo._definition.types import DefinitionGraphError
from marivo.analysis.core.model import BoundQuantity, BoundSignature, reject


@dataclass(frozen=True, slots=True)
class BoundGraph:
    """A multi-output Query definition, never a persisted Artifact dependency."""

    root: QueryNode
    signature: BoundSignature

    def __post_init__(self) -> None:
        if (
            not isinstance(self.root, (ObserveQuery, ReadQuery, ResultQuery))
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


def read_input_graph(graph: BoundGraph) -> BoundGraph:
    """Prepare the explicit classified key domain of one source attribute read."""
    root = graph.root
    assert isinstance(root, ReadQuery)
    axes = root.population.axes
    return BoundGraph(
        ObserveQuery((), axes, root.owner, root.population),
        BoundSignature(root.owner, (), axes, observation=graph.signature.observation),
    )


def _fail(expected: str, received: str) -> NoReturn:
    reject(expected, received, "Rebuild the exact bound definition graph.", "analysis.graph")
