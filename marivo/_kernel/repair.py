"""Dependency-neutral repair facts for typed analysis failures."""

from dataclasses import dataclass
from typing import Literal, Protocol

HelpSurface = Literal["analysis", "datasource", "semantic", "ontology"]

RepairKind = Literal[
    "retry",
    "inspect",
    "user_choice",
    "semantic_authoring",
    "environment",
]


class RepairTarget(Protocol):
    @property
    def surface(self) -> HelpSurface: ...

    @property
    def canonical_id(self) -> str | None: ...

    @property
    def display(self) -> str: ...


class Repair(Protocol):
    @property
    def kind(self) -> RepairKind: ...

    @property
    def action(self) -> str: ...

    @property
    def help_target(self) -> RepairTarget: ...

    @property
    def snippet(self) -> str | None: ...

    @property
    def candidates(self) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class RepairHelpTarget:
    surface: HelpSurface
    canonical_id: str | None = None

    @property
    def display(self) -> str:
        if self.canonical_id is None:
            return self.surface
        return f"{self.surface}.{self.canonical_id}"


@dataclass(frozen=True, slots=True, kw_only=True)
class StructuredRepair:
    kind: RepairKind
    action: str
    help_target: RepairHelpTarget
    snippet: str | None = None
    candidates: tuple[str, ...] = ()
