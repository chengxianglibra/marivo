"""Semantic-owned public addresses, independent of Python symbol identities."""

from marivo.introspection.live.targets import canonical_target


def semantic_target(identity: str) -> str:
    """Name a semantic concept, constructor or receiver operation."""
    if identity.startswith("objects."):
        return identity.removeprefix("objects.")
    if identity.startswith("expressions."):
        return canonical_target(identity.removeprefix("expressions."))
    if identity in {
        "domain",
        "entity",
        "dimension",
        "time_dimension",
        "measure",
        "metric",
        "relationship",
        "period_calendar",
        "temporal_set",
    }:
        return identity + ".define"
    if identity in {"measure_column", "dimension_column", "time_dimension_column"}:
        return identity.removesuffix("_column") + ".from_column"
    return canonical_target(identity)
