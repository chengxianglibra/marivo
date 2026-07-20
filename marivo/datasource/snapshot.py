"""Immutable authoring snapshots acquired by one bounded user-data query."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast
from urllib.parse import urlparse

import ibis.expr.types as ir
import pandas as pd

from marivo._authoring.model import AuthoringContract
from marivo.datasource import backends as _backends
from marivo.datasource import store as _store
from marivo.datasource._capabilities.contracts import (
    contract_for_snapshot,
    repair_for_authoring_code,
)
from marivo.datasource.authoring import _storage_name
from marivo.datasource.engines import require_profile_for_backend_type
from marivo.datasource.errors import DatasourceAuthoringError, DatasourceObservedEffects
from marivo.datasource.ir import CsvSourceIR, JsonSourceIR, ParquetSourceIR, TableSourceIR
from marivo.datasource.metadata import ColumnMetadata
from marivo.datasource.source import AuthoringScope, PartitionScope, TableSource
from marivo.preview import normalize_preview_cell
from marivo.refs import DatasourceKind, Ref
from marivo.render import Card, RenderableResult

if TYPE_CHECKING:
    from marivo.datasource.inspection import SourceInspection


_FREQUENCY_CAPACITY = 10
_DISPLAY_SAMPLE_CAPACITY = 10
type JsonScalar = str | int | float | bool | None


@dataclass(frozen=True)
class SnapshotCoverage:
    observed_row_count: int
    retained_row_count: int
    scope_exhaustion: Literal["exhaustive", "truncated"]
    scope_exactness: Literal["scope_exact", "sample_only"]
    sampling_method: Literal["first_rows_limit"]
    pushed_predicate: tuple[tuple[str, str], ...]


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
class DiscoverySnapshot(RenderableResult):
    """Immutable bounded source evidence with query-free semantic projections.

    Use ``entity()``, ``dimensions()``, ``time_dimensions()``, ``measures()``,
    ``values()``, and ``relationships()`` to project evidence already captured
    by ``SourceInspection.sample(...)``. Projection methods never query the
    datasource.
    """

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
                    ".entity(columns=(...))",
                    ".dimensions(columns=(...))",
                    ".values(column, limit=...)",
                    ".time_dimensions(columns=(...))",
                    ".measures(columns=(...))",
                    ".relationships(other, left=(...), right=(...))",
                    ".contract()",
                    ".render()",
                    ".show()",
                ),
            )
            .status(
                f"cache={self.cache_status} exhaustion={self.coverage.scope_exhaustion} "
                f"sampling={self.coverage.sampling_method}"
            )
            .table(
                columns=("column", "type", "nulls", "distinct"),
                rows=(
                    (
                        profile.name,
                        profile.data_type,
                        str(profile.sample_null_count),
                        str(profile.sample_distinct_count),
                    )
                    for profile in self.profiles
                ),
                row_count=len(self.profiles),
                label="profiles",
            )
        )

    def contract(self) -> AuthoringContract:
        """Return the explicit-scope and acquired-evidence states this snapshot proves."""
        return contract_for_snapshot(
            datasource_id=self.datasource.path,
            source=self.source,
            snapshot_id=self.id,
        )

    def entity(self, *, columns: tuple[str, ...]) -> EntityEvidenceResult:
        """Project column-local identity observations.

        Args:
            columns: Non-empty tuple of columns retained by this snapshot.

        Returns:
            Immutable sample uniqueness and lexical evidence for every requested column.

        Example:
            ``snapshot.entity(columns=("order_id",))``

        Constraints:
            This method is query-free and does not recommend or rank keys.
        """
        return _project_entity(self, columns=columns)

    def dimensions(self, *, columns: tuple[str, ...]) -> DimensionEvidenceResult:
        """Project column-local dimension observations.

        Args:
            columns: Non-empty tuple of columns retained by this snapshot.

        Returns:
            Immutable physical, frequency, and completeness evidence by column.

        Example:
            ``snapshot.dimensions(columns=("region",))``

        Constraints:
            This method is query-free and does not infer business semantics.
        """
        return _project_dimensions(self, columns=columns)

    def values(self, column: str, *, limit: int) -> DimensionValuesResult:
        """Return bounded retained frequency evidence for one dimension column.

        Args:
            column: One column retained by this snapshot.
            limit: Positive maximum number of retained value-count pairs to return.

        Returns:
            Sample- and scope-qualified value completeness evidence.

        Example:
            ``snapshot.values("region", limit=10)``

        Constraints:
            This method is query-free; unavailable retained values remain unavailable.
        """
        return _project_values(self, column, limit=limit)

    def time_dimensions(self, *, columns: tuple[str, ...]) -> TimeEvidenceResult:
        """Project fixed deterministic time rules.

        Args:
            columns: Non-empty tuple of columns retained by this snapshot.

        Returns:
            Immutable exact checked, matched, and failed counts by column and rule.

        Example:
            ``snapshot.time_dimensions(columns=("event_date", "event_hour"))``

        Constraints:
            This method is query-free; hour evidence is component-only.
        """
        return _project_time_dimensions(self, columns=columns)

    def measures(self, *, columns: tuple[str, ...]) -> MeasureEvidenceResult:
        """Project column-local measure observations.

        Args:
            columns: Non-empty tuple of columns retained by this snapshot.

        Returns:
            Immutable numeric profile evidence for every requested column.

        Example:
            ``snapshot.measures(columns=("amount",))``

        Constraints:
            This method is query-free and does not choose aggregation, unit, or additivity.
        """
        return _project_measures(self, columns=columns)

    def relationships(
        self,
        other: DiscoverySnapshot,
        *,
        left: tuple[str, ...],
        right: tuple[str, ...],
    ) -> RelationshipEvidenceResult:
        """Compare one explicit retained column pair.

        Args:
            other: Snapshot providing the right-hand evidence.
            left: Exactly one column retained by this snapshot.
            right: Exactly one column retained by ``other``.

        Returns:
            Type and retained-value overlap evidence with both physical scopes.

        Example:
            ``orders.relationships(customers, left=("customer_id",), right=("id",))``

        Constraints:
            This method is query-free; multi-column overlap is unavailable.
        """
        return _project_relationships(self, other, left=left, right=right)


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


def _source_expression(backend: object, source: TableSource) -> ir.Table:
    if isinstance(source, TableSourceIR):
        table = getattr(backend, "table", None)
        if not callable(table):
            raise RuntimeError("datasource backend does not expose table()")
        if source.database is None:
            return cast("ir.Table", table(source.table))
        return cast("ir.Table", table(source.table, database=source.database))
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
        _backends.apply_json_http_settings(backend, source)
        reader = getattr(backend, "read_json", None)
        if not callable(reader):
            raise RuntimeError("datasource backend does not expose read_json()")
        json_options: dict[str, object] = {"columns": dict(source.schema)}
        if source.format != "auto":
            json_options["format"] = source.format
        return cast("ir.Table", reader(source.path, **json_options))
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
    snapshot_id = snapshot_identity(
        datasource_fingerprint=datasource_fingerprint,
        source=inspection.source,
        scope=scope,
        columns=columns,
        schema_fingerprint=schema_fingerprint,
        persist_values=persist_values,
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

    backend = _backends.build_backend(datasource_ir, read_only=True)
    timeout_entered = False
    execute_attempted = False
    try:
        expression = _source_expression(backend, inspection.source)
        pushed_predicate = scope.values if isinstance(scope, PartitionScope) else ()
        for column, value in pushed_predicate:
            expression = expression.filter(expression[column] == value)
        expression = expression.select(*columns).limit(scope.max_rows + 1)
        try:
            with timeout(backend, scope.timeout_seconds):
                timeout_entered = True
                execute_attempted = True
                frame = expression.execute()
        except Exception as exc:
            if not timeout_entered:
                raise _acquisition_error(
                    code="timeout_not_enforceable",
                    reason=f"the datasource adapter could not arm its timeout: {exc}",
                    received=type(exc).__name__,
                    scope_state=inspection.partitioning.state,
                ) from exc
            if not execute_attempted:
                raise _acquisition_error(
                    code="timeout_not_enforceable",
                    reason=f"the datasource adapter could not enter its execution guard: {exc}",
                    received=type(exc).__name__,
                    scope_state=inspection.partitioning.state,
                ) from exc
            raise _acquisition_error(
                code="acquisition_execution_failed",
                reason=f"bounded datasource acquisition failed after query execution: {exc}",
                received=type(exc).__name__,
                scope_state=inspection.partitioning.state,
                query_executed=True,
            ) from exc
    finally:
        disconnect = getattr(backend, "disconnect", None)
        if callable(disconnect):
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
        value_evidence_state="available",
        cache_status=lookup.status,
        created_at=created_at,
        expires_at=created_at + SNAPSHOT_TTL,
        _project_root=inspection._project_root,
    )
    store.write_snapshot(snapshot, datasource_fingerprint=datasource_fingerprint)
    return snapshot


from marivo.datasource.evidence import (  # noqa: E402
    DimensionEvidenceResult,
    DimensionValuesResult,
    EntityEvidenceResult,
    MeasureEvidenceResult,
    RelationshipEvidenceResult,
    TimeEvidenceResult,
    _project_dimensions,
    _project_entity,
    _project_measures,
    _project_relationships,
    _project_time_dimensions,
    _project_values,
)
