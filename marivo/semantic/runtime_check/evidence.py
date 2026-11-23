"""Capture source evidence and structured failure observations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from types import MappingProxyType

from marivo._authoring.model import AuthoringRepair
from marivo._kernel.refs import (
    DatasourceKind,
    EntityKind,
    Ref,
    SemanticKindTag,
)
from marivo.datasource.errors import (
    DatasourceAuthoringError,
)
from marivo.datasource.spi import (
    AuthoringScope,
    EntitySourceIR,
    SourceInspection,
    backend_failure_summary,
    source_unavailability,
)
from marivo.semantic.errors import repair
from marivo.semantic.runtime_check.results import (
    ValidationCheckResult,
)
from marivo.semantic.runtime_check.source_checks import (
    ValidationCheckKind,
    ValidationStatus,
)


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _result(
    *,
    kind: ValidationCheckKind,
    status: ValidationStatus,
    datasource: Ref[DatasourceKind],
    source: EntitySourceIR,
    target_refs: Sequence[Ref[SemanticKindTag]],
    affected_refs: Sequence[Ref[SemanticKindTag]],
    checked_at: str,
    inspection: SourceInspection | None,
    observed: Mapping[str, object],
    repair_value: AuthoringRepair | None,
    user_data_queried: bool,
    scopes: Sequence[tuple[Ref[EntityKind], AuthoringScope]] = (),
) -> ValidationCheckResult:
    schema_fingerprint = None
    capability_fingerprint = None
    if inspection is not None:
        schema_fingerprint = _fingerprint(
            [(column.name, column.type, column.nullable) for column in inspection.schema]
        )
        capability_fingerprint = _fingerprint(asdict(inspection.execution_capabilities))
    return ValidationCheckResult(
        kind=kind,
        status=status,
        _datasource=datasource,
        source=source,
        _target_refs=tuple(target_refs),
        _affected_refs=tuple(affected_refs),
        checked_at=checked_at,
        observed_schema_fingerprint=schema_fingerprint,
        observed_capability_fingerprint=capability_fingerprint,
        observed=MappingProxyType(dict(observed)),
        repair=repair_value,
        user_data_queried=user_data_queried,
        _scopes=tuple(scopes),
    )


def _unavailable_observed(exc: Exception) -> dict[str, object]:
    failure = backend_failure_summary(exc)
    return {
        "code": source_unavailability(failure),
        "failure_identity": failure.identity,
        "message": failure.message,
    }


def _unavailable_repair(observed: Mapping[str, object]) -> AuthoringRepair:
    code = observed["code"]
    match code:
        case "permission_denied":
            action = "Check access to the datasource operation or object named in the error."
        case "unsupported":
            action = "Check which datasource operations the backend supports."
        case "source_unavailable":
            action = "Verify the physical source name and namespace in the declaration."
        case "connection_unavailable":
            action = "Check datasource reachability and the declared connection settings."
        case "timeout":
            action = "Check backend responsiveness and the applicable timeout settings."
        case _:
            action = "Review the backend error details before retrying validation."
    return repair(
        kind="reconnect" if code in ("connection_unavailable", "timeout") else "inspect",
        canonical_id="validate",
        action=action,
    )


def _authoring_failure_observed(exc: DatasourceAuthoringError) -> dict[str, object]:
    failure = backend_failure_summary(exc)
    return {
        "code": exc.code,
        "stage": exc.stage,
        "failure_identity": failure.identity,
        "message": failure.message,
    }
