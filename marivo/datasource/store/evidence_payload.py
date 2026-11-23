"""Project-local persistence for privacy-aware authoring evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import fields, is_dataclass
from datetime import date, datetime, timedelta
from math import isfinite
from typing import Literal, TypeAlias, cast

from marivo._kernel.compat import UTC
from marivo._kernel.refs import Ref, RefPayloadV1
from marivo.datasource.ir import DatasourceIR, QueryParamScalar, QueryParamScalarList
from marivo.datasource.source import (
    AuthoringScope,
    PartitionScope,
    TableSource,
    UnprunedScope,
    scope_time_range,
    time_range,
)
from marivo.datasource.store.evidence import (
    ColumnProfile,
    DeterministicMatch,
    JsonScalar,
    SnapshotCoverage,
)

EVIDENCE_FORMAT_VERSION = 4
SNAPSHOT_TTL = timedelta(hours=24)

JsonValue: TypeAlias = str | int | float | bool | list["JsonValue"] | dict[str, "JsonValue"] | None
CacheMissStatus: TypeAlias = Literal["fresh", "stale", "mismatched"]


_SNAPSHOT_PAYLOAD_FIELDS = frozenset(
    {
        "evidence_format_version",
        "id",
        "datasource",
        "datasource_fingerprint",
        "source",
        "source_params",
        "scope",
        "columns",
        "schema_fingerprint",
        "persist_values",
        "created_at",
        "expires_at",
        "profiles",
        "coverage",
        "retained_values",
        "payload_digest",
    }
)
_PROFILE_FIELDS = frozenset(field.name for field in fields(ColumnProfile))
_MATCH_FIELDS = frozenset(field.name for field in fields(DeterministicMatch))
_COVERAGE_FIELDS = frozenset(field.name for field in fields(SnapshotCoverage))


def _normalize_json(value: object) -> JsonValue:
    if type(value) is Ref:
        return cast("JsonValue", RefPayloadV1.from_ref(value).to_dict())
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


def _source_param_value(value: object) -> QueryParamScalar | QueryParamScalarList | None:
    """Normalize one persisted source-param value; return None when invalid."""
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        items: list[QueryParamScalar] = []
        for item in value:
            scalar = _source_param_value(item)
            if scalar is None:
                return None
            if isinstance(scalar, Sequence) and not isinstance(scalar, str | bytes | bytearray):
                return None
            items.append(cast("QueryParamScalar", scalar))
        return items
    if isinstance(value, bool):
        return value
    if isinstance(value, str | int):
        return value
    if isinstance(value, float):
        return value if isfinite(value) else None
    return None


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
    predicate = scope_time_range(scope)
    if predicate is not None:
        return {
            "kind": "time_range",
            "column": predicate.column,
            "start": predicate.start.isoformat(),
            "end": predicate.end.isoformat(),
            "bound_kind": "datetime" if isinstance(predicate.start, datetime) else "date",
            "max_rows": scope.max_rows,
            "timeout_seconds": scope.timeout_seconds,
        }
    payload: dict[str, object] = {
        "kind": "partition" if isinstance(scope, PartitionScope) else "unpruned",
        "max_rows": scope.max_rows,
        "timeout_seconds": scope.timeout_seconds,
    }
    if isinstance(scope, PartitionScope):
        payload["partition"] = scope.values
    return payload


def snapshot_identity(
    *,
    datasource_fingerprint: str,
    source: TableSource,
    scope: AuthoringScope,
    columns: tuple[str, ...],
    schema_fingerprint: str,
    persist_values: bool,
    source_params: tuple[tuple[str, QueryParamScalar | QueryParamScalarList], ...] = (),
) -> str:
    """Return the stable SHA-256 identity for one snapshot evidence policy."""
    payload = {
        "datasource_fingerprint": datasource_fingerprint,
        "source": source.to_dict(),
        "source_params": source_params,
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
    predicates: list[tuple[str, ...]] = []
    for item in _sequence(payload.get("pushed_predicate"), field="coverage.pushed_predicate"):
        entry = _sequence(item, field="coverage.pushed_predicate entry")
        if len(entry) == 3 and entry[0] == "eq":
            predicates.append(
                (
                    "eq",
                    _string(entry[1], field="coverage.pushed_predicate.column"),
                    _string(entry[2], field="coverage.pushed_predicate.value"),
                )
            )
        elif len(entry) == 4 and entry[0] == "time_range":
            predicates.append(
                (
                    "time_range",
                    _string(entry[1], field="coverage.pushed_predicate.column"),
                    _string(entry[2], field="coverage.pushed_predicate.start"),
                    _string(entry[3], field="coverage.pushed_predicate.end"),
                )
            )
        else:
            raise ValueError("coverage.pushed_predicate entry is invalid")
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


def _validate_profile_counts(
    profiles: tuple[ColumnProfile, ...], coverage: SnapshotCoverage
) -> None:
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


def _retained_values(
    value: object,
    *,
    columns: tuple[str, ...],
    retained_row_count: int,
    persist_values: bool,
) -> tuple[dict[str, JsonScalar], ...]:
    """Decode named row values retained by an explicit persisted snapshot."""
    rows = _sequence(value, field="retained_values")
    if not persist_values:
        if rows:
            raise ValueError("non-persisted snapshots cannot retain row values")
        return ()
    if len(rows) != retained_row_count:
        raise ValueError("retained row values do not match snapshot coverage")
    normalized: list[dict[str, JsonScalar]] = []
    for row in rows:
        values = _mapping(row, field="retained_values row")
        if set(values) != set(columns):
            raise ValueError("retained row columns do not match selected columns")
        normalized.append({column: cast("JsonScalar", values[column]) for column in columns})
        if any(
            not isinstance(item, (str, int, float, bool)) and item is not None
            for item in normalized[-1].values()
        ):
            raise ValueError("retained row values must be JSON scalars")
    return tuple(normalized)


def _validate_snapshot_consistency(
    *,
    scope: AuthoringScope,
    columns: tuple[str, ...],
    profiles: tuple[ColumnProfile, ...],
    coverage: SnapshotCoverage,
) -> None:
    if tuple(profile.name for profile in profiles) != columns:
        raise ValueError("profile names and order must match selected columns")
    expected_predicate: tuple[tuple[str, ...], ...]
    predicate = scope_time_range(scope)
    if predicate is not None:
        expected_predicate = (
            (
                "time_range",
                predicate.column,
                predicate.start.isoformat(),
                predicate.end.isoformat(),
            ),
        )
    elif isinstance(scope, PartitionScope):
        expected_predicate = tuple(("eq", column, value) for column, value in scope.values)
    else:
        expected_predicate = ()
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

    _validate_profile_counts(profiles, coverage)


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
    if kind == "time_range":
        expected_fields = {
            "kind",
            "column",
            "start",
            "end",
            "bound_kind",
            "max_rows",
            "timeout_seconds",
        }
        if set(payload) != expected_fields:
            raise ValueError("time-range scope fields are invalid")
        column = _string(payload.get("column"), field="scope.column")
        start = _string(payload.get("start"), field="scope.start")
        end = _string(payload.get("end"), field="scope.end")
        bound_kind = _string(payload.get("bound_kind"), field="scope.bound_kind")
        if bound_kind == "date":
            parsed_start: date | datetime = date.fromisoformat(start)
            parsed_end: date | datetime = date.fromisoformat(end)
        elif bound_kind == "datetime":
            parsed_start = datetime.fromisoformat(start)
            parsed_end = datetime.fromisoformat(end)
        else:
            raise ValueError("scope.bound_kind is invalid")
        return time_range(
            column,
            start=parsed_start,
            end=parsed_end,
            max_rows=max_rows,
            timeout_seconds=timeout_seconds,
        )
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


TIME_RULE_IDS = (
    "type.native_date",
    "type.native_timestamp",
    "date.iso",
    "datetime.iso",
    "date.yyyymmdd",
    "time.hour_00_23",
)
