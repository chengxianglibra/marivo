"""Definition traversal and canonical identities with native Ibis recipe tokens."""

from marivo._definition.deferred import canonical_recipe
from marivo._definition.graph_encoding import DefinitionEncoder, encoded_fingerprint
from marivo._definition.graph_encoding import canonical_bytes as canonical_bytes
from marivo._definition.graph_encoding import canonical_value as canonical_value
from marivo._definition.graph_encoding import dataclass_identity_name as dataclass_identity_name
from marivo._definition.graph_encoding import fingerprint as fingerprint
from marivo._definition.graph_traversal import DefinitionClosure as DefinitionClosure
from marivo._definition.graph_traversal import DefinitionNode as DefinitionNode
from marivo._definition.graph_traversal import Edge as Edge
from marivo._definition.graph_traversal import EdgeRole as EdgeRole
from marivo._definition.graph_traversal import children as children
from marivo._definition.graph_traversal import direct_refs as direct_refs
from marivo._definition.graph_traversal import walk as walk


def encode_definition(value: object) -> object:
    """Encode a closed definition or a captured value embedded in one."""
    return DefinitionEncoder(canonical_recipe).encode(value)


def definition_fingerprint(value: object) -> str:
    """Hash the selected facts and inputs of one captured definition value."""
    return encoded_fingerprint(encode_definition(value))


def computation_fingerprint(node: DefinitionNode) -> str:
    """Hash a computation's rules and exact dependencies independently of its name."""
    return DefinitionEncoder(canonical_recipe).fingerprint(node)
