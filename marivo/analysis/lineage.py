"""Lineage dataclasses for analysis frames."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LineageStep:
    intent: str
    job_ref: str | None
    inputs: list[str]
    params_digest: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Lineage:
    steps: list[LineageStep] = field(default_factory=list)
    external_inputs: list[str] = field(default_factory=list)

    @classmethod
    def compose(cls, a: Lineage, b: Lineage, *, new_step: LineageStep) -> Lineage:
        """Concatenate two source lineages plus a new step."""
        merged_external = sorted(set(a.external_inputs) | set(b.external_inputs))
        return cls(
            steps=[*a.steps, *b.steps, new_step],
            external_inputs=merged_external,
        )
