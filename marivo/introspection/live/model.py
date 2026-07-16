"""Neutral identities, limits, and protocols for live help resolution."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict


@dataclass(frozen=True)
class SurfaceLimits:
    """Numeric rendering and suggestion limits shared by help surfaces."""

    root_help_max_lines: int = 80
    root_help_max_codepoints: int = 8_000
    focused_help_max_lines: int = 120
    focused_help_max_codepoints: int = 12_000
    object_contract_max_subjects: int = 8
    object_contract_render_max_lines: int = 120
    object_contract_render_max_codepoints: int = 12_000
    help_suggestion_limit: int = 5


SURFACE_LIMITS = SurfaceLimits()

HelpSurface = Literal["analysis", "datasource", "semantic"]


class EnvironmentFingerprint(BaseModel):
    """Snapshot of the installed package and interpreter identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    marivo_version: str
    python_executable: str
    package_path: str

    @classmethod
    def current(cls) -> EnvironmentFingerprint:
        """Construct a fingerprint from the current runtime environment."""
        import marivo

        return cls(
            marivo_version=marivo.__version__,
            python_executable=str(Path(sys.executable).resolve()),
            package_path=str(Path(marivo.__file__).resolve()),
        )


class LiveHelpTarget(BaseModel):
    """Typed identity for a lookup on one live help surface."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    surface: HelpSurface
    canonical_id: str | None = None

    @property
    def display(self) -> str:
        """Return the stable display form used by help and repair text."""
        if self.canonical_id is not None:
            return self.canonical_id
        return self.surface


class ResolvableHelpDescriptor(Protocol):
    """Minimal native-descriptor facts consumed by neutral resolution."""

    @property
    def canonical_id(self) -> str:
        """Return the canonical target accepted by the owning help surface."""
        ...

    @property
    def public_entrypoint(self) -> str | None:
        """Return the public invocation shape, when the target is invokable."""
        ...

    @property
    def summary(self) -> str:
        """Return a bounded factual summary used for suggestions."""
        ...


DescriptorT_co = TypeVar(
    "DescriptorT_co",
    bound=ResolvableHelpDescriptor,
    covariant=True,
)


class LiveSurfaceRegistry(Protocol[DescriptorT_co]):
    """Read-only native registry view required by the neutral resolver."""

    @property
    def surface(self) -> HelpSurface:
        """Return the owning help surface."""
        ...

    def canonical_ids(self) -> tuple[str, ...]:
        """Return canonical help ids in deterministic registry order."""
        ...

    def by_canonical_id(self, canonical_id: str) -> DescriptorT_co:
        """Return the original native descriptor for a canonical id."""
        ...

    def by_callable(self, value: object) -> DescriptorT_co:
        """Return the original descriptor for an exact callable identity."""
        ...
