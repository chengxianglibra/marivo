"""Typed failures shared by analysis construction, execution and validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar, Literal, NoReturn, TypedDict, get_args

from typing_extensions import override

from marivo._kernel.errors import LiveHelpTarget, MarivoError, StructuredRepair
from marivo.introspection.live.targets import canonical_target

RepairKind = Literal["retry", "inspect", "user_choice", "semantic_authoring", "environment"]


@dataclass(frozen=True, slots=True, kw_only=True)
class AnalysisRepair(StructuredRepair[RepairKind]):
    """Typed repair instruction attached to an analysis failure.

    The kind distinguishes retry, further inspection, a user choice,
    semantic authoring, or environment repair. Read action for the next
    step and help_target for its exact contract.
    """

    kinds: ClassVar[tuple[str, ...]] = get_args(RepairKind)


class _DerivedFields(TypedDict, total=False):
    """Internal typed dict for derived stable fields.

    Keys are ``expected``, ``received``, ``location``, and ``repair``.
    """

    expected: str
    received: str
    location: str
    repair: AnalysisRepair


class AnalysisError(MarivoError):
    """Call marivo.help(AnalysisError) for its public consumption contract.

    Base class for analysis errors. Read the structured fields and owning repair.
    """

    def __init__(
        self,
        *,
        message: str,
        expected: str | None = None,
        received: str | None = None,
        location: str | None = None,
        repair: AnalysisRepair | None = None,
        hint: str | None = None,
        context: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self._context: dict[str, object] = dict(context) if context else {}

        derived = self._derive_fields()
        self.expected: str | None = expected if expected is not None else derived.get("expected")
        self.received: str | None = received if received is not None else derived.get("received")
        self.location: str | None = location if location is not None else derived.get("location")
        self.repair = repair if repair is not None else derived.get("repair")

        self.hint = hint

    @property
    def kind(self) -> str:
        name = type(self).__name__
        return name[:-5] if name.endswith("Error") else name

    def _derive_fields(self) -> _DerivedFields:
        """Override in subtypes to derive stable fields from ``_context``."""

        return _DerivedFields()

    @override
    def __str__(self) -> str:
        lines = [f"{type(self).__name__}: {self.message}"]

        context_lines: list[str] = []
        if self.location:
            context_lines.append(f"Location: {self.location}")
        if self.expected:
            context_lines.append(f"Expected: {self.expected}")
        if self.received:
            context_lines.append(f"Received: {self.received}")
        if self.hint:
            context_lines.append(f"Hint: {self.hint}")
        if context_lines:
            lines.append("")
            lines.extend(context_lines)

        if self.repair is not None:
            lines.append("")
            lines.append("Repair:")
            lines.append(f"  {self.repair.action}")
            if self.repair.snippet:
                lines.extend(f"  {line}" for line in self.repair.snippet.splitlines())
            if self.repair.candidates:
                lines.append(f"  Candidates: {', '.join(self.repair.candidates)}")
            lines.append(f"Help: marivo.help({self.repair.help_target.display!r})")

        return "\n".join(lines)


class CoreRuleError(AnalysisError):
    """A private analysis structure has invalid input or lacks a premise."""

    def __init__(
        self,
        *,
        expected: str,
        received: str,
        repair: str,
        location: str,
        canonical_id: str | None = None,
        candidates: tuple[str, ...] = (),
    ) -> None:
        super().__init__(
            message="Analysis core rule validation failed.",
            expected=expected,
            received=received,
            location=location,
            repair=AnalysisRepair(
                kind="retry",
                action=repair,
                candidates=candidates,
                help_target=LiveHelpTarget(
                    surface="analysis",
                    canonical_id=canonical_id,
                ),
            ),
        )


def reject(
    expected: str,
    received: str,
    repair: str,
    location: str,
    *,
    canonical_id: str | None = None,
    candidates: tuple[str, ...] = (),
) -> NoReturn:
    raise CoreRuleError(
        expected=expected,
        received=received,
        repair=repair,
        location=location,
        canonical_id=canonical_id,
        candidates=candidates,
    )


class DatasetConstructionError(AnalysisError):
    """A Dataset value violates its owning closed construction contract."""

    def __init__(
        self,
        *,
        expected: str,
        received: str,
        repair: str,
        location: str = "dataset",
        message: str = "Dataset contract validation failed.",
        help_target: str | None = None,
        received_limit: int = 320,
        repair_kind: RepairKind = "retry",
    ) -> None:
        bounded_received = received[:received_limit]
        if received_limit > 320 and len(received) > received_limit:
            bounded_received = received[: received_limit - 3] + "..."
        super().__init__(
            message=message,
            expected=expected[:320],
            received=bounded_received,
            location=location[:160],
            hint=repair[:480],
            repair=AnalysisRepair(
                kind=repair_kind,
                action=repair[:480],
                help_target=LiveHelpTarget(
                    surface="analysis",
                    canonical_id=canonical_target(help_target) if help_target is not None else None,
                ),
            ),
        )


class DatasetRegistrationError(DatasetConstructionError):
    """A private family registration is incomplete or inconsistent."""


class ObservationConstructionError(DatasetConstructionError):
    """The supplied source or operator lacks exact semantic authority."""


class ObservationBindingError(ObservationConstructionError):
    """Parameterized source inputs do not match their declaration."""
