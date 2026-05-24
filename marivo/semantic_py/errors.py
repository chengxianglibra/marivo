from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class SourceLocation:
    file: str
    line: int


@dataclass(frozen=True)
class SemanticError(Exception):
    phase: Literal["decorator", "assembly", "load", "runtime", "parity"]
    kind: str
    location: SourceLocation | None
    function: str | None
    message: str
    hint: str | None = None
    refs: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        Exception.__init__(self, str(self))

    def __str__(self) -> str:
        prefix = f"{self.phase}:{self.kind}"
        if self.location is not None:
            prefix = f"{prefix} at {self.location.file}:{self.location.line}"
        return f"{prefix}: {self.message}"


class SemanticDecoratorError(SemanticError, ValueError):
    pass


class SemanticAssemblyError(SemanticError):
    pass


class SemanticRuntimeError(SemanticError):
    pass


class SemanticParityError(SemanticError):
    pass


class PySemanticNotFoundError(KeyError):
    def __init__(self, entity: str, name: str) -> None:
        super().__init__(f"{entity} '{name}' not found")
        self.entity = entity
        self.name = name


PySemanticNotFound = PySemanticNotFoundError


class SemanticLoadError(Exception):
    def __init__(self, errors: list[SemanticError]) -> None:
        self.errors = errors
        joined = "; ".join(str(error) for error in errors)
        super().__init__(joined)
