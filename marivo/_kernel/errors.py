"""Dependency-neutral errors, Help addresses, and structured repair facts."""

from dataclasses import dataclass
from typing import ClassVar, Generic, Literal, NoReturn, TypeAlias, TypeVar, get_args


class MarivoError(Exception):
    """Preserve an owning surface's typed repair across backend boundaries."""


HelpSurface = Literal["analysis", "datasource", "semantic", "ontology"]


@dataclass(frozen=True, slots=True, kw_only=True)
class LiveHelpTarget:
    """Exact address on one public Help surface."""

    surface: HelpSurface
    canonical_id: str | None = None

    def __post_init__(self) -> None:
        if self.surface not in get_args(HelpSurface):
            raise ValueError(f"unknown Help surface: {self.surface!r}")
        if self.canonical_id is not None and not isinstance(self.canonical_id, str):
            raise ValueError("canonical_id must be a string or None")

    @property
    def display(self) -> str:
        if self.canonical_id is None:
            return self.surface
        return f"{self.surface}.{self.canonical_id}"


KindT = TypeVar("KindT", bound=str)


@dataclass(frozen=True, slots=True, kw_only=True)
class StructuredRepair(Generic[KindT]):
    """Immutable retry or inspection guidance with a domain-owned kind."""

    kinds: ClassVar[tuple[str, ...]] = ()

    kind: KindT
    action: str
    help_target: LiveHelpTarget
    snippet: str | None = None
    candidates: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in self.kinds:
            raise ValueError(f"unknown repair kind: {self.kind!r}")
        if not isinstance(self.action, str):
            raise ValueError("action must be a string")
        if not isinstance(self.help_target, LiveHelpTarget):
            raise ValueError("help_target must be a LiveHelpTarget")
        if self.snippet is not None and not isinstance(self.snippet, str):
            raise ValueError("snippet must be a string or None")
        candidates: object = self.candidates
        if not isinstance(candidates, (tuple, list)) or not all(
            isinstance(candidate, str) for candidate in candidates
        ):
            raise ValueError("candidates must contain only strings")
        if isinstance(candidates, list):
            object.__setattr__(self, "candidates", tuple(candidates))

    def model_dump(self, *, mode: Literal["python", "json"] = "python") -> dict[str, object]:
        """Project the repair using its existing Python or JSON field shape."""
        return {
            "kind": self.kind,
            "help_target": {
                "surface": self.help_target.surface,
                "canonical_id": self.help_target.canonical_id,
            },
            "action": self.action,
            "snippet": self.snippet,
            "candidates": list(self.candidates) if mode == "json" else self.candidates,
        }


DefinitionErrorKind: TypeAlias = Literal[
    "invalid_value",
    "cycle",
    "ownership_conflict",
    "unresolved",
    "unknown_rule_version",
]


class DefinitionGraphError(ValueError):
    """Structured failure raised by the private definition layer.

    Semantic and analysis owners translate it into their public error families
    at their boundaries; ``path`` locates the failing node or value.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: DefinitionErrorKind,
        path: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.kind: DefinitionErrorKind = kind
        self.path = path


def invalid(
    message: str,
    *,
    kind: DefinitionErrorKind = "invalid_value",
    path: tuple[str, ...] = (),
) -> NoReturn:
    raise DefinitionGraphError(message, kind=kind, path=path)
