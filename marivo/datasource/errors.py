"""Typed datasource errors and recovery actions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from marivo._authoring.errors import ContractScopeErrorPayload
from marivo._authoring.model import AuthoringRepair
from marivo.introspection.live.errors import HelpTargetErrorPayload
from marivo.introspection.live.model import LiveHelpTarget

ScopeState = Literal["known", "none", "unknown"]

_BACKEND_CODE_RE = re.compile(r"\bCode:\s*([A-Za-z0-9_.-]+)")
_BACKEND_NAME_RE = re.compile(r"\(([A-Z][A-Z0-9_]+)\)")
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?P<key>[\"']?(?:password|passwd|pwd|token|secret|auth|authorization|"
    r"authentication)[\"']?)\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_URL_USERINFO_RE = re.compile(r"://[^/@\s]+(?::[^/@\s]*)?@")
_MAX_BACKEND_MESSAGE_CODEPOINTS = 500


@dataclass(frozen=True)
class _BackendFailureSummary:
    exception_type: str
    backend_code: str | None
    backend_name: str | None
    message: str

    @property
    def identity(self) -> str:
        parts = [self.exception_type]
        if self.backend_code is not None:
            parts.append(f"code={self.backend_code}")
        if self.backend_name is not None:
            parts.append(f"name={self.backend_name}")
        return " ".join(parts)


def _backend_failure_summary(exc: Exception) -> _BackendFailureSummary:
    """Return a bounded diagnostic that does not expose common secret shapes."""
    raw_message = str(exc)
    message = _URL_USERINFO_RE.sub("://<redacted>@", raw_message)
    message = _SECRET_VALUE_RE.sub(lambda match: f"{match.group('key')}=<redacted>", message)
    if len(message) > _MAX_BACKEND_MESSAGE_CODEPOINTS:
        message = message[: _MAX_BACKEND_MESSAGE_CODEPOINTS - 3] + "..."

    raw_code = getattr(exc, "code", None)
    if raw_code is None:
        code_match = _BACKEND_CODE_RE.search(raw_message)
        backend_code = code_match.group(1) if code_match is not None else None
    else:
        backend_code = str(raw_code)

    raw_name = getattr(exc, "name", None)
    backend_name: str | None
    if isinstance(raw_name, str) and re.fullmatch(r"[A-Z][A-Z0-9_]+", raw_name):
        backend_name = raw_name
    else:
        name_matches = _BACKEND_NAME_RE.findall(raw_message)
        backend_name = name_matches[-1] if name_matches else None

    return _BackendFailureSummary(
        exception_type=type(exc).__name__,
        backend_code=backend_code,
        backend_name=backend_name,
        message=message,
    )


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
                qualified = f"{target.surface}.{target.canonical_id}"
                lines.append(f"Help: marivo.help({qualified!r})")
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
        self.code = code
        self.stage = stage
        super().__init__(
            message=reason,
            expected=expected,
            received=received,
            effect_observed=effect_observed,
            repair=repair,
        )

    def __str__(self) -> str:
        rendered = super().__str__().splitlines()
        rendered[1:1] = (f"Code: {self.code}", f"Stage: {self.stage}")
        return "\n".join(rendered)


class DatasourceHelpTargetError(DatasourceError):
    """Datasource-owned rejection of an unsupported live help target."""

    def __init__(self, payload: HelpTargetErrorPayload) -> None:
        owning_surface = payload.surface or "unknown"
        super().__init__(
            message=payload.message,
            expected=f"accepted datasource help target ({', '.join(payload.accepted_kinds)})",
            received=payload.received,
            location=f"{owning_surface} help surface",
            repair=AuthoringRepair(
                kind="retry",
                help_target=LiveHelpTarget(surface="datasource"),
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


class DatasourceSourceCapabilityError(DatasourceError):
    """A datasource backend cannot realize the requested physical source."""


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
