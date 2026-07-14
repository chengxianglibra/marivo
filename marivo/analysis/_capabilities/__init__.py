"""Private capability kernel for ``marivo.analysis``.

Re-exports private kernel helpers only for internal Marivo modules and
tests.  Nothing in this package is added to
``marivo/analysis/__init__.py`` or ``mv.__all__``.
"""

from __future__ import annotations

from marivo.analysis._capabilities.model import (
    ARTIFACT_FAMILIES,
    ROOT_GROUP_ORDER,
    SURFACE_LIMITS,
    ArtifactFamily,
    BoundaryCapability,
    CapabilityBase,
    CapabilityDescriptor,
    CapabilityKind,
    ConstructorCapability,
    InputFamily,
    OperatorCapability,
    OutputFamily,
    ReadCapability,
    RecoveryCapability,
    RootGroup,
    RootVisibility,
    SameAsInputFamily,
    SurfaceLimits,
)

__all__ = [
    "ARTIFACT_FAMILIES",
    "ROOT_GROUP_ORDER",
    "SURFACE_LIMITS",
    "ArtifactFamily",
    "BoundaryCapability",
    "CapabilityBase",
    "CapabilityDescriptor",
    "CapabilityKind",
    "ConstructorCapability",
    "InputFamily",
    "OperatorCapability",
    "OutputFamily",
    "ReadCapability",
    "RecoveryCapability",
    "RootGroup",
    "RootVisibility",
    "SameAsInputFamily",
    "SurfaceLimits",
]
