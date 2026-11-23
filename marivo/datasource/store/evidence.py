"""Immutable authoring snapshots acquired by one bounded user-data query."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, TypeAlias

from typing_extensions import override

from marivo._kernel.refs import DatasourceKind, Ref
from marivo._kernel.render import Card, RenderableResult
from marivo._kernel.symbolic import DatasourceSymbol, symbol_factory
from marivo.datasource.ir import (
    QueryParamScalar,
    QueryParamScalarList,
)
from marivo.datasource.source import AuthoringScope, TableSource

JsonScalar: TypeAlias = str | int | float | bool | None


def _null_rate_text(null_count: int, row_count: int) -> str:
    rate = 0.0 if row_count == 0 else null_count / row_count
    return f"{rate:.2%}"


@dataclass(frozen=True)
class SnapshotCoverage:
    observed_row_count: int
    retained_row_count: int
    scope_exhaustion: Literal["exhaustive", "truncated"]
    scope_exactness: Literal["scope_exact", "sample_only"]
    sampling_method: Literal["first_rows_limit"]
    pushed_predicate: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class DeterministicMatch:
    rule: str
    checked: int
    matched: int
    failed: int
    role: Literal["value", "component_only"]


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    data_type: str
    nullable: bool | None
    partition_role: bool
    sample_row_count: int
    sample_null_count: int
    sample_empty_count: int
    sample_distinct_count: int
    scope_distinct_count: int | None
    scope_distinct_lower_bound: int
    min_value: JsonScalar | None
    max_value: JsonScalar | None
    negative_count: int
    zero_count: int
    min_length: int | None
    max_length: int | None
    avg_length: float | None
    character_patterns: tuple[tuple[str, int], ...]
    top_values: tuple[tuple[JsonScalar, int], ...] | None
    display_samples: tuple[JsonScalar, ...] | None
    frequency_capacity: int
    deterministic_matches: tuple[DeterministicMatch, ...]
    name_suffix: str | None
    url_syntax_checked: int
    url_syntax_matched: int


@dataclass(frozen=True, repr=False)
class DiscoveryEvidence(RenderableResult):
    """Immutable bounded rows and profiles from one explicit source acquisition."""

    id: str
    _datasource: Ref[DatasourceKind]
    source: TableSource
    scope: AuthoringScope
    columns: tuple[str, ...]
    schema_fingerprint: str
    profiles: tuple[ColumnProfile, ...]
    coverage: SnapshotCoverage
    persist_values: bool
    value_evidence_state: Literal["available", "value_evidence_unavailable"]
    cache_status: Literal["fresh", "cached", "stale", "mismatched"]
    created_at: datetime
    expires_at: datetime
    _project_root: Path
    source_params: tuple[tuple[str, QueryParamScalar | QueryParamScalarList], ...] = ()
    retained_values: tuple[dict[str, JsonScalar], ...] = ()

    @property
    def datasource(self) -> DatasourceSymbol:
        """Native lookup selector for the acquired datasource."""
        return symbol_factory.datasource(self._datasource.path)

    @override
    def _repr_identity(self) -> str:
        return (
            f"DiscoveryEvidence id={self.id} datasource={self._datasource.path} "
            f"columns={len(self.columns)} rows={self.coverage.retained_row_count}"
        )

    @override
    def _card(self) -> Card:
        return (
            Card(
                identity=self._repr_identity(),
                available=(
                    ".profiles",
                    ".coverage",
                    ".source_params",
                    ".retained_values",
                    ".show()",
                ),
            )
            .status(
                f"cache={self.cache_status} exhaustion={self.coverage.scope_exhaustion} "
                f"sampling={self.coverage.sampling_method}"
            )
            .field("scope", repr(self.scope))
            .field(
                "source params",
                json.dumps(dict(self.source_params), sort_keys=True, separators=(",", ":")),
            )
            .field("selected columns", ", ".join(self.columns))
            .field(
                "coverage",
                (
                    f"observed_rows={self.coverage.observed_row_count} "
                    f"retained_rows={self.coverage.retained_row_count} "
                    f"scope_exactness={self.coverage.scope_exactness}"
                ),
            )
            .field(
                "value/cache state",
                (
                    f"value_evidence={self.value_evidence_state} "
                    f"persist_values={self.persist_values} cache={self.cache_status}"
                ),
            )
            .field("retained row shape", "tuple[dict[str, JsonScalar], ...]")
            .field("typed consumers", "none; snapshot is generic evidence")
            .field(
                "reacquire boundary",
                "only missing columns, missing retained values, stale evidence, or identity mismatch",
            )
            .table(
                columns=("column", "type", "nulls", "null_rate", "distinct"),
                rows=(
                    (
                        profile.name,
                        profile.data_type,
                        str(profile.sample_null_count),
                        _null_rate_text(profile.sample_null_count, profile.sample_row_count),
                        str(profile.sample_distinct_count),
                    )
                    for profile in self.profiles
                ),
                row_count=len(self.profiles),
                label="profiles",
            )
        )


def evidence_datasource(snapshot: DiscoveryEvidence) -> Ref[DatasourceKind]:
    return snapshot._datasource  # noqa: SLF001 - Preserve the exact identity in stored evidence.
