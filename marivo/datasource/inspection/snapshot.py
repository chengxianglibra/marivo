"""Immutable authoring snapshots acquired by one bounded user-data query."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast
from urllib.parse import urlparse

import pandas as pd

from marivo._kernel.refs import DatasourceKind, Ref
from marivo.datasource import credentials as cr
from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.errors import (
    DatasourceAuthoringError,
    DatasourceCredentialError,
    DatasourceCredentialScopeError,
    DatasourceObservedEffects,
    _backend_failure_summary,
)
from marivo.datasource.inspection.details import Partitioning
from marivo.datasource.inspection.errors import repair_for_authoring_code
from marivo.datasource.ir import (
    DatasourceIR,
    JsonSourceIR,
    QueryParamScalar,
    QueryParamScalarList,
)
from marivo.datasource.metadata_types import ColumnMetadata
from marivo.datasource.runtime.json_source import normalize_json_source_params
from marivo.datasource.runtime.preview import normalize_preview_cell
from marivo.datasource.runtime.session_access import safe_source_error, source_native_timeout
from marivo.datasource.runtime.session_factory import open_source_session
from marivo.datasource.source import (
    AuthoringScope,
    PartitionScope,
    TableSource,
    _apply_scope,
    scope_time_range,
)
from marivo.datasource.store import declarations as _store
from marivo.datasource.store.authoring import _storage_name
from marivo.datasource.store.authoring_store import AuthoringStore
from marivo.datasource.store.evidence import (
    ColumnProfile,
    DeterministicMatch,
    DiscoveryEvidence,
    JsonScalar,
    SnapshotCoverage,
)
from marivo.datasource.store.evidence_payload import (
    SNAPSHOT_TTL,
    datasource_spec_fingerprint,
    snapshot_identity,
)

if TYPE_CHECKING:
    from marivo.datasource.runtime.adapters import SourceSession


_FREQUENCY_CAPACITY = 10
_DISPLAY_SAMPLE_CAPACITY = 10


def _acquisition_error(
    *,
    code: str,
    reason: str,
    received: str,
    scope_state: Literal["known", "none", "unknown"],
    query_executed: bool = False,
) -> DatasourceAuthoringError:
    return DatasourceAuthoringError(
        code=code,
        stage="acquire",
        expected=(
            "a successful bounded datasource acquisition"
            if query_executed
            else "valid native configuration and source binding before user-data execution"
        ),
        received=received,
        reason=reason,
        effect_observed=DatasourceObservedEffects(
            query_executed=query_executed,
            scope_state=scope_state,
        ),
        repair=repair_for_authoring_code(code),
    )


def _json_scalar(value: object) -> JsonScalar:
    normalized = normalize_preview_cell(value)
    if normalized is None or isinstance(normalized, str | int | float | bool):
        return normalized
    return str(normalized)


def _character_patterns(values: tuple[JsonScalar, ...]) -> tuple[tuple[str, int], ...]:
    text_values = tuple(value for value in values if isinstance(value, str))
    checks: tuple[tuple[str, Callable[[str], bool]], ...] = (
        ("digits", lambda value: value.isdigit()),
        ("letters", lambda value: value.isalpha()),
        ("alphanumeric", lambda value: value.isalnum()),
        ("contains_whitespace", lambda value: bool(re.search(r"\s", value))),
    )
    return tuple((name, sum(check(value) for value in text_values)) for name, check in checks)


def _url_syntax(value: JsonScalar) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$"
)
_YYYYMMDD_RE = re.compile(r"^\d{8}$")
_HOUR_RE = re.compile(r"^\d{2}$")


def _valid_iso_date(value: JsonScalar) -> bool:
    if not isinstance(value, str) or _ISO_DATE_RE.fullmatch(value) is None:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _valid_iso_datetime(value: JsonScalar) -> bool:
    if not isinstance(value, str) or _ISO_DATETIME_RE.fullmatch(value) is None:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _valid_yyyymmdd(value: JsonScalar) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        return False
    if _YYYYMMDD_RE.fullmatch(text) is None:
        return False
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError:
        return False
    return True


def _valid_hour(value: JsonScalar) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return 0 <= value <= 23
    if not isinstance(value, str) or _HOUR_RE.fullmatch(value) is None:
        return False
    return 0 <= int(value) <= 23


def _deterministic_matches(
    values: tuple[JsonScalar, ...], data_type: str
) -> tuple[DeterministicMatch, ...]:
    checked = len(values)
    matches: list[DeterministicMatch] = []
    normalized_type = data_type.lower()
    native_rule: str | None = None
    if normalized_type == "date":
        native_rule = "type.native_date"
    elif normalized_type.startswith("timestamp"):
        native_rule = "type.native_timestamp"
    if native_rule is not None:
        matches.append(
            DeterministicMatch(
                rule=native_rule,
                checked=checked,
                matched=checked,
                failed=0,
                role="value",
            )
        )

    checks: tuple[
        tuple[str, Callable[[JsonScalar], bool], Literal["value", "component_only"]], ...
    ] = (
        ("date.iso", _valid_iso_date, "value"),
        ("datetime.iso", _valid_iso_datetime, "value"),
        ("date.yyyymmdd", _valid_yyyymmdd, "value"),
        ("time.hour_00_23", _valid_hour, "component_only"),
    )
    for rule, check, role in checks:
        matched = sum(check(value) for value in values)
        if matched == 0:
            continue
        matches.append(
            DeterministicMatch(
                rule=rule,
                checked=checked,
                matched=matched,
                failed=checked - matched,
                role=role,
            )
        )
    return tuple(matches)


def _profile_column(
    frame: pd.DataFrame,
    column: ColumnMetadata,
    *,
    partition_names: frozenset[str],
    scope_exhaustion: Literal["exhaustive", "truncated"],
) -> ColumnProfile:
    series = frame[column.name]
    non_null = series.dropna()
    values = tuple(_json_scalar(value) for value in non_null.tolist())
    sample_distinct_count = len(set(values))
    is_string = all(isinstance(value, str) for value in values)
    lengths = tuple(len(value) for value in values if isinstance(value, str))
    numeric_values = tuple(
        float(value)
        for value in values
        if isinstance(value, int | float) and not isinstance(value, bool)
    )
    counter = Counter(values)
    top_values = tuple(counter.most_common(_FREQUENCY_CAPACITY))
    min_value: JsonScalar | None = None
    max_value: JsonScalar | None = None
    if values:
        if all(isinstance(value, str) for value in values):
            string_order = tuple(value for value in values if isinstance(value, str))
            min_value = min(string_order)
            max_value = max(string_order)
        elif all(
            isinstance(value, int | float) and not isinstance(value, bool) for value in values
        ):
            numeric_order = tuple(
                value
                for value in values
                if isinstance(value, int | float) and not isinstance(value, bool)
            )
            min_value = min(numeric_order)
            max_value = max(numeric_order)
        elif all(isinstance(value, bool) for value in values):
            bool_order = tuple(value for value in values if isinstance(value, bool))
            min_value = min(bool_order)
            max_value = max(bool_order)
    lower_name = column.name.lower()
    name_suffix = "_id" if lower_name.endswith("_id") else None
    url_checked = len(values)
    url_matched = sum(_url_syntax(value) for value in values)
    return ColumnProfile(
        name=column.name,
        data_type=column.type,
        nullable=column.nullable,
        partition_role=column.name in partition_names,
        sample_row_count=len(series),
        sample_null_count=int(series.isna().sum()),
        sample_empty_count=sum(value == "" for value in values) if is_string else 0,
        sample_distinct_count=sample_distinct_count,
        scope_distinct_count=(sample_distinct_count if scope_exhaustion == "exhaustive" else None),
        scope_distinct_lower_bound=sample_distinct_count,
        min_value=min_value,
        max_value=max_value,
        negative_count=sum(value < 0 for value in numeric_values),
        zero_count=sum(value == 0 for value in numeric_values),
        min_length=min(lengths) if lengths else None,
        max_length=max(lengths) if lengths else None,
        avg_length=sum(lengths) / len(lengths) if lengths else None,
        character_patterns=_character_patterns(values),
        top_values=top_values,
        display_samples=values[:_DISPLAY_SAMPLE_CAPACITY],
        frequency_capacity=_FREQUENCY_CAPACITY,
        deterministic_matches=_deterministic_matches(values, column.type),
        name_suffix=name_suffix,
        url_syntax_checked=url_checked,
        url_syntax_matched=url_matched,
    )


def _schema_fingerprint(schema: tuple[ColumnMetadata, ...]) -> str:
    payload = tuple((column.name, column.type, column.nullable) for column in schema)
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def acquire_snapshot(
    *,
    datasource: Ref[DatasourceKind],
    source: TableSource,
    schema: tuple[ColumnMetadata, ...],
    partitioning: Partitioning,
    project_root: Path,
    scope: AuthoringScope,
    columns: tuple[str, ...],
    persist_values: bool,
    refresh: bool,
    source_params: Mapping[str, QueryParamScalar | QueryParamScalarList] | None = None,
) -> DiscoveryEvidence:
    """Acquire and locally profile one selected-column, limit-plus-one sample."""
    datasource_id = _storage_name(datasource)
    datasource_ir = _store.load_one(datasource_id, project_root=project_root)
    if datasource_ir is None:
        raise _acquisition_error(
            code="datasource_missing",
            reason=f"datasource {datasource_id!r} is not configured",
            received=datasource_id,
            scope_state=partitioning.state,
        )
    schema_fingerprint = _schema_fingerprint(schema)
    datasource_fingerprint = datasource_spec_fingerprint(datasource_ir)
    if isinstance(source, JsonSourceIR):
        normalized_source_params = normalize_json_source_params(source, source_params)
    elif source_params:
        raise _acquisition_error(
            code="source_params_unsupported",
            reason="source_params are only valid for parameterized JSON sources",
            received=source.kind,
            scope_state=partitioning.state,
        )
    else:
        normalized_source_params = {}
    source_param_items = tuple(normalized_source_params.items())
    snapshot_id = snapshot_identity(
        datasource_fingerprint=datasource_fingerprint,
        source=source,
        scope=scope,
        columns=columns,
        schema_fingerprint=schema_fingerprint,
        persist_values=persist_values,
        source_params=source_param_items,
    )
    store = AuthoringStore(project_root)
    lookup = store.lookup_snapshot(
        snapshot_id=snapshot_id,
        datasource=datasource,
        datasource_fingerprint=datasource_fingerprint,
        source=source,
        scope=scope,
        columns=columns,
        schema_fingerprint=schema_fingerprint,
        persist_values=persist_values,
        source_params=source_param_items,
        refresh=refresh,
    )
    if lookup.snapshot is not None:
        return lookup.snapshot
    frame, pushed_predicate = _read_sample(
        datasource_ir,
        source,
        scope,
        columns,
        normalized_source_params,
        snapshot_id,
        scope_state=partitioning.state,
    )
    observed_row_count = len(frame)
    retained = frame.iloc[: scope.max_rows].copy()
    scope_exhaustion: Literal["exhaustive", "truncated"] = (
        "truncated" if observed_row_count > scope.max_rows else "exhaustive"
    )
    selected_schema = tuple(column for name in columns for column in schema if column.name == name)
    partition_names = frozenset(field.name for field in partitioning.fields)
    profiles = tuple(
        _profile_column(
            retained,
            column,
            partition_names=partition_names,
            scope_exhaustion=scope_exhaustion,
        )
        for column in selected_schema
    )
    created_at = lookup.now
    selected_data_types = {column.name: column.type.lower() for column in selected_schema}

    snapshot = DiscoveryEvidence(
        id=snapshot_id,
        _datasource=datasource,
        source=source,
        scope=scope,
        columns=columns,
        schema_fingerprint=schema_fingerprint,
        profiles=profiles,
        coverage=SnapshotCoverage(
            observed_row_count=observed_row_count,
            retained_row_count=len(retained),
            scope_exhaustion=scope_exhaustion,
            scope_exactness=("scope_exact" if scope_exhaustion == "exhaustive" else "sample_only"),
            sampling_method="first_rows_limit",
            pushed_predicate=pushed_predicate,
        ),
        persist_values=persist_values,
        value_evidence_state=("available" if persist_values else "value_evidence_unavailable"),
        cache_status=lookup.status,
        created_at=created_at,
        expires_at=created_at + SNAPSHOT_TTL,
        _project_root=project_root,
        source_params=source_param_items,
        retained_values=_retained_snapshot_rows(retained, columns, selected_data_types)
        if persist_values
        else (),
    )
    store.write_snapshot(snapshot, datasource_fingerprint=datasource_fingerprint)
    return snapshot


def _read_sample(
    datasource_ir: DatasourceIR,
    source: TableSource,
    scope: AuthoringScope,
    columns: tuple[str, ...],
    normalized_source_params: Mapping[str, QueryParamScalar | QueryParamScalarList],
    snapshot_id: str,
    *,
    scope_state: Literal["known", "none", "unknown"],
) -> tuple[pd.DataFrame, tuple[tuple[str, ...], ...]]:
    profile = require_profile_for_backend_type(datasource_ir.backend_type)

    session: SourceSession | None = None
    timeout_entered = False
    try:
        try:
            session = open_source_session(
                profile,
                datasource_ir,
                terminal_timeout_seconds=scope.timeout_seconds,
            )
        except (DatasourceCredentialError, DatasourceCredentialScopeError):
            raise
        except Exception as exc:
            exc = cr.safe_backend_exception(exc, None)
            failure = _backend_failure_summary(exc)
            raise _acquisition_error(
                code="acquisition_connection_failed",
                reason=f"the datasource backend could not be opened: {failure.message}",
                received=failure.identity,
                scope_state=scope_state,
            ) from exc
        try:
            binding = session.bind(
                source,
                source_identity=snapshot_id,
                source_params=normalized_source_params,
            )
            expression = binding.relation
        except (DatasourceCredentialError, DatasourceCredentialScopeError):
            raise
        except Exception as exc:
            exc = safe_source_error(session, exc)
            failure = _backend_failure_summary(exc)
            raise _acquisition_error(
                code="acquisition_source_failed",
                reason=f"the inspected source could not be resolved: {failure.message}",
                received=failure.identity,
                scope_state=scope_state,
            ) from exc
        pushed_predicate: tuple[tuple[str, ...], ...]
        time_predicate = scope_time_range(scope)
        if time_predicate is not None:
            pushed_predicate = (
                (
                    "time_range",
                    time_predicate.column,
                    time_predicate.start.isoformat(),
                    time_predicate.end.isoformat(),
                ),
            )
        elif isinstance(scope, PartitionScope):
            pushed_predicate = tuple(("eq", column, value) for column, value in scope.values)
        else:
            pushed_predicate = ()
        expression = _apply_scope(expression, scope)
        expression = expression.select(*columns).limit(scope.max_rows + 1)
        try:
            with source_native_timeout(session, scope.timeout_seconds):
                timeout_entered = True
                frame = session.collect_bounded(
                    expression,
                    source_identities=(snapshot_id,),
                    purpose="datasource.snapshot",
                    max_rows=scope.max_rows + 1,
                )
        except (DatasourceCredentialError, DatasourceCredentialScopeError):
            raise
        except Exception as exc:
            exc = safe_source_error(session, exc)
            failure = _backend_failure_summary(exc)
            if not timeout_entered:
                raise _acquisition_error(
                    code="native_timeout_configuration_failed",
                    reason=f"the datasource adapter could not install native timeout settings: {failure.message}",
                    received=failure.identity,
                    scope_state=scope_state,
                ) from exc
            if not any(
                submission.query_executed
                and submission.purpose == "datasource.snapshot"
                and submission.source_identity == snapshot_id
                for submission in session.submissions
            ):
                raise _acquisition_error(
                    code="acquisition_preparation_failed",
                    reason=f"bounded datasource acquisition could not be prepared: {failure.message}",
                    received=failure.identity,
                    scope_state=scope_state,
                ) from exc
            raise _acquisition_error(
                code="acquisition_execution_failed",
                reason=(
                    "bounded datasource acquisition failed after query execution: "
                    f"{failure.message}"
                ),
                received=failure.identity,
                scope_state=scope_state,
                query_executed=True,
            ) from exc
    finally:
        if session is not None:
            session.close()
    return frame, pushed_predicate


def _persisted_value(value: object, dtype: str | None) -> JsonScalar:
    normalized = normalize_preview_cell(value)
    if dtype == "date":
        if isinstance(value, pd.Timestamp):
            if (
                value.hour
                == value.minute
                == value.second
                == value.microsecond
                == value.nanosecond
                == 0
            ):
                return value.date().isoformat()
        elif type(value) is date:
            return value.isoformat()
    return cast("JsonScalar", normalized)


def _retained_snapshot_rows(
    frame: pd.DataFrame, columns: tuple[str, ...], dtypes: Mapping[str, str]
) -> tuple[dict[str, JsonScalar], ...]:
    return tuple(
        {
            column: _persisted_value(value, dtypes.get(column))
            for column, value in zip(columns, row, strict=True)
        }
        for row in frame.itertuples(index=False, name=None)
    )
