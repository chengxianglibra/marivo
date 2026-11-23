"""Resolve qualified Help links used by Analysis descriptors."""

from marivo.datasource.help_spi import DATASOURCE_LIVE_SURFACE
from marivo.introspection.live.resolve import resolve_live_target
from marivo.ontology.help_spi import ONTOLOGY_LIVE_SURFACE
from marivo.semantic.help_spi import SEMANTIC_LIVE_SURFACE


def validate_external_link(target: str) -> None:
    if target == "authoring":
        return
    surface, _, member = target.partition(".")
    if surface == "semantic":
        resolve_live_target(member, SEMANTIC_LIVE_SURFACE)
    elif surface == "datasource":
        resolve_live_target(member, DATASOURCE_LIVE_SURFACE)
    elif surface == "ontology":
        resolve_live_target(member, ONTOLOGY_LIVE_SURFACE)
    else:
        raise ValueError(f"Unsupported Help link {target!r}")
