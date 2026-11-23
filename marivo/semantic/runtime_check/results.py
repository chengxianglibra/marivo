"""Immutable online validation results and their safe renderings."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from typing_extensions import override

from marivo._authoring.model import AuthoringRepair
from marivo._authoring.render import _repair_summary
from marivo._kernel.refs import (
    DatasourceKind,
    EntityKind,
    Ref,
    SemanticKindTag,
)
from marivo._kernel.render import Card, RenderableResult
from marivo._kernel.symbolic import (
    DatasourceSymbol,
    EntitySymbol,
    LookupSymbol,
    symbol_factory,
    symbol_from_ref,
)
from marivo.datasource.spi import AuthoringScope, EntitySourceIR
from marivo.semantic.runtime_check.source_checks import (
    ValidationCheckKind,
    ValidationStatus,
)
from marivo.telemetry import traced


def _scope_dict(scope: AuthoringScope) -> dict[str, object]:
    payload: dict[str, object] = {
        "kind": "unpruned",
        "max_rows": scope.max_rows,
        "timeout_seconds": scope.timeout_seconds,
    }
    time_range = getattr(scope, "_time_range", None)
    if time_range is not None:
        payload.update(
            {
                "kind": "time_range",
                "column": time_range.column,
                "start": time_range.start.isoformat(),
                "end": time_range.end.isoformat(),
            }
        )
    elif hasattr(scope, "values"):
        payload.update({"kind": "partition", "values": dict(scope.values)})
    return payload


def _repair_dict(value: AuthoringRepair | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "kind": value.kind,
        "help_target": f"{value.help_target.surface}.{value.help_target.canonical_id}",
        "action": value.action,
        "snippet": value.snippet,
        "candidates": list(value.candidates),
    }


@dataclass(frozen=True, repr=False, slots=True)
class ValidationCheckResult(RenderableResult):
    kind: ValidationCheckKind
    status: ValidationStatus
    _datasource: Ref[DatasourceKind]
    source: EntitySourceIR
    _target_refs: tuple[Ref[SemanticKindTag], ...]
    _affected_refs: tuple[Ref[SemanticKindTag], ...]
    checked_at: str
    observed_schema_fingerprint: str | None
    observed_capability_fingerprint: str | None
    observed: Mapping[str, object]
    repair: AuthoringRepair | None
    user_data_queried: bool
    _scopes: tuple[tuple[Ref[EntityKind], AuthoringScope], ...] = ()

    @property
    def datasource(self) -> DatasourceSymbol:
        return symbol_factory.datasource(self._datasource.path)

    @property
    def target_refs(self) -> tuple[LookupSymbol, ...]:
        return tuple(symbol_from_ref(ref) for ref in self._target_refs)

    @property
    def affected_refs(self) -> tuple[LookupSymbol, ...]:
        return tuple(symbol_from_ref(ref) for ref in self._affected_refs)

    @property
    def scopes(self) -> tuple[tuple[EntitySymbol, AuthoringScope], ...]:
        return tuple((symbol_factory.entity(ref.path), scope) for ref, scope in self._scopes)

    @override
    def _repr_identity(self) -> str:
        return (
            f"ValidationCheckResult kind={self.kind} status={self.status} "
            f"targets={len(self._target_refs)}"
        )

    @override
    def _card(self) -> Card:
        card = Card(
            identity=self._repr_identity(),
            available=(
                ".observed",
                ".affected_refs",
                ".scopes",
                ".repair",
                ".show()",
                ".to_dict()",
            ),
        ).status(
            f"user_data_queried={self.user_data_queried} "
            f"datasource={self._datasource.key} source={self.source.kind}"
        )
        if self._affected_refs:
            card = card.listing("affected refs", (ref.key for ref in self._affected_refs))
        if self._scopes:
            card = card.listing(
                "scopes",
                (
                    f"{entity_ref.key}: {json.dumps(_scope_dict(scope), sort_keys=True, default=str)}"
                    for entity_ref, scope in self._scopes
                ),
            )
        if self.observed:
            card = card.field(
                "observed", json.dumps(dict(self.observed), sort_keys=True, default=str)
            )
        if self.repair is not None:
            card = card.field("repair", _repair_summary(self.repair))
        return card.field("checked_at", self.checked_at)

    @traced(surface="semantic", operation="validation_check_result.to_dict", kind="method")
    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "status": self.status,
            "datasource": self._datasource.key,
            "source": self.source.to_dict(),
            "target_refs": [ref.key for ref in self._target_refs],
            "affected_refs": [ref.key for ref in self._affected_refs],
            "checked_at": self.checked_at,
            "observed_schema_fingerprint": self.observed_schema_fingerprint,
            "observed_capability_fingerprint": self.observed_capability_fingerprint,
            "observed": dict(self.observed),
            "repair": _repair_dict(self.repair),
            "user_data_queried": self.user_data_queried,
            "scopes": [
                {"entity": entity_ref.key, "scope": _scope_dict(scope)}
                for entity_ref, scope in self._scopes
            ],
        }


@dataclass(frozen=True, repr=False, slots=True)
class ValidationReport(RenderableResult):
    status: ValidationStatus
    checks: tuple[ValidationCheckResult, ...]
    checked_at: str
    catalog_definition_fingerprint: str

    @property
    def _affected_refs(self) -> tuple[Ref[SemanticKindTag], ...]:
        return tuple(
            dict.fromkeys(ref for check in self.checks for ref in check_affected_refs(check))
        )

    @property
    def affected_refs(self) -> tuple[LookupSymbol, ...]:
        return tuple(symbol_from_ref(ref) for ref in self._affected_refs)

    @override
    def _repr_identity(self) -> str:
        return f"ValidationReport status={self.status} checks={len(self.checks)}"

    @override
    def _card(self) -> Card:
        card = Card(
            identity=self._repr_identity(),
            available=(".checks", ".affected_refs", ".show()", ".to_dict()"),
        )
        failures = tuple(check for check in self.checks if check.status != "current")
        if failures:
            card = card.listing(
                "non-successful checks",
                (
                    f"{check.kind}: {check.status}; affected refs: "
                    f"{', '.join(ref.key for ref in check_affected_refs(check)) or 'none'}"
                    + (f" -> {_repair_summary(check.repair)}" if check.repair is not None else "")
                    for check in failures
                ),
            )
        card = card.table(
            columns=("check", "status", "data query", "affected refs"),
            rows=(
                (
                    check.kind,
                    check.status,
                    "yes" if check.user_data_queried else "no",
                    str(len(check_affected_refs(check))),
                )
                for check in self.checks
            ),
            row_count=len(self.checks),
            label="online validation",
            show_omission_counts=True,
        )
        return card.field("checked_at", self.checked_at)

    @traced(surface="semantic", operation="validation_report.to_dict", kind="method")
    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "checks": [check.to_dict() for check in self.checks],
            "affected_refs": [ref.key for ref in self._affected_refs],
            "checked_at": self.checked_at,
            "catalog_definition_fingerprint": self.catalog_definition_fingerprint,
        }


def check_affected_refs(check: ValidationCheckResult) -> tuple[Ref[SemanticKindTag], ...]:
    return check._affected_refs  # noqa: SLF001 - Report aggregation uses exact captured identities.
