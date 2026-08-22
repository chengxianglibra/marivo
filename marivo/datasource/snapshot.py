"""Immutable authoring snapshots acquired by one bounded user-data query."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeAlias, cast
from urllib.parse import urlparse

import ibis.expr.types as ir
import pandas as pd

from marivo.datasource import backends as _backends
from marivo.datasource import store as _store
from marivo.datasource._capabilities.contracts import repair_for_authoring_code
from marivo.datasource.authoring import _storage_name
from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.errors import (
    DatasourceAuthoringError,
    DatasourceObservedEffects,
    _backend_failure_summary,
)
from marivo.datasource.ir import (
    CsvSourceIR,
    JsonSourceIR,
    ParquetSourceIR,
    QueryParamScalar,
    QueryParamScalarList,
    TableSourceIR,
)
from marivo.datasource.json_source import normalize_json_source_params, read_json_source
from marivo.datasource.metadata import ColumnMetadata
from marivo.datasource.source import AuthoringScope, PartitionScope, TableSource
from marivo.datasource.table_source import table_source_expression
from marivo.preview import normalize_preview_cell
from marivo.refs import DatasourceKind, Ref
from marivo.render import Card, RenderableResult

if TYPE_CHECKING:
    from ibis.backends import BaseBackend

    from marivo.datasource.inspection import SourceInspection


_FREQUENCY_CAPACITY = 10
_DISPLAY_SAMPLE_CAPACITY = 10
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
class DiscoverySnapshotContract(RenderableResult):
    """Query-free read contract for one :class:`DiscoverySnapshot`."""

    snapshot_id: str
    columns: tuple[str, ...]
    retained_row_count: int
    value_evidence_state: Literal["available", "value_evidence_unavailable"]
    retained_values_shape: Literal["dict_rows"]
    available_reads: tuple[str, ...]

    def _repr_identity(self) -> str:
        return (
            f"DiscoverySnapshotContract snapshot={self.snapshot_id} rows={self.retained_row_count}"
        )

    def _card(self) -> Card:
        retained_values = (
            "available via .retained_values"
            if ".retained_values" in self.available_reads
            else "unavailable; sample(..., persist_values=True) is required"
        )
        return (
            Card(
                identity=self._repr_identity(),
                available=(
                    ".snapshot_id",
                    ".columns",
                    ".retained_row_count",
                    ".value_evidence_state",
                    ".retained_values_shape",
                    ".available_reads",
                    ".show()",
                ),
            )
            .status(
                f"value_evidence={self.value_evidence_state} "
                f"retained_rows={self.retained_row_count}"
            )
            .field("selected columns", repr(self.columns))
            .field("retained row shape", "tuple[dict[str, JsonScalar], ...]")
            .field("retained values", retained_values)
            .listing("query-free reads", self.available_reads)
            .field("typed consumers", "none; snapshot is generic evidence")
        )

    def __str__(self) -> str:
        return self.render()


@dataclass(frozen=True, repr=False)
class DiscoverySnapshot(RenderableResult):
    """Immutable bounded rows and profiles from one explicit source acquisition."""

    id: str
    datasource: Ref[DatasourceKind]
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

    def _repr_identity(self) -> str:
        return (
            f"DiscoverySnapshot id={self.id} datasource={self.datasource.path} "
            f"columns={len(self.columns)} rows={self.coverage.retained_row_count}"
        )

    def _card(self) -> Card:
        return (
            Card(
                identity=self._repr_identity(),
                available=(
                    ".profiles",
                    ".coverage",
                    ".source_params",
                    ".retained_values",
                    ".contract()",
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

    def contract(self) -> DiscoverySnapshotContract:
        """Return the query-free read contract for this generic evidence snapshot.

        Returns:
            A bounded contract describing selected columns, retained row shape,
            value-evidence availability, and direct field reads.

        Example:
            ``snapshot.contract().show()``

        Constraints:
            This method never refreshes the snapshot, connects to a datasource,
            or infers semantic meaning from the retained evidence.
        """
        available_reads = [".columns", ".profiles", ".coverage", ".source_params"]
        if self.value_evidence_state == "available":
            available_reads.append(".retained_values")
        return DiscoverySnapshotContract(
            snapshot_id=self.id,
            columns=self.columns,
            retained_row_count=self.coverage.retained_row_count,
            value_evidence_state=self.value_evidence_state,
            retained_values_shape="dict_rows",
            available_reads=tuple(available_reads),
        )


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
            else "an enforceable adapter timeout before user-data execution"
        ),
        received=received,
        reason=reason,
        effect_observed=DatasourceObservedEffects(
            query_executed=query_executed,
            scope_state=scope_state,
        ),
        repair=repair_for_authoring_code(code),
    )


def _source_expression(
    backend: object,
    source: TableSource,
    *,
    source_params: Mapping[str, QueryParamScalar | QueryParamScalarList] | None = None,
) -> ir.Table:
    if isinstance(source, TableSourceIR):
        return table_source_expression(backend, source)
    if isinstance(source, ParquetSourceIR):
        reader = getattr(backend, "read_parquet", None)
        if not callable(reader):
            raise RuntimeError("datasource backend does not expose read_parquet()")
        options: dict[str, object] = {}
        if source.hive_partitioning:
            options["hive_partitioning"] = True
        expression = cast("ir.Table", reader(source.path, **options))
        if source.columns is not None:
            expression = expression.select(*source.columns)
        return expression
    if isinstance(source, CsvSourceIR):
        reader = getattr(backend, "read_csv", None)
        if not callable(reader):
            raise RuntimeError("datasource backend does not expose read_csv()")
        csv_options: dict[str, object] = {"columns": dict(source.schema)}
        if not source.header:
            csv_options["header"] = False
        if source.delimiter != ",":
            csv_options["delimiter"] = source.delimiter
        return cast("ir.Table", reader(source.path, **csv_options))
    if isinstance(source, JsonSourceIR):
        return read_json_source(backend, source, source_params=source_params)
    raise TypeError(f"unsupported source type: {type(source).__name__}")


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
    inspection: SourceInspection,
    *,
    scope: AuthoringScope,
    columns: tuple[str, ...],
    persist_values: bool,
    refresh: bool,
    source_params: Mapping[str, QueryParamScalar | QueryParamScalarList] | None = None,
) -> DiscoverySnapshot:
    """Acquire and locally profile one selected-column, limit-plus-one sample."""
    from marivo.datasource.authoring_store import (
        SNAPSHOT_TTL,
        AuthoringStore,
        datasource_spec_fingerprint,
        snapshot_identity,
    )

    datasource_id = _storage_name(inspection.datasource)
    datasource_ir = _store.load_one(datasource_id, project_root=inspection._project_root)
    if datasource_ir is None:
        raise _acquisition_error(
            code="datasource_missing",
            reason=f"datasource {datasource_id!r} is not configured",
            received=datasource_id,
            scope_state=inspection.partitioning.state,
        )
    schema_fingerprint = _schema_fingerprint(inspection.schema)
    datasource_fingerprint = datasource_spec_fingerprint(datasource_ir)
    if isinstance(inspection.source, JsonSourceIR):
        normalized_source_params = normalize_json_source_params(inspection.source, source_params)
    elif source_params:
        raise _acquisition_error(
            code="source_params_unsupported",
            reason="source_params are only valid for parameterized JSON sources",
            received=inspection.source.kind,
            scope_state=inspection.partitioning.state,
        )
    else:
        normalized_source_params = {}
    source_param_items = tuple(normalized_source_params.items())
    snapshot_id = snapshot_identity(
        datasource_fingerprint=datasource_fingerprint,
        source=inspection.source,
        scope=scope,
        columns=columns,
        schema_fingerprint=schema_fingerprint,
        persist_values=persist_values,
        source_params=source_param_items,
    )
    store = AuthoringStore(inspection._project_root)
    lookup = store.lookup_snapshot(
        snapshot_id=snapshot_id,
        datasource=inspection.datasource,
        datasource_fingerprint=datasource_fingerprint,
        source=inspection.source,
        scope=scope,
        columns=columns,
        schema_fingerprint=schema_fingerprint,
        persist_values=persist_values,
        source_params=source_param_items,
        refresh=refresh,
    )
    if lookup.snapshot is not None:
        return lookup.snapshot
    profile = require_profile_for_backend_type(datasource_ir.backend_type)
    timeout = profile.authoring_timeout
    if timeout is None:
        raise _acquisition_error(
            code="timeout_not_enforceable",
            reason="the datasource adapter cannot enforce the requested acquisition timeout",
            received=f"timeout_seconds={scope.timeout_seconds}",
            scope_state=inspection.partitioning.state,
        )

    backend: BaseBackend | None = None
    timeout_entered = False
    execute_attempted = False
    try:
        try:
            backend = _backends.build_backend(datasource_ir, read_only=True)
        except Exception as exc:
            failure = _backend_failure_summary(exc)
            raise _acquisition_error(
                code="acquisition_connection_failed",
                reason=f"the datasource backend could not be opened: {failure.message}",
                received=failure.identity,
                scope_state=inspection.partitioning.state,
            ) from exc
        try:
            expression = _source_expression(
                backend,
                inspection.source,
                source_params=normalized_source_params,
            )
        except Exception as exc:
            failure = _backend_failure_summary(exc)
            raise _acquisition_error(
                code="acquisition_source_failed",
                reason=f"the inspected source could not be resolved: {failure.message}",
                received=failure.identity,
                scope_state=inspection.partitioning.state,
            ) from exc
        pushed_predicate: tuple[tuple[str, ...], ...]
        if isinstance(scope, PartitionScope) and scope._time_range is not None:
            time_predicate = scope._time_range
            expression = expression.filter(
                (expression[time_predicate.column] >= time_predicate.start)
                & (expression[time_predicate.column] < time_predicate.end)
            )
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
            for column, value in scope.values:
                expression = expression.filter(expression[column] == value)
        else:
            pushed_predicate = ()
        expression = expression.select(*columns).limit(scope.max_rows + 1)
        try:
            with timeout(backend, scope.timeout_seconds):
                timeout_entered = True
                execute_attempted = True
                frame = expression.execute()
        except Exception as exc:
            failure = _backend_failure_summary(exc)
            if not timeout_entered:
                raise _acquisition_error(
                    code="timeout_not_enforceable",
                    reason=f"the datasource adapter could not arm its timeout: {failure.message}",
                    received=failure.identity,
                    scope_state=inspection.partitioning.state,
                ) from exc
            if not execute_attempted:
                raise _acquisition_error(
                    code="timeout_not_enforceable",
                    reason=(
                        "the datasource adapter could not enter its execution guard: "
                        f"{failure.message}"
                    ),
                    received=failure.identity,
                    scope_state=inspection.partitioning.state,
                ) from exc
            raise _acquisition_error(
                code="acquisition_execution_failed",
                reason=(
                    "bounded datasource acquisition failed after query execution: "
                    f"{failure.message}"
                ),
                received=failure.identity,
                scope_state=inspection.partitioning.state,
                query_executed=True,
            ) from exc
    finally:
        if backend is not None:
            disconnect = getattr(backend, "disconnect", None)
            if callable(disconnect):
                with suppress(Exception):
                    disconnect()

    observed_row_count = len(frame)
    retained = frame.iloc[: scope.max_rows].copy()
    scope_exhaustion: Literal["exhaustive", "truncated"] = (
        "truncated" if observed_row_count > scope.max_rows else "exhaustive"
    )
    selected_schema = tuple(
        column for name in columns for column in inspection.schema if column.name == name
    )
    partition_names = frozenset(field.name for field in inspection.partitioning.fields)
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

    def persisted_value(value: object, column: str) -> JsonScalar:
        normalized = normalize_preview_cell(value)
        if selected_data_types.get(column) == "date":
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

    snapshot = DiscoverySnapshot(
        id=snapshot_id,
        datasource=inspection.datasource,
        source=inspection.source,
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
        _project_root=inspection._project_root,
        source_params=source_param_items,
        retained_values=tuple(
            {
                column: persisted_value(value, column)
                for column, value in zip(columns, row, strict=True)
            }
            for row in retained.itertuples(index=False, name=None)
        )
        if persist_values
        else (),
    )
    store.write_snapshot(snapshot, datasource_fingerprint=datasource_fingerprint)
    return snapshot
