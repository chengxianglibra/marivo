"""Typed datasource errors and recovery actions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from marivo.introspection.live.errors import ContractScopeErrorPayload, HelpTargetErrorPayload
from marivo.introspection.live.model import AuthoringRepair, LiveHelpTarget

ScopeState = Literal["known", "none", "unknown"]


class DatasourceObservedEffects(BaseModel):
    """Facts observed before or during one datasource operation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query_executed: bool
    scope_state: ScopeState | None = None


def repair(
    *,
    kind: Literal[
        "retry",
        "configure",
        "register",
        "reconnect",
        "inspect",
        "rescope",
        "reacquire",
        "reauthor",
        "reload",
        "reverify",
        "repreview",
        "environment",
    ],
    canonical_id: str,
    action: str,
    snippet: str | None = None,
    candidates: tuple[str, ...] = (),
    preserves_evidence: bool | None = None,
) -> AuthoringRepair:
    """Construct a datasource-owned typed repair."""
    return AuthoringRepair(
        kind=kind,
        help_target=LiveHelpTarget(surface="datasource", canonical_id=canonical_id),
        action=action,
        snippet=snippet,
        candidates=candidates,
        preserves_evidence=preserves_evidence,
    )


class DatasourceError(Exception):
    """Base datasource error with the stable recovery field set."""

    def __init__(
        self,
        *,
        message: str,
        expected: str | None = None,
        received: str | None = None,
        location: str | None = None,
        effect_observed: DatasourceObservedEffects | None = None,
        repair: AuthoringRepair | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.expected = expected
        self.received = received
        self.location = location
        self.effect_observed = effect_observed
        self.repair = repair

    def __str__(self) -> str:
        lines = [f"{type(self).__name__}: {self.message}"]
        for label, value in (
            ("Location", self.location),
            ("Expected", self.expected),
            ("Received", self.received),
        ):
            if value is not None:
                lines.append(f"{label}: {value}")
        if self.effect_observed is not None:
            lines.append(f"Query executed: {self.effect_observed.query_executed}")
            if self.effect_observed.scope_state is not None:
                lines.append(f"Scope state: {self.effect_observed.scope_state}")
        if self.repair is not None:
            lines.extend(("", "Repair:", f"  {self.repair.action}"))
            if self.repair.snippet is not None:
                lines.extend(f"  {line}" for line in self.repair.snippet.splitlines())
            if self.repair.candidates:
                lines.append(f"  Candidates: {', '.join(self.repair.candidates)}")
            if self.repair.preserves_evidence is not None:
                lines.append(f"  Preserves evidence: {self.repair.preserves_evidence}")
            target = self.repair.help_target
            if target.canonical_id is not None:
                lines.append(f"Help: md.help({target.canonical_id!r})")
        return "\n".join(lines)


class DatasourceAuthoringError(DatasourceError):
    """Blocked authoring operation with explicit non-execution evidence."""

    def __init__(
        self,
        *,
        code: str,
        stage: Literal["inspect", "preflight", "acquire", "cache", "project"],
        expected: str,
        received: str,
        reason: str,
        effect_observed: DatasourceObservedEffects,
        repair: AuthoringRepair,
    ) -> None:
        del code, stage
        super().__init__(
            message=reason,
            expected=expected,
            received=received,
            effect_observed=effect_observed,
            repair=repair,
        )


class DatasourceHelpTargetError(DatasourceError):
    """Datasource-owned rejection of an unsupported live help target."""

    def __init__(self, payload: HelpTargetErrorPayload) -> None:
        owning_surface = payload.surface or "unknown"
        super().__init__(
            message=payload.message,
            expected=f"accepted datasource help target ({', '.join(payload.accepted_kinds)})",
            received=payload.received,
            location=f"{owning_surface} help surface",
            repair=repair(
                kind="retry",
                canonical_id="help",
                action="Retry with a registered datasource help target.",
                candidates=payload.candidates,
            ),
        )


class DatasourceContractScopeError(DatasourceError):
    """Datasource-owned rejection of an over-broad contract request."""

    def __init__(self, payload: ContractScopeErrorPayload) -> None:
        super().__init__(
            message=payload.message,
            expected=f"at most {payload.allowed_maximum} datasource subjects",
            received=", ".join(payload.requested_subjects),
            location="datasource contract scope",
            repair=AuthoringRepair(
                kind="retry",
                help_target=payload.repair_target,
                action="Narrow subject_refs to datasource-owned candidates.",
                candidates=payload.owned_subjects,
            ),
        )


class DatasourceSecretInPlaintextError(DatasourceError):
    pass


class DatasourceFieldInvalidError(DatasourceError):
    pass


class DatasourceLoadError(DatasourceError):
    pass


class DatasourceDuplicateError(DatasourceError):
    pass


class DatasourceMissingError(DatasourceError):
    pass


class DatasourceSecretStorePermissionsError(DatasourceError):
    pass


class DatasourceEnvVarMissingError(DatasourceError):
    pass


class DatasourceBackendTypeUnsupportedError(DatasourceError):
    pass


class DatasourceSchemaVersionError(DatasourceError):
    pass


class DatasourceConnectionError(DatasourceError):
    pass


class DatasourcePreviewError(DatasourceError):
    pass


class DatasourceMetadataError(DatasourceError):
    pass


class DatasourceRawSqlError(DatasourceError):
    pass
