"""Typed analysis errors with unified Marivo help repairs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict

from marivo._errors import MarivoError
from marivo.introspection.live.model import LiveHelpTarget
from marivo.introspection.live.targets import canonical_target

RepairKind = Literal[
    "retry",
    "inspect",
    "user_choice",
    "semantic_authoring",
    "environment",
]


class AnalysisRepair(BaseModel):
    """Typed repair instruction for an :class:`AnalysisError`.

    Parameters
    ----------
    kind:
        Closed repair category. ``retry`` means the agent can re-attempt with
        a corrected call. ``inspect`` means the agent should gather more
        evidence before proceeding. ``user_choice`` means several mechanically
        legal repairs remain and business judgment must select one.
        ``semantic_authoring`` means a required semantic object is absent, so
        typed analysis must stop that branch; the agent may use terminal
        ``md.raw_sql(...)`` and must request semantic-authoring approval at
        closeout. ``environment`` means project or datasource state must be
        repaired before retry.
    action:
        One-sentence concrete next step.
    help_target:
        Canonical surface-qualified ``marivo.help(...)`` target to consult.
    snippet:
        Optional paste-ready code snippet.
    candidates:
        Optional tuple of live candidate strings (e.g. available metric ids).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: RepairKind
    action: str
    help_target: LiveHelpTarget
    snippet: str | None = None
    candidates: tuple[str, ...] = ()


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

        # Derive stable fields from context if not explicitly provided.
        derived = self._derive_fields()
        self.expected: str | None = expected if expected is not None else derived.get("expected")
        self.received: str | None = received if received is not None else derived.get("received")
        self.location: str | None = location if location is not None else derived.get("location")
        self.repair: AnalysisRepair | None = repair if repair is not None else derived.get("repair")

        self.hint = hint

    @property
    def kind(self) -> str:
        name = type(self).__name__
        return name[:-5] if name.endswith("Error") else name

    def _derive_fields(self) -> _DerivedFields:
        """Override in subtypes to derive stable fields from ``_context``."""

        return _DerivedFields()

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
            target = self.repair.help_target
            qualified = (
                target.surface
                if target.canonical_id is None
                else f"{target.surface}.{target.canonical_id}"
            )
            lines.append(f"Help: marivo.help({qualified!r})")

        return "\n".join(lines)


class TimezoneInvalidError(AnalysisError):
    pass


class AnalysisStageUnavailableError(AnalysisError):
    """A requested Frame/audit stage has not been delivered in this build."""

    def __init__(
        self, *, stage: str, expected: str, received: str, repair: str, target: str
    ) -> None:
        self.stage = stage
        super().__init__(
            message="This Frame/audit stage is not available in the current build.",
            expected=expected,
            received=received,
            location=f"analysis.{target}",
            repair=AnalysisRepair(
                kind="environment",
                action=repair,
                help_target=LiveHelpTarget(
                    surface="analysis", canonical_id=canonical_target(target)
                ),
            ),
        )


class HelpTargetError(AnalysisError):
    """Private analysis-surface rejection adapted by unified help."""

    def __init__(
        self,
        *,
        target: object,
        suggestions: tuple[str, ...],
        owning_surface: Literal["datasource", "semantic"] | None = None,
    ) -> None:
        received = target if isinstance(target, str) else type(target).__name__
        message = "analysis help target is not registered"
        if owning_surface is not None:
            callable_target = getattr(target, "__func__", target)
            target_name = getattr(callable_target, "__qualname__", type(target).__qualname__)
            retry_call = f'marivo.help("{owning_surface}.{target_name}")'
            message += f". This target belongs to {owning_surface}; use {retry_call} instead."
            action = f"Retry with the owning surface: {retry_call}."
            help_target = LiveHelpTarget(
                surface=owning_surface,
                canonical_id=target_name,
            )
        else:
            action = "Use marivo.help() to choose authoring or analysis."
            help_target = LiveHelpTarget(surface="analysis")
        if suggestions and owning_surface is None:
            # Surface fuzzy candidates on the first line. See issue #35.
            message += f". Did you mean: {', '.join(suggestions)}?"
        super().__init__(
            message=message,
            expected=("a canonical target string or registered public callable/type"),
            received=str(received),
            location="marivo.help.target",
            repair=AnalysisRepair(
                kind="retry" if owning_surface is not None else "inspect",
                action=action,
                help_target=help_target,
                candidates=() if owning_surface is not None else suggestions,
            ),
        )


class SessionStateError(AnalysisError): ...


class SessionNotFoundError(SessionStateError): ...


class SessionAlreadyExistsError(SessionStateError):
    """A Session name is already owned by an existing immutable identity."""


class FindingNotFoundError(AnalysisError): ...


def _runtime_read_repair(*, action: str, target: str, snippet: str | None = None) -> AnalysisRepair:
    return AnalysisRepair(
        kind="inspect",
        action=action,
        help_target=LiveHelpTarget(surface="analysis", canonical_id=canonical_target(target)),
        snippet=snippet,
    )


class ArtifactNotFoundError(AnalysisError):
    @classmethod
    def for_ref(cls, ref: str) -> ArtifactNotFoundError:
        return cls(
            message=f"Artifact {ref!r} does not exist in the current Session",
            expected="an exact Artifact ref owned by the current Session",
            received=ref,
            location="session.persisted_frame(artifact_id)",
            repair=_runtime_read_repair(
                action="Inspect session.persisted_frames() for committed descriptions and schemas, then pass a selected exact artifact_id to session.persisted_frame(). Ambiguous candidates require further inspection or caller selection.",
                target="session.persisted_frames",
                snippet="page = session.persisted_frames(); page.show()",
            ),
        )
