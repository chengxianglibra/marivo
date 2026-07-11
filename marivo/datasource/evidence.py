"""Pure semantic-shaped projections from immutable authoring snapshots."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from marivo.datasource.source import AuthoringScope
from marivo.render import Card, RenderableResult

type EvidenceValue = str | int | float | bool | None

TIME_RULE_IDS = (
    "type.native_date",
    "type.native_timestamp",
    "date.iso",
    "datetime.iso",
    "date.yyyymmdd",
    "time.hour_00_23",
)

_ENTITY_UNRESOLVED = (
    "business_identity",
    "cross_partition_uniqueness",
    "temporal_stability",
    "key_reuse",
)
_DIMENSION_UNRESOLVED = (
    "category_meaning",
    "label_semantics",
    "privacy_policy",
    "business_definition",
)
_TIME_UNRESOLVED = (
    "business_event_time",
    "timezone",
    "default_time_dimension",
)
_MEASURE_UNRESOLVED = (
    "aggregation",
    "unit",
    "additivity",
    "business_definition",
)


@dataclass(frozen=True)
class EntityColumnEvidence:
    column: str
    profile: ColumnProfile
    sample_unique: bool
    name_suffix: str | None
    url_syntax_checked: int
    url_syntax_matched: int
    unresolved: tuple[str, ...]


@dataclass(frozen=True)
class DimensionColumnEvidence:
    column: str
    profile: ColumnProfile
    sample_values_complete: bool
    scope_values_complete: bool
    unresolved: tuple[str, ...]


@dataclass(frozen=True)
class TimeColumnEvidence:
    column: str
    profile: ColumnProfile
    deterministic_matches: tuple[DeterministicMatch, ...]
    unresolved: tuple[str, ...]


@dataclass(frozen=True)
class MeasureColumnEvidence:
    column: str
    profile: ColumnProfile
    unresolved: tuple[str, ...]


@dataclass(frozen=True, repr=False)
class EntityEvidenceResult(RenderableResult):
    status: Literal["complete", "incomplete"]
    columns: tuple[str, ...]
    evidence_by_column: Mapping[str, EntityColumnEvidence]
    issues: tuple[str, ...]
    next_calls: tuple[str, ...]

    def _repr_identity(self) -> str:
        return f"EntityEvidenceResult status={self.status} columns={len(self.columns)}"

    def _card(self) -> Card:
        return _column_result_card(
            identity=self._repr_identity(),
            status=self.status,
            columns=(
                "column",
                "type",
                "sample_rows",
                "sample_nulls",
                "sample_distinct",
                "sample_unique",
                "name_suffix",
                "url_syntax",
            ),
            rows=(
                (
                    column,
                    evidence.profile.data_type,
                    str(evidence.profile.sample_row_count),
                    str(evidence.profile.sample_null_count),
                    str(evidence.profile.sample_distinct_count),
                    str(evidence.sample_unique),
                    evidence.name_suffix or "none",
                    f"{evidence.url_syntax_matched}/{evidence.url_syntax_checked}",
                )
                for column, evidence in self.evidence_by_column.items()
            ),
            row_count=len(self.evidence_by_column),
            issues=self.issues,
            next_calls=self.next_calls,
        )


@dataclass(frozen=True, repr=False)
class DimensionEvidenceResult(RenderableResult):
    status: Literal["complete", "incomplete"]
    columns: tuple[str, ...]
    evidence_by_column: Mapping[str, DimensionColumnEvidence]
    issues: tuple[str, ...]
    next_calls: tuple[str, ...]

    def _repr_identity(self) -> str:
        return f"DimensionEvidenceResult status={self.status} columns={len(self.columns)}"

    def _card(self) -> Card:
        return _column_result_card(
            identity=self._repr_identity(),
            status=self.status,
            columns=(
                "column",
                "type",
                "sample_nulls",
                "sample_empty",
                "sample_distinct",
                "sample_values_complete",
                "scope_values_complete",
                "value_evidence_state",
                "partition",
            ),
            rows=(
                (
                    column,
                    evidence.profile.data_type,
                    str(evidence.profile.sample_null_count),
                    str(evidence.profile.sample_empty_count),
                    str(evidence.profile.sample_distinct_count),
                    str(evidence.sample_values_complete),
                    str(evidence.scope_values_complete),
                    ("unavailable" if evidence.profile.top_values is None else "available"),
                    str(evidence.profile.partition_role),
                )
                for column, evidence in self.evidence_by_column.items()
            ),
            row_count=len(self.evidence_by_column),
            issues=self.issues,
            next_calls=self.next_calls,
        )


@dataclass(frozen=True, repr=False)
class TimeEvidenceResult(RenderableResult):
    status: Literal["complete", "incomplete"]
    columns: tuple[str, ...]
    evidence_by_column: Mapping[str, TimeColumnEvidence]
    issues: tuple[str, ...]
    next_calls: tuple[str, ...]

    def _repr_identity(self) -> str:
        return f"TimeEvidenceResult status={self.status} columns={len(self.columns)}"

    def _card(self) -> Card:
        return _column_result_card(
            identity=self._repr_identity(),
            status=self.status,
            columns=("column", "type", "rule", "checked", "matched", "failed", "role"),
            rows=_time_evidence_rows(self.evidence_by_column),
            row_count=sum(
                max(1, len(evidence.deterministic_matches))
                for evidence in self.evidence_by_column.values()
            ),
            issues=self.issues,
            next_calls=self.next_calls,
        )


@dataclass(frozen=True, repr=False)
class MeasureEvidenceResult(RenderableResult):
    status: Literal["complete", "incomplete"]
    columns: tuple[str, ...]
    evidence_by_column: Mapping[str, MeasureColumnEvidence]
    issues: tuple[str, ...]
    next_calls: tuple[str, ...]

    def _repr_identity(self) -> str:
        return f"MeasureEvidenceResult status={self.status} columns={len(self.columns)}"

    def _card(self) -> Card:
        return _column_result_card(
            identity=self._repr_identity(),
            status=self.status,
            columns=(
                "column",
                "type",
                "sample_nulls",
                "sample_distinct",
                "min",
                "max",
                "negative",
                "zero",
                "value_evidence_state",
            ),
            rows=(
                (
                    column,
                    evidence.profile.data_type,
                    str(evidence.profile.sample_null_count),
                    str(evidence.profile.sample_distinct_count),
                    str(evidence.profile.min_value),
                    str(evidence.profile.max_value),
                    str(evidence.profile.negative_count),
                    str(evidence.profile.zero_count),
                    ("unavailable" if evidence.profile.top_values is None else "available"),
                )
                for column, evidence in self.evidence_by_column.items()
            ),
            row_count=len(self.evidence_by_column),
            issues=self.issues,
            next_calls=self.next_calls,
        )


@dataclass(frozen=True, repr=False)
class DimensionValuesResult(RenderableResult):
    status: Literal["complete", "incomplete"]
    snapshot_id: str
    column: str
    sample_distinct_count: int
    returned_value_count: int | None
    sample_values_complete: bool
    scope_values_complete: bool
    value_evidence_state: Literal["available", "value_evidence_unavailable"]
    frequency_capacity: int
    values: tuple[tuple[EvidenceValue, int], ...] | None
    issues: tuple[str, ...]
    next_calls: tuple[str, ...]

    def _repr_identity(self) -> str:
        return (
            f"DimensionValuesResult status={self.status} snapshot={self.snapshot_id} "
            f"column={self.column} "
            f"values={_available_value(self.returned_value_count)}"
        )

    def _card(self) -> Card:
        card = Card(
            identity=self._repr_identity(),
            available=(
                ".values",
                ".sample_distinct_count",
                ".sample_values_complete",
                ".scope_values_complete",
                ".issues",
                ".next_calls",
                ".render(max_output_bytes=...)",
                ".show(max_output_bytes=...)",
            ),
        ).status(self.status)
        card.field("value_evidence_state", self.value_evidence_state)
        card.field("sample_distinct_count", str(self.sample_distinct_count))
        card.field("frequency_capacity", str(self.frequency_capacity))
        card.field("sample_values_complete", str(self.sample_values_complete))
        card.field("scope_values_complete", str(self.scope_values_complete))
        if self.values is None:
            card.field("values", "unavailable")
        else:
            card.table(
                columns=("value", "count"),
                rows=((str(value), str(count)) for value, count in self.values),
                row_count=len(self.values),
                label="values",
                show_omission_counts=True,
            )
        if self.issues:
            card.listing("issues", self.issues)
        if self.next_calls:
            card.listing("Next calls", self.next_calls)
        return card


@dataclass(frozen=True, repr=False)
class RelationshipEvidenceResult(RenderableResult):
    status: Literal["complete", "incomplete"]
    left_snapshot_id: str
    right_snapshot_id: str
    left_scope: AuthoringScope
    right_scope: AuthoringScope
    left: tuple[str, ...]
    right: tuple[str, ...]
    left_profile: ColumnProfile | None
    right_profile: ColumnProfile | None
    type_compatible: bool | None
    evidence_state: Literal["available", "unavailable"]
    retained_overlap_count: int | None
    retained_left_orphan_count: int | None
    retained_right_orphan_count: int | None
    scope_comparability: Literal["unresolved"]
    issues: tuple[str, ...]
    next_calls: tuple[str, ...]

    def _repr_identity(self) -> str:
        return (
            f"RelationshipEvidenceResult status={self.status} "
            f"left={self.left_snapshot_id} right={self.right_snapshot_id}"
        )

    def _card(self) -> Card:
        card = Card(
            identity=self._repr_identity(),
            available=(
                ".left_scope",
                ".right_scope",
                ".left_profile",
                ".right_profile",
                ".issues",
                ".next_calls",
                ".render(max_output_bytes=...)",
                ".show(max_output_bytes=...)",
            ),
        ).status(self.status)
        card.field("columns", f"left={self.left!r} right={self.right!r}")
        card.field("left_scope", repr(self.left_scope))
        card.field("right_scope", repr(self.right_scope))
        card.field("type_compatible", _available_value(self.type_compatible))
        card.field("evidence_state", self.evidence_state)
        card.field(
            "retained_counts_scope",
            "retained values within left_scope and right_scope only",
        )
        card.field("retained_overlap_count", _available_value(self.retained_overlap_count))
        card.field(
            "retained_left_orphan_count",
            _available_value(self.retained_left_orphan_count),
        )
        card.field(
            "retained_right_orphan_count",
            _available_value(self.retained_right_orphan_count),
        )
        card.field("scope_comparability", self.scope_comparability)
        if self.issues:
            card.listing("issues", self.issues)
        if self.next_calls:
            card.listing("Next calls", self.next_calls)
        return card


def _available_value(value: object | None) -> str:
    return "unavailable" if value is None else str(value)


def _time_evidence_rows(
    evidence_by_column: Mapping[str, TimeColumnEvidence],
) -> Iterable[tuple[str, ...]]:
    for column, evidence in evidence_by_column.items():
        if not evidence.deterministic_matches:
            checked = evidence.profile.sample_row_count - evidence.profile.sample_null_count
            yield (
                column,
                evidence.profile.data_type,
                "none",
                str(checked),
                "0",
                str(checked),
                "none",
            )
            continue
        for match in evidence.deterministic_matches:
            yield (
                column,
                evidence.profile.data_type,
                match.rule,
                str(match.checked),
                str(match.matched),
                str(match.failed),
                match.role,
            )


def _column_result_card(
    *,
    identity: str,
    status: Literal["complete", "incomplete"],
    columns: tuple[str, ...],
    rows: Iterable[Sequence[str]],
    row_count: int,
    issues: tuple[str, ...],
    next_calls: tuple[str, ...],
) -> Card:
    card = Card(
        identity=identity,
        available=(
            ".columns",
            ".evidence_by_column",
            ".issues",
            ".next_calls",
            ".render(max_output_bytes=...)",
            ".show(max_output_bytes=...)",
        ),
    ).status(status)
    card.table(
        columns=columns,
        rows=rows,
        row_count=row_count,
        label="evidence",
        show_omission_counts=True,
    )
    if issues:
        card.listing("issues", issues)
    if next_calls:
        card.listing("Next calls", next_calls)
    return card


def _profiles(snapshot: DiscoverySnapshot, columns: tuple[str, ...]) -> tuple[ColumnProfile, ...]:
    if not columns:
        raise ValueError("columns must contain at least one column")
    if len(set(columns)) != len(columns):
        raise ValueError(f"columns must not contain duplicates; received={columns!r}")
    missing = tuple(column for column in columns if column not in snapshot.columns)
    if missing:
        raise ValueError(
            f"columns must exist in snapshot.columns; missing={missing!r}; "
            f"available={snapshot.columns!r}"
        )
    profiles_by_name = {profile.name: profile for profile in snapshot.profiles}
    return tuple(profiles_by_name[column] for column in columns)


def _sample_values_complete(profile: ColumnProfile) -> bool:
    return (
        profile.top_values is not None and len(profile.top_values) == profile.sample_distinct_count
    )


def _value_projection_state(
    snapshot: DiscoverySnapshot, *, help_topic: str
) -> tuple[
    Literal["complete", "incomplete"],
    tuple[str, ...],
    tuple[str, ...],
]:
    if snapshot.value_evidence_state == "value_evidence_unavailable":
        return (
            "incomplete",
            ("value_evidence_unavailable",),
            ("md.inspect(...).sample(..., persist_values=True, refresh=True)",),
        )
    return "complete", (), (f"ms.help('{help_topic}')",)


def _project_entity(
    snapshot: DiscoverySnapshot, *, columns: tuple[str, ...]
) -> EntityEvidenceResult:
    profiles = _profiles(snapshot, columns)
    evidence = {
        profile.name: EntityColumnEvidence(
            column=profile.name,
            profile=profile,
            sample_unique=(
                profile.sample_null_count == 0
                and profile.sample_distinct_count == profile.sample_row_count
            ),
            name_suffix=profile.name_suffix,
            url_syntax_checked=profile.url_syntax_checked,
            url_syntax_matched=profile.url_syntax_matched,
            unresolved=_ENTITY_UNRESOLVED,
        )
        for profile in profiles
    }
    return EntityEvidenceResult(
        status="complete",
        columns=columns,
        evidence_by_column=MappingProxyType(evidence),
        issues=(),
        next_calls=("ms.help('entity')",),
    )


def _project_dimensions(
    snapshot: DiscoverySnapshot, *, columns: tuple[str, ...]
) -> DimensionEvidenceResult:
    profiles = _profiles(snapshot, columns)
    status, issues, next_calls = _value_projection_state(snapshot, help_topic="dimension")
    evidence: dict[str, DimensionColumnEvidence] = {}
    for profile in profiles:
        sample_complete = _sample_values_complete(profile)
        evidence[profile.name] = DimensionColumnEvidence(
            column=profile.name,
            profile=profile,
            sample_values_complete=sample_complete,
            scope_values_complete=(
                sample_complete and snapshot.coverage.scope_exhaustion == "exhaustive"
            ),
            unresolved=_DIMENSION_UNRESOLVED,
        )
    return DimensionEvidenceResult(
        status=status,
        columns=columns,
        evidence_by_column=MappingProxyType(evidence),
        issues=issues,
        next_calls=next_calls,
    )


def _project_values(
    snapshot: DiscoverySnapshot, column: str, *, limit: int
) -> DimensionValuesResult:
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")
    (profile,) = _profiles(snapshot, (column,))
    values = None if profile.top_values is None else profile.top_values[:limit]
    dictionary_complete = _sample_values_complete(profile)
    sample_complete = dictionary_complete and profile.sample_distinct_count <= limit
    scope_complete = sample_complete and snapshot.coverage.scope_exhaustion == "exhaustive"
    issues: list[str] = []
    if values is None:
        issues.append("value_evidence_unavailable")
    elif not dictionary_complete:
        issues.append("sample_frequency_dictionary_bounded")
    elif not sample_complete:
        issues.append("requested_limit_bounded")
    if snapshot.coverage.scope_exhaustion != "exhaustive":
        issues.append("scope_values_incomplete")
    if values is None:
        next_calls = ("md.inspect(...).sample(..., persist_values=True, refresh=True)",)
    else:
        next_calls = ("ms.help('dimension')",)
    return DimensionValuesResult(
        status="complete" if scope_complete else "incomplete",
        snapshot_id=snapshot.id,
        column=column,
        sample_distinct_count=profile.sample_distinct_count,
        returned_value_count=None if values is None else len(values),
        sample_values_complete=sample_complete,
        scope_values_complete=scope_complete,
        value_evidence_state=snapshot.value_evidence_state,
        frequency_capacity=profile.frequency_capacity,
        values=values,
        issues=tuple(issues),
        next_calls=next_calls,
    )


def _project_time_dimensions(
    snapshot: DiscoverySnapshot, *, columns: tuple[str, ...]
) -> TimeEvidenceResult:
    profiles = _profiles(snapshot, columns)
    evidence = {
        profile.name: TimeColumnEvidence(
            column=profile.name,
            profile=profile,
            deterministic_matches=profile.deterministic_matches,
            unresolved=_TIME_UNRESOLVED,
        )
        for profile in profiles
    }
    return TimeEvidenceResult(
        status="complete",
        columns=columns,
        evidence_by_column=MappingProxyType(evidence),
        issues=(),
        next_calls=("ms.help('time_dimension')",),
    )


def _project_measures(
    snapshot: DiscoverySnapshot, *, columns: tuple[str, ...]
) -> MeasureEvidenceResult:
    profiles = _profiles(snapshot, columns)
    status, issues, next_calls = _value_projection_state(snapshot, help_topic="measure")
    evidence = {
        profile.name: MeasureColumnEvidence(
            column=profile.name,
            profile=profile,
            unresolved=_MEASURE_UNRESOLVED,
        )
        for profile in profiles
    }
    return MeasureEvidenceResult(
        status=status,
        columns=columns,
        evidence_by_column=MappingProxyType(evidence),
        issues=issues,
        next_calls=next_calls,
    )


def _project_relationships(
    snapshot: DiscoverySnapshot,
    other: DiscoverySnapshot,
    *,
    left: tuple[str, ...],
    right: tuple[str, ...],
) -> RelationshipEvidenceResult:
    left_profiles = _profiles(snapshot, left)
    right_profiles = _profiles(other, right)
    if len(left) != 1 or len(right) != 1:
        return RelationshipEvidenceResult(
            status="incomplete",
            left_snapshot_id=snapshot.id,
            right_snapshot_id=other.id,
            left_scope=snapshot.scope,
            right_scope=other.scope,
            left=left,
            right=right,
            left_profile=None,
            right_profile=None,
            type_compatible=None,
            evidence_state="unavailable",
            retained_overlap_count=None,
            retained_left_orphan_count=None,
            retained_right_orphan_count=None,
            scope_comparability="unresolved",
            issues=("multi_column",),
            next_calls=("ms.help('relationship')",),
        )

    left_profile = left_profiles[0]
    right_profile = right_profiles[0]
    dictionaries_complete = _sample_values_complete(left_profile) and _sample_values_complete(
        right_profile
    )
    if dictionaries_complete:
        assert left_profile.top_values is not None
        assert right_profile.top_values is not None
        left_values = {value for value, _count in left_profile.top_values}
        right_values = {value for value, _count in right_profile.top_values}
        overlap_count = len(left_values & right_values)
        left_orphan_count = len(left_values - right_values)
        right_orphan_count = len(right_values - left_values)
        evidence_state: Literal["available", "unavailable"] = "available"
        issues: tuple[str, ...] = ()
    else:
        overlap_count = None
        left_orphan_count = None
        right_orphan_count = None
        evidence_state = "unavailable"
        issues = ("retained_values_unavailable",)
    return RelationshipEvidenceResult(
        status="complete" if evidence_state == "available" else "incomplete",
        left_snapshot_id=snapshot.id,
        right_snapshot_id=other.id,
        left_scope=snapshot.scope,
        right_scope=other.scope,
        left=left,
        right=right,
        left_profile=left_profile,
        right_profile=right_profile,
        type_compatible=left_profile.data_type == right_profile.data_type,
        evidence_state=evidence_state,
        retained_overlap_count=overlap_count,
        retained_left_orphan_count=left_orphan_count,
        retained_right_orphan_count=right_orphan_count,
        scope_comparability="unresolved",
        issues=issues,
        next_calls=("ms.help('relationship')",),
    )


from marivo.datasource.snapshot import (  # noqa: E402
    ColumnProfile,
    DeterministicMatch,
    DiscoverySnapshot,
)
