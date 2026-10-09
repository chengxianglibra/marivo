"""Stable identities for independently bound observation classifications."""

from hashlib import sha256

from marivo.analysis.core.graph import MethodNode, Node, SourceLeaf
from marivo.analysis.core.model import Coordinate
from marivo.analysis.core.predicates import ValuePredicate
from marivo.analysis.core.rules import AttachCategory, BindProject, PartsTransport, TimeProduct


def digest(text: str) -> str:
    return sha256(text.encode()).hexdigest()


def read_coordinate(params: BindProject) -> Coordinate:
    """Freeze a field role without replacing its semantic Dimension identity."""
    binding_id = (
        digest(repr((params.ref, params.path_contracts, params.owner_selection)))
        if params.path or params.owner_selection is not None
        else "direct"
    )
    return Coordinate(params.field_owner, params.ref.path, "group", binding_id)


def _predicate(predicate: ValuePredicate) -> str:
    return repr(
        (
            predicate.operator,
            predicate.value,
            predicate.unknown,
            predicate.input_index,
            predicate.right_index,
            tuple(_predicate(child) for child in predicate.children),
        )
    )


def classification_meaning(node: Node) -> str:
    """Identify a classification's frozen semantics independently of invocation IDs."""
    if isinstance(node, SourceLeaf):
        return digest(repr((node.definition, node.signature.domain.version_selection)))
    if not isinstance(node, MethodNode):
        return node.fingerprint
    params = node.parameters
    if isinstance(params, BindProject):
        own = repr(
            (
                params.ref,
                params.path_contracts,
                params.owner_selection,
                params.resolved_versions,
                params.expression_bodies,
            )
        )
    elif isinstance(params, PartsTransport):
        own = repr(
            (params.mode, tuple(_predicate(p) for p in params.predicates), params.classification)
        )
    elif isinstance(params, AttachCategory):
        own = repr(params.coordinate)
    elif isinstance(params, TimeProduct):
        return classification_meaning(node.inputs[0].node)
    else:
        own = repr((node.method, node.signature.domain.instance_key))
    return digest(own + repr(tuple(classification_meaning(edge.node) for edge in node.inputs)))
