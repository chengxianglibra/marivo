"""Naming rules shared by native Help owners.

Owners explicitly choose semantic identities; this helper only spells registered
identity segments consistently. It does not normalize public user input.
"""

import re


def canonical_target(identity: str) -> str:
    """Spell an owner-chosen identity as lowercase snake_case segments."""

    def segment(value: str) -> str:
        value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", value)
        return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value).lower()

    return ".".join(segment(value) for value in identity.split("."))


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
    }:
        return identity + ".define"
    if identity in {"measure_column", "dimension_column", "time_dimension_column"}:
        return identity.removesuffix("_column") + ".from_column"
    return canonical_target(identity)
