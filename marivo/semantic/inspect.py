"""Bounded source and column evidence collectors."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from time import monotonic
from typing import TYPE_CHECKING, Any

from marivo.preview import normalize_preview_cell
from marivo.semantic.dtos import (
    BoundedProfilePolicy,
    ColumnEvidence,
    ColumnProfile,
    DatasetSource,
    FileSource,
    SamplePolicy,
    SelectedColumnsPolicy,
    SourceEvidencePack,
    TableSource,
)

if TYPE_CHECKING:
    import pandas as pd

    from marivo.analysis.datasources.metadata import TableMetadata  # noqa: F401

_TOP_K = 10
_NUMERIC_TYPE_TOKENS = (
    "INT",
    "INTEGER",
    "BIGINT",
    "SMALLINT",
    "TINYINT",
    "FLOAT",
    "DOUBLE",
    "REAL",
    "DECIMAL",
    "NUMERIC",
    "NUMBER",
)
_TEMPORAL_TYPE_TOKENS = (
    "DATE",
    "TIME",
    "TIMESTAMP",
    "DATETIME",
)


def _source_table_expr(backend: Any, source: DatasetSource) -> Any:
    if isinstance(source, TableSource):
        if source.database is None:
            return backend.table(source.table)
        return backend.table(source.table, database=source.database)

    if isinstance(source, FileSource):
        if source.format == "parquet":
            return backend.read_parquet(source.path)
        if source.format == "csv":
            return backend.read_csv(source.path)
        if source.format == "json":
            return backend.read_json(source.path)
        raise ValueError(f"unsupported file source format: {source.format!r}")

    raise ValueError(f"unsupported dataset source: {type(source).__name__}")


def _is_numeric(data_type: str) -> bool:
    normalized = data_type.upper()
    return any(token in normalized for token in _NUMERIC_TYPE_TOKENS)


def _is_temporal(data_type: str) -> bool:
    normalized = data_type.upper()
    return any(token in normalized for token in _TEMPORAL_TYPE_TOKENS)


def _profile_column(
    frame: pd.DataFrame,
    column: str,
    data_type: str,
    nullable: bool | None,
    comment: str | None,
) -> ColumnProfile:
    if column not in frame:
        return ColumnProfile(
            column=column,
            data_type=data_type,
            nullable=nullable,
            comment=comment,
            warnings=("column absent from bounded sample",),
            sample_scope="none",
            approximate=True,
        )

    series = frame[column]
    non_null = series.dropna()
    top_values = tuple(
        (normalize_preview_cell(value), int(count))
        for value, count in series.value_counts(dropna=True).head(_TOP_K).items()
    )

    min_value: object | None = None
    max_value: object | None = None
    if not non_null.empty and (_is_numeric(data_type) or _is_temporal(data_type)):
        try:
            min_value = normalize_preview_cell(non_null.min())
            max_value = normalize_preview_cell(non_null.max())
        except TypeError:
            min_value = None
            max_value = None

    return ColumnProfile(
        column=column,
        data_type=data_type,
        nullable=nullable,
        comment=comment,
        null_count=int(series.isna().sum()),
        empty_count=int(series.eq("").sum()),
        distinct_count=int(series.nunique(dropna=True)),
        top_values=top_values,
        min_value=min_value,
        max_value=max_value,
        sample_scope="bounded_sample",
        approximate=True,
    )


_TIMEOUT_WARNING = "timeout budget exceeded; row profiling skipped"


def _column_specs(metadata: Any) -> list[tuple[str, str, bool | None, str | None]]:
    return [
        (column.name, column.type, column.nullable, column.comment) for column in metadata.columns
    ]


def collect_source_evidence(
    *,
    datasource: str,
    source: DatasetSource,
    inspect_source: Callable[..., Any],
    backend_factory: Callable[[str], Any],
    sample_policy: SamplePolicy,
) -> SourceEvidencePack:
    metadata = inspect_source(datasource, source=source.to_ir())

    specs = _column_specs(metadata)
    schema = tuple((name, data_type) for name, data_type, _, _ in specs)
    column_comments = tuple((name, comment) for name, _, _, comment in specs if comment is not None)
    nullable = tuple((name, nullable_value) for name, _, nullable_value, _ in specs)
    partition_hints = tuple(partition.name for partition in metadata.partitions)
    metadata_warnings = [f"{warning.kind}: {warning.message}" for warning in metadata.warnings]

    column_profiles: tuple[ColumnProfile, ...] = ()
    truncated = False
    if isinstance(sample_policy, (BoundedProfilePolicy, SelectedColumnsPolicy)):
        limit = _validated_row_limit(sample_policy)
        profile_specs = specs
        if isinstance(sample_policy, SelectedColumnsPolicy):
            selected = set(sample_policy.columns)
            profile_specs = [spec for spec in specs if spec[0] in selected]

        max_columns = sample_policy.max_profiled_columns
        if max_columns is not None and len(profile_specs) > max_columns:
            skipped = profile_specs[max_columns:]
            profile_specs = profile_specs[:max_columns]
            skipped_names = ", ".join(name for name, _, _, _ in skipped)
            metadata_warnings.append(
                f"skipped {len(skipped)} columns due to max_profiled_columns: {skipped_names}"
            )

        profile_column_names = tuple(name for name, _, _, _ in profile_specs)
        if profile_column_names and _budget_exhausted_before_read(sample_policy):
            metadata_warnings.append(_TIMEOUT_WARNING)
        elif profile_column_names:
            started_at = monotonic()
            backend = backend_factory(datasource)
            table_expr = _source_table_expr(backend, source)
            frame = table_expr.select(*profile_column_names).limit(limit + 1).execute()
            if _budget_exceeded_after_read(sample_policy, started_at):
                metadata_warnings.append(_TIMEOUT_WARNING)
            else:
                truncated = len(frame) > limit
                sample_frame = frame.head(limit)
                column_profiles = tuple(
                    _profile_column(sample_frame, name, data_type, nullable_value, comment)
                    for name, data_type, nullable_value, comment in profile_specs
                )

    return SourceEvidencePack(
        datasource=datasource,
        source=source,
        schema=schema,
        table_comment=metadata.comment,
        column_comments=column_comments,
        nullable=nullable,
        partition_hints=partition_hints,
        key_hints=(),
        column_profiles=column_profiles,
        metadata_warnings=tuple(metadata_warnings),
        sample_policy=sample_policy,
        redaction_status="redacted" if sample_policy.redact else "not_redacted",
        truncated=truncated,
    )


def collect_column_evidence(
    *,
    datasource: str,
    source: DatasetSource,
    columns: Sequence[str],
    inspect_source: Callable[..., Any],
    backend_factory: Callable[[str], Any],
    sample_policy: BoundedProfilePolicy | SelectedColumnsPolicy,
) -> tuple[ColumnEvidence, ...]:
    limit = _validated_row_limit(sample_policy)
    metadata = inspect_source(datasource, source=source.to_ir())
    specs = _column_specs(metadata)
    specs_by_name = {
        name: (data_type, nullable_value, comment)
        for name, data_type, nullable_value, comment in specs
    }

    selected_columns = tuple(dict.fromkeys(columns))
    max_columns = sample_policy.max_profiled_columns
    if max_columns is None:
        profile_columns = selected_columns
        skipped_columns: tuple[str, ...] = ()
    else:
        profile_columns = selected_columns[:max_columns]
        skipped_columns = selected_columns[max_columns:]

    present_columns = tuple(column for column in profile_columns if column in specs_by_name)
    frame: pd.DataFrame | None = None
    timeout_exceeded = _budget_exhausted_before_read(sample_policy)
    if present_columns and not timeout_exceeded:
        started_at = monotonic()
        backend = backend_factory(datasource)
        table_expr = _source_table_expr(backend, source)
        frame = table_expr.select(*present_columns).limit(limit).execute()
        timeout_exceeded = _budget_exceeded_after_read(sample_policy, started_at)

    evidence: list[ColumnEvidence] = []
    for column in selected_columns:
        spec = specs_by_name.get(column)
        if column in skipped_columns:
            profile = _none_scope_profile(
                column=column,
                data_type=spec[0] if spec is not None else "UNKNOWN",
                nullable=spec[1] if spec is not None else None,
                comment=spec[2] if spec is not None else None,
                warning=(
                    "skipped due to max_profiled_columns; "
                    "row profiling was not read for this column"
                ),
            )
        elif spec is None:
            profile = ColumnProfile(
                column=column,
                data_type="UNKNOWN",
                nullable=None,
                comment=None,
                warnings=("column absent from source schema",),
                sample_scope="none",
                approximate=True,
            )
        else:
            data_type, nullable_value, comment = spec
            if timeout_exceeded:
                profile = _none_scope_profile(
                    column=column,
                    data_type=data_type,
                    nullable=nullable_value,
                    comment=comment,
                    warning=_TIMEOUT_WARNING,
                )
            elif frame is None:
                profile = _none_scope_profile(
                    column=column,
                    data_type=data_type,
                    nullable=nullable_value,
                    comment=comment,
                    warning="column absent from bounded sample",
                )
            else:
                profile = _profile_column(frame, column, data_type, nullable_value, comment)

        evidence.append(
            ColumnEvidence(
                datasource=datasource,
                source=source,
                column=column,
                profile=profile,
                issues=(),
            )
        )

    return tuple(evidence)


def _validated_row_limit(sample_policy: BoundedProfilePolicy | SelectedColumnsPolicy) -> int:
    if sample_policy.limit < 0:
        raise ValueError("limit must be non-negative")
    return sample_policy.limit


def _budget_exhausted_before_read(sample_policy: SamplePolicy) -> bool:
    return sample_policy.timeout_seconds is not None and sample_policy.timeout_seconds <= 0


def _budget_exceeded_after_read(sample_policy: SamplePolicy, started_at: float) -> bool:
    return (
        sample_policy.timeout_seconds is not None
        and monotonic() - started_at > sample_policy.timeout_seconds
    )


def _none_scope_profile(
    *,
    column: str,
    data_type: str,
    nullable: bool | None,
    comment: str | None,
    warning: str,
) -> ColumnProfile:
    return ColumnProfile(
        column=column,
        data_type=data_type,
        nullable=nullable,
        comment=comment,
        warnings=(warning,),
        sample_scope="none",
        approximate=True,
    )
