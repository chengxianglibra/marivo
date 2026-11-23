"""Validate the stored identity keys of a literal Population in its Catalog."""

from marivo.analysis.model.errors import reject
from marivo.semantic import Entity, SemanticCatalog


def validate_identity_keys(
    keys: tuple[tuple[str, tuple[str, ...]], ...], catalog: SemanticCatalog
) -> None:
    for identity, captured in keys:
        entity = catalog.get(identity)
        if not isinstance(entity, Entity) or entity.primary_key != captured:
            reject(
                "the captured complete primary-key interpretation",
                identity,
                "Rebuild the literal Population with the receiving Catalog's complete identity.",
                "population",
            )
