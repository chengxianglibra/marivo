"""Typed public analysis repairs and errors with analysis Help targets."""

from __future__ import annotations

from typing import Literal

from marivo._kernel.errors import LiveHelpTarget
from marivo.analysis.model.errors import AnalysisError as AnalysisError
from marivo.analysis.model.errors import AnalysisRepair as AnalysisRepair
from marivo.analysis.model.errors import RepairKind as RepairKind
from marivo.introspection.live.targets import canonical_target


class TimezoneInvalidError(AnalysisError):
    pass


class ReportTimezoneUnresolved(AnalysisError):  # noqa: N818 - canonical public error name
    """The host does not identify a report timezone for a new Session."""

    def __init__(self, *, source: str, received: str) -> None:
        self.source = source
        self.system_value = received
        super().__init__(
            message="The system report timezone could not be resolved.",
            expected="a loadable system IANA timezone or an explicit Session timezone",
            received=f"{source}={received!r}",
            location="mv.session.get_or_create(timezone=...)",
            context={"source": source, "system_value": received, "query_executed": False},
            repair=AnalysisRepair(
                kind="retry",
                action="Pass an explicit IANA timezone or UTC offset for this analysis.",
                snippet='session = mv.session.get_or_create(name, timezone="...")',
                help_target=LiveHelpTarget(
                    surface="analysis", canonical_id="session.get_or_create"
                ),
            ),
        )


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
