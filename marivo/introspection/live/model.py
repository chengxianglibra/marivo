"""Neutral identities, limits, and protocols for live help resolution."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from marivo._kernel.environment import environment_identity
from marivo._kernel.errors import HelpSurface as HelpSurface
from marivo._kernel.errors import LiveHelpTarget as LiveHelpTarget


@dataclass(frozen=True)
class SurfaceLimits:
    """Numeric rendering and suggestion limits shared by help surfaces."""

    root_help_max_lines: int = 96
    root_help_max_codepoints: int = 8_000
    focused_help_max_lines: int = 120
    focused_help_max_codepoints: int = 12_000
    object_contract_max_subjects: int = 8
    object_contract_render_max_lines: int = 120
    object_contract_render_max_codepoints: int = 12_000
    help_suggestion_limit: int = 5


SURFACE_LIMITS = SurfaceLimits()


class EnvironmentFingerprint(BaseModel):
    """Snapshot of the installed package and interpreter identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    marivo_version: str
    python_executable: str
    package_path: str

    @classmethod
    def current(cls) -> EnvironmentFingerprint:
        """Construct a fingerprint from the current runtime environment."""
        return cls(**environment_identity())
