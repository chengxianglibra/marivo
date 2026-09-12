"""Typed analysis errors with unified Marivo help repairs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict

from marivo.introspection.live.model import LiveHelpTarget

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


class AnalysisError(Exception):
    """Call marivo.help(AnalysisError) for its public consumption contract.

    Base class for all analysis errors.
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
            expected=(
                "None, canonical target string, registered public callable/type, "
                "public analysis object, semantic object/ref, or AnalysisError"
            ),
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


class SessionIdentityAmbiguousError(SessionStateError):
    """An exact identity names two different existing Sessions."""


class SessionTimezoneConflict(SessionStateError):  # noqa: N818
    def _derive_fields(self) -> _DerivedFields:
        persisted = self._context.get("persisted_report_tz", "<persisted>")
        requested = self._context.get("requested_report_tz", "<requested>")
        return _DerivedFields(
            expected=f"report_timezone={persisted!r}",
            received=f"report_timezone={requested!r}",
            location="mv.session.get_or_create(report_timezone=...)",
            repair=AnalysisRepair(
                kind="retry",
                action=(
                    "Use the persisted report timezone or create a new named Session "
                    "with the desired report timezone."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="session.namespace"),
            ),
        )


class FindingNotFoundError(AnalysisError): ...


class EvidenceIntegrityError(AnalysisError):
    """Committed evidence cannot be resolved to one intact canonical graph."""


class InvalidEventPatternError(AnalysisError):
    @property
    def kind(self) -> str:
        return "invalid_event_pattern"


class PatternStepMismatchError(AnalysisError):
    @property
    def kind(self) -> str:
        return "pattern_step_mismatch"


class InvalidEventMatchingPolicyError(AnalysisError):
    @property
    def kind(self) -> str:
        return "invalid_event_matching_policy"


def _runtime_read_repair(*, action: str, target: str, snippet: str | None = None) -> AnalysisRepair:
    return AnalysisRepair(
        kind="inspect",
        action=action,
        help_target=LiveHelpTarget(surface="analysis", canonical_id=target),
        snippet=snippet,
    )


class ArtifactNotFoundError(AnalysisError):
    @classmethod
    def for_ref(cls, ref: str) -> ArtifactNotFoundError:
        return cls(
            message=f"Artifact {ref!r} does not exist in the current Session",
            expected="an exact Artifact ref owned by the current Session",
            received=ref,
            location="session.artifact(ref)",
            repair=_runtime_read_repair(
                action="Inspect bounded Run history or the Session graph, then retry one exact ref.",
                target="session.runs",
                snippet="page = session.runs(limit=20)\nartifact = session.artifact('<ref>')",
            ),
        )


class RunNotFoundError(AnalysisError):
    @classmethod
    def for_id(cls, run_id: str) -> RunNotFoundError:
        return cls(
            message=f"Run {run_id!r} does not exist in the current Session",
            expected="an exact Run id owned by the current Session",
            received=run_id,
            location="session.get_run(run_id)",
            repair=_runtime_read_repair(
                action="Read a bounded Run page and retry one returned Run id.",
                target="session.runs",
                snippet="page = session.runs(limit=20)\nrun = session.get_run(page.items[0].run_id)",
            ),
        )


class SessionGraphLimitError(AnalysisError):
    @classmethod
    def for_value(cls, value: object) -> SessionGraphLimitError:
        return cls(
            message="Session graph max_nodes is outside the supported bound",
            expected="max_nodes within [1, 500]",
            received=repr(value),
            location="session.graph(max_nodes=...)",
            repair=_runtime_read_repair(
                action="Pass max_nodes within [1, 500].",
                target="session.graph",
                snippet="graph = session.graph(max_nodes=100)",
            ),
        )


class SessionGraphArgumentError(AnalysisError):
    @classmethod
    def invalid(cls, *, artifact_ref: str | None, direction: object) -> SessionGraphArgumentError:
        return cls(
            message="Session graph focus arguments do not describe a supported traversal",
            expected=(
                "direction='ancestors' for an overall graph, or an exact artifact_ref with "
                "direction='ancestors'|'descendants'"
            ),
            received=f"artifact_ref={artifact_ref!r}, direction={direction!r}",
            location="session.graph(...) arguments",
            repair=_runtime_read_repair(
                action="Pass one exact Artifact ref when requesting descendant traversal.",
                target="session.graph",
                snippet=(
                    "graph = session.graph(artifact_ref='<ref>', "
                    "direction='descendants', max_nodes=100)"
                ),
            ),
        )


class SessionGraphTooLargeError(AnalysisError):
    @classmethod
    def for_count(cls, *, count: int, limit: int) -> SessionGraphTooLargeError:
        return cls(
            message="Session runtime history exceeds the bounded overall graph scan",
            expected=f"at most {limit} combined Run and Artifact records",
            received=str(count),
            location="session.graph() overall scan",
            repair=_runtime_read_repair(
                action="Page Runs to obtain an exact Artifact ref, then request one focused graph direction.",
                target="session.runs",
                snippet=(
                    "page = session.runs(limit=20)\n"
                    "graph = session.graph(artifact_ref='<ref>', direction='ancestors')"
                ),
            ),
        )


class SessionGraphIntegrityError(AnalysisError):
    @classmethod
    def mismatch(
        cls, *, message: str, expected: str, received: str, location: str
    ) -> SessionGraphIntegrityError:
        return cls(
            message=message,
            expected=expected,
            received=received,
            location=location,
            repair=_runtime_read_repair(
                action=(
                    "Inspect the named Run and Artifact records, then regenerate the computation "
                    "in a fresh Session when canonical storage is corrupt."
                ),
                target="session.graph",
            ),
        )
