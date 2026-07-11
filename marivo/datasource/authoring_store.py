"""Project-local persistence for privacy-aware authoring evidence."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from marivo.config import (
    AUTHORING_CHECK_DIR,
    AUTHORING_DIR,
    AUTHORING_SNAPSHOT_DIR,
    STATE_DIR,
)
from marivo.datasource.authoring import DatasourceRef
from marivo.datasource.evidence import TIME_RULE_IDS
from marivo.datasource.ir import DatasourceIR
from marivo.datasource.snapshot import (
    ColumnProfile,
    DeterministicMatch,
    DiscoverySnapshot,
    JsonScalar,
    SnapshotCoverage,
)
from marivo.datasource.source import AuthoringScope, PartitionScope, TableSource, UnprunedScope
from marivo.refs import SemanticRef

if TYPE_CHECKING:
    from marivo.semantic.preview_checks import PreviewCheck

EVIDENCE_FORMAT_VERSION = 1
CHECK_FORMAT_VERSION = 1
SNAPSHOT_TTL = timedelta(hours=24)

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
type CacheMissStatus = Literal["fresh", "stale", "mismatched"]


_SNAPSHOT_MEMORY: dict[str, DiscoverySnapshot] = {}

_SNAPSHOT_PAYLOAD_FIELDS = frozenset(
    {
        "evidence_format_version",
        "id",
        "datasource",
        "datasource_fingerprint",
        "source",
        "scope",
        "columns",
        "schema_fingerprint",
        "persist_values",
        "created_at",
        "expires_at",
        "profiles",
        "coverage",
        "payload_digest",
    }
)
_PROFILE_FIELDS = frozenset(field.name for field in fields(ColumnProfile))
_MATCH_FIELDS = frozenset(field.name for field in fields(DeterministicMatch))
_COVERAGE_FIELDS = frozenset(field.name for field in fields(SnapshotCoverage))


@dataclass(frozen=True)
class SnapshotCacheLookup:
    """Internal cache lookup result for the single-query acquisition path."""

    snapshot: DiscoverySnapshot | None
    status: CacheMissStatus
    now: datetime


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _normalize_json(value: object) -> JsonValue:
    if isinstance(value, SemanticRef):
        return value.id
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise TypeError("authoring evidence timestamps must be UTC-aware")
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _normalize_json(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, tuple | list):
        return [_normalize_json(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("authoring evidence mappings require string keys")
            normalized[key] = _normalize_json(item)
        return normalized
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"unsupported authoring evidence value: {type(value).__name__}")


def _encoded(value: object) -> bytes:
    return json.dumps(
        _normalize_json(value),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _payload_digest(payload: dict[str, object]) -> str:
    body = {key: value for key, value in payload.items() if key != "payload_digest"}
    return hashlib.sha256(_encoded(body)).hexdigest()


def _require_fields(payload: dict[str, object], expected: frozenset[str], *, field: str) -> None:
    if set(payload) != expected:
        raise ValueError(f"{field} fields are invalid")


def datasource_spec_fingerprint(datasource: DatasourceIR) -> str:
    """Hash a datasource declaration without resolving or serializing secrets."""
    payload = {
        "semantic_id": datasource.semantic_id,
        "backend_type": datasource.backend_type,
        "fields": datasource.fields,
        "env_refs": tuple(sorted(datasource.env_refs.items())),
    }
    return hashlib.sha256(_encoded(payload)).hexdigest()


def _scope_payload(scope: AuthoringScope) -> dict[str, object]:
    payload: dict[str, object] = {
        "kind": "partition" if isinstance(scope, PartitionScope) else "unpruned",
        "max_rows": scope.max_rows,
        "timeout_seconds": scope.timeout_seconds,
    }
    if isinstance(scope, PartitionScope):
        payload["partition"] = scope.values
    return payload


def preview_check_scope_payload(scope: AuthoringScope) -> dict[str, JsonValue]:
    """Return the row-free PreviewCheck representation for one validated scope."""
    payload = _normalize_json(scope)
    if not isinstance(payload, dict):
        raise TypeError("preview check scope must normalize to a mapping")
    return payload


def snapshot_identity(
    *,
    datasource_fingerprint: str,
    source: TableSource,
    scope: AuthoringScope,
    columns: tuple[str, ...],
    schema_fingerprint: str,
    persist_values: bool,
) -> str:
    """Return the stable SHA-256 identity for one snapshot evidence policy."""
    payload = {
        "datasource_fingerprint": datasource_fingerprint,
        "source": source.to_dict(),
        "scope": _scope_payload(scope),
        "columns": columns,
        "schema_fingerprint": schema_fingerprint,
        "persist_values": persist_values,
        "evidence_format_version": EVIDENCE_FORMAT_VERSION,
    }
    return hashlib.sha256(_encoded(payload)).hexdigest()


def _mapping(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be a JSON object")
    return {str(key): item for key, item in value.items()}


def _sequence(value: object, *, field: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a JSON array")
    return list(value)


def _string(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _optional_string(value: object, *, field: str) -> str | None:
    return None if value is None else _string(value, field=field)


def _integer(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    return value


def _optional_integer(value: object, *, field: str) -> int | None:
    return None if value is None else _integer(value, field=field)


def _boolean(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _optional_boolean(value: object, *, field: str) -> bool | None:
    return None if value is None else _boolean(value, field=field)


def _optional_float(value: object, *, field: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ValueError(f"{field} must be a number")
    return float(value)


def _json_scalar(value: object, *, field: str) -> JsonScalar:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise ValueError(f"{field} must be a JSON scalar")


def _timestamp(value: object, *, field: str) -> datetime:
    parsed = datetime.fromisoformat(_string(value, field=field))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be a UTC timestamp")
    return parsed.astimezone(UTC)


def _deterministic_match(value: object) -> DeterministicMatch:
    payload = _mapping(value, field="deterministic_match")
    _require_fields(payload, _MATCH_FIELDS, field="deterministic_match")
    rule = _string(payload.get("rule"), field="deterministic_match.rule")
    if rule not in TIME_RULE_IDS:
        raise ValueError("deterministic_match.rule is invalid")
    role = _string(payload.get("role"), field="deterministic_match.role")
    normalized_role: Literal["value", "component_only"]
    if role == "value":
        normalized_role = "value"
    elif role == "component_only":
        normalized_role = "component_only"
    else:
        raise ValueError("deterministic_match.role is invalid")
    checked = _integer(payload.get("checked"), field="deterministic_match.checked")
    matched = _integer(payload.get("matched"), field="deterministic_match.matched")
    failed = _integer(payload.get("failed"), field="deterministic_match.failed")
    if min(checked, matched, failed) < 0 or matched + failed != checked:
        raise ValueError("deterministic_match counts are invalid")
    expected_role = "component_only" if rule == "time.hour_00_23" else "value"
    if normalized_role != expected_role:
        raise ValueError("deterministic_match.role does not match its rule")
    return DeterministicMatch(
        rule=rule,
        checked=checked,
        matched=matched,
        failed=failed,
        role=normalized_role,
    )


def _string_int_pairs(value: object, *, field: str) -> tuple[tuple[str, int], ...]:
    pairs: list[tuple[str, int]] = []
    for item in _sequence(value, field=field):
        entry = _sequence(item, field=f"{field} entry")
        if len(entry) != 2:
            raise ValueError(f"{field} entries must contain two values")
        pairs.append(
            (
                _string(entry[0], field=f"{field}.name"),
                _integer(entry[1], field=f"{field}.count"),
            )
        )
    return tuple(pairs)


def _top_values(value: object) -> tuple[tuple[JsonScalar, int], ...] | None:
    if value is None:
        return None
    pairs: list[tuple[JsonScalar, int]] = []
    for item in _sequence(value, field="profile.top_values"):
        entry = _sequence(item, field="profile.top_values entry")
        if len(entry) != 2:
            raise ValueError("profile.top_values entries must contain two values")
        pairs.append(
            (
                _json_scalar(entry[0], field="profile.top_values.value"),
                _integer(entry[1], field="profile.top_values.count"),
            )
        )
    return tuple(pairs)


def _display_samples(value: object) -> tuple[JsonScalar, ...] | None:
    if value is None:
        return None
    return tuple(
        _json_scalar(item, field="profile.display_samples")
        for item in _sequence(value, field="profile.display_samples")
    )


def _column_profile(value: object) -> ColumnProfile:
    payload = _mapping(value, field="profile")
    _require_fields(payload, _PROFILE_FIELDS, field="profile")
    profile = ColumnProfile(
        name=_string(payload.get("name"), field="profile.name"),
        data_type=_string(payload.get("data_type"), field="profile.data_type"),
        nullable=_optional_boolean(payload.get("nullable"), field="profile.nullable"),
        partition_role=_boolean(payload.get("partition_role"), field="profile.partition_role"),
        sample_row_count=_integer(
            payload.get("sample_row_count"), field="profile.sample_row_count"
        ),
        sample_null_count=_integer(
            payload.get("sample_null_count"), field="profile.sample_null_count"
        ),
        sample_empty_count=_integer(
            payload.get("sample_empty_count"), field="profile.sample_empty_count"
        ),
        sample_distinct_count=_integer(
            payload.get("sample_distinct_count"), field="profile.sample_distinct_count"
        ),
        scope_distinct_count=_optional_integer(
            payload.get("scope_distinct_count"), field="profile.scope_distinct_count"
        ),
        scope_distinct_lower_bound=_integer(
            payload.get("scope_distinct_lower_bound"),
            field="profile.scope_distinct_lower_bound",
        ),
        min_value=_json_scalar(payload.get("min_value"), field="profile.min_value"),
        max_value=_json_scalar(payload.get("max_value"), field="profile.max_value"),
        negative_count=_integer(payload.get("negative_count"), field="profile.negative_count"),
        zero_count=_integer(payload.get("zero_count"), field="profile.zero_count"),
        min_length=_optional_integer(payload.get("min_length"), field="profile.min_length"),
        max_length=_optional_integer(payload.get("max_length"), field="profile.max_length"),
        avg_length=_optional_float(payload.get("avg_length"), field="profile.avg_length"),
        character_patterns=_string_int_pairs(
            payload.get("character_patterns"), field="profile.character_patterns"
        ),
        top_values=_top_values(payload.get("top_values")),
        display_samples=_display_samples(payload.get("display_samples")),
        frequency_capacity=_integer(
            payload.get("frequency_capacity"), field="profile.frequency_capacity"
        ),
        deterministic_matches=tuple(
            _deterministic_match(item)
            for item in _sequence(
                payload.get("deterministic_matches"), field="profile.deterministic_matches"
            )
        ),
        name_suffix=_optional_string(payload.get("name_suffix"), field="profile.name_suffix"),
        url_syntax_checked=_integer(
            payload.get("url_syntax_checked"), field="profile.url_syntax_checked"
        ),
        url_syntax_matched=_integer(
            payload.get("url_syntax_matched"), field="profile.url_syntax_matched"
        ),
    )
    counts = (
        profile.sample_row_count,
        profile.sample_null_count,
        profile.sample_empty_count,
        profile.sample_distinct_count,
        profile.scope_distinct_lower_bound,
        profile.negative_count,
        profile.zero_count,
        profile.frequency_capacity,
        profile.url_syntax_checked,
        profile.url_syntax_matched,
    )
    optional_counts = (
        profile.scope_distinct_count,
        profile.min_length,
        profile.max_length,
    )
    if any(count < 0 for count in counts) or any(
        count is not None and count < 0 for count in optional_counts
    ):
        raise ValueError("profile counts must be nonnegative")
    rules = tuple(match.rule for match in profile.deterministic_matches)
    if len(rules) != len(set(rules)):
        raise ValueError("profile deterministic rules must be unique")
    return profile


def _coverage(value: object) -> SnapshotCoverage:
    payload = _mapping(value, field="coverage")
    _require_fields(payload, _COVERAGE_FIELDS, field="coverage")
    exhaustion = _string(payload.get("scope_exhaustion"), field="coverage.scope_exhaustion")
    exactness = _string(payload.get("scope_exactness"), field="coverage.scope_exactness")
    method = _string(payload.get("sampling_method"), field="coverage.sampling_method")
    normalized_exhaustion: Literal["exhaustive", "truncated"]
    if exhaustion == "exhaustive":
        normalized_exhaustion = "exhaustive"
    elif exhaustion == "truncated":
        normalized_exhaustion = "truncated"
    else:
        raise ValueError("coverage.scope_exhaustion is invalid")
    normalized_exactness: Literal["scope_exact", "sample_only"]
    if exactness == "scope_exact":
        normalized_exactness = "scope_exact"
    elif exactness == "sample_only":
        normalized_exactness = "sample_only"
    else:
        raise ValueError("coverage.scope_exactness is invalid")
    normalized_method: Literal["first_rows_limit"]
    if method == "first_rows_limit":
        normalized_method = "first_rows_limit"
    else:
        raise ValueError("coverage.sampling_method is invalid")
    predicates: list[tuple[str, str]] = []
    for item in _sequence(payload.get("pushed_predicate"), field="coverage.pushed_predicate"):
        entry = _sequence(item, field="coverage.pushed_predicate entry")
        if len(entry) != 2:
            raise ValueError("coverage.pushed_predicate entries must contain two values")
        predicates.append(
            (
                _string(entry[0], field="coverage.pushed_predicate.column"),
                _string(entry[1], field="coverage.pushed_predicate.value"),
            )
        )
    coverage = SnapshotCoverage(
        observed_row_count=_integer(
            payload.get("observed_row_count"), field="coverage.observed_row_count"
        ),
        retained_row_count=_integer(
            payload.get("retained_row_count"), field="coverage.retained_row_count"
        ),
        scope_exhaustion=normalized_exhaustion,
        scope_exactness=normalized_exactness,
        sampling_method=normalized_method,
        pushed_predicate=tuple(predicates),
    )
    if coverage.observed_row_count < 0 or coverage.retained_row_count < 0:
        raise ValueError("coverage row counts must be nonnegative")
    return coverage


def _validate_snapshot_consistency(
    *,
    scope: AuthoringScope,
    columns: tuple[str, ...],
    profiles: tuple[ColumnProfile, ...],
    coverage: SnapshotCoverage,
) -> None:
    if tuple(profile.name for profile in profiles) != columns:
        raise ValueError("profile names and order must match selected columns")
    expected_predicate = scope.values if isinstance(scope, PartitionScope) else ()
    if coverage.pushed_predicate != expected_predicate:
        raise ValueError("coverage predicate does not match scope")
    expected_retained = min(coverage.observed_row_count, scope.max_rows)
    if coverage.retained_row_count != expected_retained:
        raise ValueError("coverage retained rows do not match observed rows and scope")
    if coverage.observed_row_count > scope.max_rows + 1:
        raise ValueError("coverage observed rows exceed the bounded acquisition")
    truncated = coverage.observed_row_count == scope.max_rows + 1
    if truncated != (coverage.scope_exhaustion == "truncated"):
        raise ValueError("coverage exhaustion does not match observed rows")
    expected_exactness = "sample_only" if truncated else "scope_exact"
    if coverage.scope_exactness != expected_exactness:
        raise ValueError("coverage exactness does not match exhaustion")

    retained = coverage.retained_row_count
    for profile in profiles:
        non_null = retained - profile.sample_null_count
        if profile.sample_row_count != retained or non_null < 0:
            raise ValueError("profile sample rows do not match retained coverage")
        bounded_counts = (
            profile.sample_empty_count,
            profile.sample_distinct_count,
            profile.scope_distinct_lower_bound,
            profile.negative_count,
            profile.zero_count,
            profile.url_syntax_checked,
            profile.url_syntax_matched,
        )
        if any(count > non_null for count in bounded_counts):
            raise ValueError("profile counts exceed retained non-null coverage")
        if profile.url_syntax_matched > profile.url_syntax_checked:
            raise ValueError("profile URL matches exceed checked values")
        if profile.scope_distinct_lower_bound != profile.sample_distinct_count:
            raise ValueError("profile distinct lower bound is inconsistent")
        expected_scope_distinct = (
            profile.sample_distinct_count if coverage.scope_exhaustion == "exhaustive" else None
        )
        if profile.scope_distinct_count != expected_scope_distinct:
            raise ValueError("profile scope distinct count is inconsistent")
        if any(match.checked > non_null for match in profile.deterministic_matches):
            raise ValueError("deterministic match counts exceed retained coverage")
        if profile.top_values is not None and (
            len(profile.top_values) > profile.frequency_capacity
            or any(count < 1 or count > non_null for _value, count in profile.top_values)
        ):
            raise ValueError("profile top values exceed retained coverage")
        if profile.display_samples is not None and len(profile.display_samples) > non_null:
            raise ValueError("profile display samples exceed retained coverage")


def authoring_scope_from_payload(value: object) -> AuthoringScope:
    """Decode and strictly validate one persisted authoring scope."""
    payload = _mapping(value, field="scope")
    kind = _string(payload.get("kind"), field="scope.kind")
    max_rows = _integer(payload.get("max_rows"), field="scope.max_rows")
    timeout_seconds = _integer(payload.get("timeout_seconds"), field="scope.timeout_seconds")
    if max_rows < 1 or timeout_seconds < 1:
        raise ValueError("scope guards must be positive")
    if kind == "unpruned":
        if set(payload) != {"kind", "max_rows", "timeout_seconds"}:
            raise ValueError("unpruned scope fields are invalid")
        return UnprunedScope(max_rows=max_rows, timeout_seconds=timeout_seconds)
    if kind != "partition":
        raise ValueError("scope.kind is invalid")
    if set(payload) != {"kind", "partition", "max_rows", "timeout_seconds"}:
        raise ValueError("partition scope fields are invalid")
    values: list[tuple[str, str]] = []
    for item in _sequence(payload.get("partition"), field="scope.partition"):
        entry = _sequence(item, field="scope.partition entry")
        if len(entry) != 2:
            raise ValueError("scope.partition entries must contain two values")
        key = _string(entry[0], field="scope.partition key")
        item_value = _string(entry[1], field="scope.partition value")
        if not key or not item_value:
            raise ValueError("scope.partition entries must be non-empty")
        values.append((key, item_value))
    if not values:
        raise ValueError("scope.partition must be non-empty")
    return PartitionScope(
        values=tuple(values),
        max_rows=max_rows,
        timeout_seconds=timeout_seconds,
    )


class AuthoringStore:
    """Persist and reload authoring evidence below one project-local state root."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.snapshot_dir = self.project_root / AUTHORING_SNAPSHOT_DIR
        self.check_dir = self.project_root / AUTHORING_CHECK_DIR

    def _memory_key(self, snapshot_id: str) -> str:
        return f"{self.project_root}:{snapshot_id}"

    def _snapshot_path(self, snapshot_id: str) -> Path:
        return self.snapshot_dir / f"{snapshot_id}.json"

    def _check_path(self, check_id: str) -> Path:
        return self.check_dir / f"{check_id}.json"

    def _ensure_directories(self) -> None:
        for path in (
            self.project_root / STATE_DIR,
            self.project_root / AUTHORING_DIR,
            self.snapshot_dir,
            self.check_dir,
        ):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(path, 0o700)

    def _read_payload(self, path: Path) -> dict[str, object] | None:
        try:
            decoded: object = json.loads(path.read_text(encoding="utf-8"))
            return _mapping(decoded, field="snapshot")
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def _metadata(
        self,
        *,
        snapshot_id: str,
        datasource: DatasourceRef,
        datasource_fingerprint: str,
        source: TableSource,
        scope: AuthoringScope,
        columns: tuple[str, ...],
        schema_fingerprint: str,
        persist_values: bool,
    ) -> dict[str, object]:
        return {
            "evidence_format_version": EVIDENCE_FORMAT_VERSION,
            "id": snapshot_id,
            "datasource": datasource.id,
            "datasource_fingerprint": datasource_fingerprint,
            "source": source.to_dict(),
            "scope": _scope_payload(scope),
            "columns": columns,
            "schema_fingerprint": schema_fingerprint,
            "persist_values": persist_values,
        }

    def _has_related_snapshot(self, datasource: DatasourceRef) -> bool:
        if not self.snapshot_dir.is_dir():
            return False
        for path in self.snapshot_dir.glob("*.json"):
            payload = self._read_payload(path)
            if payload is not None and payload.get("datasource") == datasource.id:
                return True
        return False

    def _read_valid_snapshot(
        self,
        path: Path,
        *,
        datasource: DatasourceRef,
        datasource_fingerprint: str,
        source: TableSource,
        now: datetime,
        allow_expired: bool = False,
    ) -> DiscoverySnapshot | None:
        payload = self._read_payload(path)
        if payload is None:
            return None
        try:
            _require_fields(payload, _SNAPSHOT_PAYLOAD_FIELDS, field="snapshot")
            payload_digest = _string(payload.get("payload_digest"), field="payload_digest")
            if payload_digest != _payload_digest(payload):
                return None
            if (
                _integer(payload.get("evidence_format_version"), field="evidence_format_version")
                != EVIDENCE_FORMAT_VERSION
            ):
                return None
            snapshot_id = _string(payload.get("id"), field="id")
            if path != self._snapshot_path(snapshot_id):
                return None
            if _string(payload.get("datasource"), field="datasource") != datasource.id:
                return None
            if (
                _string(payload.get("datasource_fingerprint"), field="datasource_fingerprint")
                != datasource_fingerprint
            ):
                return None
            if _mapping(payload.get("source"), field="source") != source.to_dict():
                return None
            scope = authoring_scope_from_payload(payload.get("scope"))
            columns = tuple(
                _string(item, field="columns item")
                for item in _sequence(payload.get("columns"), field="columns")
            )
            if not columns or any(not column for column in columns):
                return None
            schema_fingerprint = _string(
                payload.get("schema_fingerprint"), field="schema_fingerprint"
            )
            persist_values = _boolean(payload.get("persist_values"), field="persist_values")
            expected_id = snapshot_identity(
                datasource_fingerprint=datasource_fingerprint,
                source=source,
                scope=scope,
                columns=columns,
                schema_fingerprint=schema_fingerprint,
                persist_values=persist_values,
            )
            if snapshot_id != expected_id:
                return None
            created_at = _timestamp(payload.get("created_at"), field="created_at")
            expires_at = _timestamp(payload.get("expires_at"), field="expires_at")
            if (
                created_at > now
                or expires_at != created_at + SNAPSHOT_TTL
                or (expires_at <= now and not allow_expired)
            ):
                return None
            profiles = tuple(
                _column_profile(item)
                for item in _sequence(payload.get("profiles"), field="profiles")
            )
            coverage = _coverage(payload.get("coverage"))
            _validate_snapshot_consistency(
                scope=scope,
                columns=columns,
                profiles=profiles,
                coverage=coverage,
            )
            if not persist_values:
                profiles = tuple(
                    replace(
                        profile,
                        min_value=None,
                        max_value=None,
                        top_values=None,
                        display_samples=None,
                    )
                    for profile in profiles
                )
            return DiscoverySnapshot(
                id=snapshot_id,
                datasource=datasource,
                source=source,
                scope=scope,
                columns=columns,
                schema_fingerprint=schema_fingerprint,
                profiles=profiles,
                coverage=coverage,
                persist_values=persist_values,
                value_evidence_state=(
                    "available" if persist_values else "value_evidence_unavailable"
                ),
                cache_status="cached",
                created_at=created_at,
                expires_at=expires_at,
                _project_root=self.project_root,
            )
        except (TypeError, ValueError):
            return None

    def valid_snapshots(
        self,
        *,
        datasource: DatasourceRef,
        datasource_fingerprint: str,
        source: TableSource,
        now: datetime | None = None,
    ) -> tuple[DiscoverySnapshot, ...]:
        """Read strictly validated persisted snapshot metadata without using memory."""
        if not self.snapshot_dir.is_dir():
            return ()
        checked_at = now or _utc_now()
        snapshots = (
            snapshot
            for path in self.snapshot_dir.glob("*.json")
            if (
                snapshot := self._read_valid_snapshot(
                    path,
                    datasource=datasource,
                    datasource_fingerprint=datasource_fingerprint,
                    source=source,
                    now=checked_at,
                )
            )
            is not None
        )
        return tuple(sorted(snapshots, key=lambda snapshot: snapshot.created_at, reverse=True))

    def lookup_snapshot(
        self,
        *,
        snapshot_id: str,
        datasource: DatasourceRef,
        datasource_fingerprint: str,
        source: TableSource,
        scope: AuthoringScope,
        columns: tuple[str, ...],
        schema_fingerprint: str,
        persist_values: bool,
        refresh: bool,
    ) -> SnapshotCacheLookup:
        """Return a valid cache hit or why one acquisition is required."""
        now = _utc_now()
        if refresh:
            return SnapshotCacheLookup(snapshot=None, status="fresh", now=now)
        path = self._snapshot_path(snapshot_id)
        persisted = self._read_valid_snapshot(
            path,
            datasource=datasource,
            datasource_fingerprint=datasource_fingerprint,
            source=source,
            now=now,
            allow_expired=True,
        )
        if persisted is None:
            status: CacheMissStatus = (
                "mismatched"
                if path.is_file() or self._has_related_snapshot(datasource)
                else "fresh"
            )
            return SnapshotCacheLookup(snapshot=None, status=status, now=now)
        if (
            persisted.scope != scope
            or persisted.columns != columns
            or persisted.schema_fingerprint != schema_fingerprint
            or persisted.persist_values != persist_values
        ):
            return SnapshotCacheLookup(snapshot=None, status="mismatched", now=now)
        if persisted.expires_at <= now:
            return SnapshotCacheLookup(snapshot=None, status="stale", now=now)
        memory_key = self._memory_key(snapshot_id)
        remembered = _SNAPSHOT_MEMORY.get(memory_key)
        if remembered is not None:
            if remembered.expires_at > now:
                cached = replace(remembered, cache_status="cached")
                _SNAPSHOT_MEMORY[memory_key] = cached
                return SnapshotCacheLookup(snapshot=cached, status="fresh", now=now)
            del _SNAPSHOT_MEMORY[memory_key]

        _SNAPSHOT_MEMORY[memory_key] = persisted
        return SnapshotCacheLookup(snapshot=persisted, status="fresh", now=now)

    def _write_json(self, path: Path, payload: object) -> None:
        self._ensure_directories()
        encoded = _encoded(payload)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        descriptor_open = True
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor_open = False
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        except BaseException:
            if descriptor_open:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
            raise

    def write_snapshot(
        self,
        snapshot: DiscoverySnapshot,
        *,
        datasource_fingerprint: str,
    ) -> None:
        """Atomically persist one snapshot while retaining live values in memory."""
        persisted_profiles = snapshot.profiles
        if not snapshot.persist_values:
            persisted_profiles = tuple(
                replace(
                    profile,
                    min_value=None,
                    max_value=None,
                    top_values=None,
                    display_samples=None,
                )
                for profile in snapshot.profiles
            )
        payload = {
            **self._metadata(
                snapshot_id=snapshot.id,
                datasource=snapshot.datasource,
                datasource_fingerprint=datasource_fingerprint,
                source=snapshot.source,
                scope=snapshot.scope,
                columns=snapshot.columns,
                schema_fingerprint=snapshot.schema_fingerprint,
                persist_values=snapshot.persist_values,
            ),
            "created_at": snapshot.created_at,
            "expires_at": snapshot.expires_at,
            "profiles": persisted_profiles,
            "coverage": snapshot.coverage,
        }
        payload["payload_digest"] = _payload_digest(payload)
        self._write_json(self._snapshot_path(snapshot.id), payload)
        _SNAPSHOT_MEMORY[self._memory_key(snapshot.id)] = snapshot

    def write_preview_check(self, check: PreviewCheck) -> None:
        """Atomically persist row-free semantic preview evidence."""
        self._write_json(self._check_path(check.id), check)
