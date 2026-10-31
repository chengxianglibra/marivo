"""Separate completed Frame payload reads and metadata-only Artifact reads."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from marivo.analysis.core.artifact_data import (
    ArtifactManifest,
    DataSchema,
    OutputAttachment,
    ParquetHandle,
)
from marivo.analysis.core.data_schema import (
    CalendarComparisonBinding,
    CalendarDataAxis,
    IntervalComparisonBinding,
    IntervalDataAxis,
    PairedDataAxis,
)
from marivo.analysis.core.data_types import DataColumn
from marivo.analysis.core.data_validation import normalize_complete_frame
from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    CompletedFrameResult,
    StoredFrameBacking,
)
from marivo.analysis.core.frame_method_facts import FrameMethodFacts
from marivo.analysis.core.frame_page import FrameReadStats, detached_rows
from marivo.analysis.core.frozen_calendar import (
    FrozenCalendarBucket,
    FrozenCalendarDirectory,
    FrozenComparisonAbsent,
    FrozenIntervalBucket,
    FrozenIntervalDirectory,
    FrozenKey,
    frozen_key_rows,
    frozen_key_table,
    read_calendar_records,
    read_comparison_mapping,
    read_interval_directory,
    validate_paired_presence,
    validate_paired_records,
    validate_presence,
)
from marivo.analysis.core.model import reject
from marivo.analysis.core.unique_keys import UniqueKeys
from marivo.analysis.errors import ArtifactNotFoundError
from marivo.analysis.materialization import terminal_store
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.frame_method_facts import decode_method_payload
from marivo.analysis.materialization.storage import (
    _checked_path,
    _manifest_bytes,
    _open_payload,
)
from marivo.analysis.materialization.store import SessionStore, _one, _text
from marivo.analysis.materialization.terminal_protocol import invalid
from marivo.analysis.materialization.terminal_storage import POLICY, arrow_schema, physical_schema
from marivo.analysis.methods.frame_method_facts import validate_method_facts


@dataclass(frozen=True, slots=True)
class PreviewRead:
    n: int

    def __post_init__(self) -> None:
        if type(self.n) is not int or self.n < 0:
            reject(
                "a nonnegative integer prefix length",
                repr(self.n),
                "Use n=0 for metadata only.",
                "frame.show",
            )


@dataclass(frozen=True, slots=True)
class ExportRead:
    pass


@dataclass(frozen=True, slots=True)
class ReadLayout:
    schema: DataSchema
    handle: ParquetHandle


@dataclass(frozen=True, slots=True)
class FramePreview:
    schema: DataSchema
    rows: pd.DataFrame
    attachments: tuple[OutputAttachment, ...]


class OutputRead:
    """One call's receipt validation; no data cache or saved display permutation."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._validated: set[ParquetHandle] = set()
        self.data_rows_read = 0
        self.coordinate_rows_read = 0
        self.receipt_bytes_hashed = 0

    @property
    def stats(self) -> FrameReadStats:
        return FrameReadStats(
            self.data_rows_read, self.coordinate_rows_read, self.receipt_bytes_hashed
        )

    def _open(self, layout: ReadLayout) -> pq.ParquetFile:
        handle, receipt = layout.handle, layout.handle.receipt
        directory = _checked_path(self.root, Path(receipt.project_relative_path))
        if handle not in self._validated:
            hashed = sha256()
            for entry in receipt.file_manifest:
                path = _checked_path(self.root, directory / entry.relative_path)
                hashed.update(entry.relative_path.encode())
                with path.open("rb") as stream:
                    while block := stream.read(1024 * 1024):
                        self.receipt_bytes_hashed += len(block)
                        hashed.update(block)
            if hashed.hexdigest() != handle.payload_digest:
                raise invalid("Frame payload digest differs from its committed receipt")
            expected_manifest = _manifest_bytes(receipt.file_manifest)
            with (directory / "manifest.json").open("rb") as manifest_stream:
                saved_manifest = manifest_stream.read(len(expected_manifest) + 1)
            if saved_manifest != expected_manifest:
                raise invalid("Frame file manifest differs from its receipt")
            self._validated.add(handle)
        parquet, path = _open_payload(self.root, receipt)
        if (
            not parquet.schema_arrow.equals(physical_schema(layout.schema), check_metadata=True)
            or parquet.metadata.num_rows != receipt.realized_row_count
            or path.stat().st_size != receipt.file_manifest[0].size_bytes
            or receipt.realized_byte_count
            != path.stat().st_size + len(_manifest_bytes(receipt.file_manifest))
        ):
            parquet.close()
            raise invalid("Frame payload schema, rows or bytes differ from its receipt")
        return parquet

    def range(
        self,
        layout: ReadLayout,
        start: int,
        limit: int,
        columns: tuple[DataColumn, ...],
    ) -> pd.DataFrame:
        """Decode fixed-size batches of selected columns and retain only the range."""
        count = layout.handle.receipt.realized_row_count
        if not layout.schema.axes and count > 1:
            raise invalid("zero-axis terminal output must contain zero or one row")
        end = min(start + limit, count)
        if not columns and layout.schema.columns:
            # A zero-axis scalar can project no values. Its row count is fixed
            # metadata; there is no synthetic __unit field in this payload.
            with self._open(layout):
                pass
            return pd.DataFrame(index=pd.RangeIndex(max(0, end - start)))
        selected: list[pa.Table] = []
        names = [column.slot_id for column in columns] or ["__unit"]
        with self._open(layout) as parquet:
            position = 0
            for group in range(parquet.num_row_groups):
                group_count = parquet.metadata.row_group(group).num_rows
                if position + group_count <= start:
                    position += group_count
                    continue
                if position >= end:
                    break
                for batch in parquet.iter_batches(
                    batch_size=POLICY.batch_rows, row_groups=[group], columns=names
                ):
                    self.data_rows_read += batch.num_rows
                    lower, upper = max(start - position, 0), min(end - position, batch.num_rows)
                    if upper > lower:
                        # take allocates new buffers instead of retaining a decoded row group.
                        selected.append(
                            pa.Table.from_batches([batch]).take(
                                pa.array(range(lower, upper), type=pa.int64())
                            )
                        )
                    position += batch.num_rows
                    if position >= end:
                        break
        physical = physical_schema(layout.schema)
        wanted = pa.schema([physical.field(name) for name in names])
        table = pa.concat_tables(selected) if selected else pa.Table.from_batches([], schema=wanted)
        if table.num_rows != max(0, end - start):
            raise invalid("selected Frame range contains fewer rows than its committed count")
        if not columns:
            if count > 1 or any(value != 1 for value in table["__unit"].to_pylist()):
                raise invalid("invalid zero-axis unit output")
            return pd.DataFrame(index=pd.RangeIndex(table.num_rows))
        target = arrow_schema(layout.schema)
        table = table.cast(pa.schema([target.field(name) for name in names]), safe=True)
        return normalize_complete_frame(
            table.to_pandas(types_mapper=pd.ArrowDtype), columns, layout.schema.key_fields
        )

    def read(self, layout: ReadLayout, request: PreviewRead | ExportRead) -> pd.DataFrame:
        schema = layout.schema
        count = layout.handle.receipt.realized_row_count
        remaining = min(request.n, count) if isinstance(request, PreviewRead) else count
        batches: list[pa.RecordBatch] = []
        if remaining:
            with self._open(layout) as parquet:
                for group in range(parquet.num_row_groups):
                    if not remaining:
                        break
                    for batch in parquet.iter_batches(
                        batch_size=min(POLICY.batch_rows, remaining), row_groups=[group]
                    ):
                        batches.append(batch)
                        remaining -= batch.num_rows
                        if not remaining:
                            break
                        parquet.reader.set_batch_size(min(POLICY.batch_rows, remaining))
        elif isinstance(request, ExportRead):
            with self._open(layout):
                pass
        table = pa.Table.from_batches(batches, schema=physical_schema(schema)).cast(
            arrow_schema(schema), safe=True
        )
        if not schema.columns:
            if count > 1 or any(value != 1 for value in table["__unit"].to_pylist()):
                raise invalid("invalid zero-axis unit output")
            return pd.DataFrame(index=pd.RangeIndex(table.num_rows))
        frame = table.to_pandas(types_mapper=pd.ArrowDtype)
        return normalize_complete_frame(frame, schema.columns, schema.key_fields)

    def coordinate_range(
        self, layout: ReadLayout, start: int, limit: int, *, ordinal: bool
    ) -> pd.DataFrame:
        """Retain one attachment page, ordered by saved ordinal or mapping position."""
        before = self.data_rows_read
        rows = self.range(layout, start, limit, layout.schema.columns)
        self.coordinate_rows_read += self.data_rows_read - before
        self.data_rows_read = before
        if ordinal:
            end = min(start + limit, layout.handle.receipt.realized_row_count)
            if rows["ordinal"].tolist() != list(range(start, end)):
                raise invalid("selected calendar ordinal differs from its saved physical position")
        return rows

    def select(self, layout: ReadLayout, keys: set[FrozenKey]) -> pd.DataFrame:
        """Scan key columns, then materialize selected records only into pandas.

        Receipt hashing and Parquet row-group decompression are not bounded by
        selected row count. Unselected attachment values never form a pandas table.
        """
        schema = layout.schema
        columns = tuple(column for axis in schema.axes for column in axis.columns)
        chunks: list[pa.Table] = []
        found: set[FrozenKey] = set()
        requested = pa.Table.from_pandas(
            frozen_key_table(columns, tuple(keys)), preserve_index=False
        )
        if keys:
            with self._open(layout) as parquet:
                for batch in parquet.iter_batches(batch_size=POLICY.batch_rows):
                    self.coordinate_rows_read += batch.num_rows
                    decoded = pa.Table.from_batches([batch]).cast(arrow_schema(schema), safe=True)
                    key_table = decoded.select([column.slot_id for column in columns])
                    mask = pc.is_valid(pa.nulls(key_table.num_rows))
                    for index in range(requested.num_rows):
                        match = pc.is_null(pa.nulls(key_table.num_rows))
                        for column in columns:
                            values = key_table.column(column.slot_id)
                            scalar = requested.column(column.slot_id)[index]
                            equal = (
                                pc.fill_null(pc.equal(values, scalar), False)
                                if scalar.is_valid
                                else pc.is_null(values)
                            )
                            match = pc.and_(match, equal)
                        mask = pc.or_(mask, match)
                    selected = key_table.filter(mask)
                    if selected.num_rows > len(keys) - len(found):
                        raise invalid("duplicate selected frozen coordinate key")
                    if selected.num_rows:
                        key_frame = selected.to_pandas(types_mapper=pd.ArrowDtype)
                        for key in frozen_key_rows(key_frame, columns):
                            if key in found:
                                raise invalid("duplicate selected frozen coordinate key")
                            found.add(key)
                        chunks.append(decoded.take(pc.indices_nonzero(mask)))
        if found != keys:
            raise invalid("selected coordinate does not resolve in its saved attachment")
        table = (
            pa.concat_tables(chunks)
            if chunks
            else pa.Table.from_batches([], schema=arrow_schema(schema))
        )
        return normalize_complete_frame(
            table.to_pandas(types_mapper=pd.ArrowDtype), schema.columns, schema.key_fields
        )


def selected_manifest(store: SessionStore, value: ArtifactManifest) -> ArtifactManifest:
    with store._read() as conn:
        saved = terminal_store.manifest(store, conn, value.artifact_id)
        if saved != value:
            raise invalid("selected Frame commit metadata changed or disappeared")
    return value


def open_manifest(store: SessionStore, session_id: str, artifact_id: str) -> ArtifactManifest:
    if type(artifact_id) is not str:
        reject(
            "an Artifact ID string",
            type(artifact_id).__name__,
            "Pass the exact saved Artifact ID.",
            "session.persisted_frame",
        )
    with store._read() as conn:
        row = _one(
            conn, "SELECT session_ref FROM dataset_artifacts WHERE artifact_ref=?", (artifact_id,)
        )
        if row is None or _text(row, "session_ref") != session_id:
            raise ArtifactNotFoundError.for_ref(artifact_id)
        manifest = terminal_store.manifest(store, conn, artifact_id)
        if manifest is None:
            raise ArtifactNotFoundError.for_ref(artifact_id)
    return manifest


@contextmanager
def _frame_read_boundary() -> Iterator[None]:
    try:
        yield
    except (
        pa.ArrowException,
        OSError,
        ValueError,
        TypeError,
        OverflowError,
        MemoryError,
    ) as error:
        raise MaterializationError(
            expected="complete checked saved Frame data",
            received=f"{type(error).__name__}: {error}",
            repair="Preserve the original data files and exact receipts; reads cannot return partial data.",
            stage="frame.read",
            help_target="analysis_frame.compute",
        ) from error


@dataclass(frozen=True, slots=True)
class FrameReader:
    store: SessionStore
    session_id: str

    def manifest(self, backing: StoredFrameBacking) -> ArtifactManifest:
        value = open_manifest(self.store, self.session_id, backing.artifact_id)
        if (
            value.data != backing.data
            or value.metadata != backing.metadata
            or value.method_facts != backing.method_facts
        ):
            raise invalid("stored Frame descriptor differs from its exact commit")
        return value

    def complete(self, backing: StoredFrameBacking) -> CompletedFrameResult:
        value = self.manifest(backing)
        metadata = terminal_store.complete_metadata(value)
        return CompletedFrameResult(self._data(value), metadata, self._method_facts(value))

    def method_facts(self, backing: StoredFrameBacking) -> tuple[FrameMethodFacts, ...]:
        return self._method_facts(self.manifest(backing))

    def _method_facts(self, value: ArtifactManifest) -> tuple[FrameMethodFacts, ...]:
        reader = OutputRead(self.store.project_root)
        with _frame_read_boundary():
            result: list[FrameMethodFacts] = []
            for saved in value.method_facts:
                part = saved.payload
                rows = reader.read(ReadLayout(part.schema, part.rows), ExportRead())
                facts = decode_method_payload(saved, rows)
                validate_method_facts(facts)
                result.append(facts)
            return tuple(result)

    def data(self, backing: StoredFrameBacking) -> CompletedFrameData:
        return self._data(self.manifest(backing))

    def _data(self, value: ArtifactManifest) -> CompletedFrameData:
        reader = OutputRead(self.store.project_root)
        with _frame_read_boundary():
            rows = reader.read(ReadLayout(value.data.schema, value.data.rows), ExportRead())
            parts = tuple(
                OutputAttachment(
                    part.name,
                    part.schema,
                    reader.read(ReadLayout(part.schema, part.rows), ExportRead()),
                )
                for part in value.data.attachments
            )
            schema = value.data.schema
            return CompletedFrameData(
                schema,
                rows,
                parts,
                unique_keys=UniqueKeys(
                    tuple(c.slot_id for c in schema.columns), (schema.key_fields,)
                ),
            )

    def range(
        self,
        backing: StoredFrameBacking,
        start: int,
        limit: int,
        columns: tuple[DataColumn, ...],
    ) -> tuple[FramePreview, FrameReadStats]:
        value = self.manifest(backing)
        with _frame_read_boundary():
            reader = OutputRead(self.store.project_root)
            schema = value.data.schema
            rows = reader.range(ReadLayout(schema, value.data.rows), start, limit, columns)
            saved = {part.name: part for part in value.data.attachments}

            def select(name: str, keys: set[FrozenKey]) -> OutputAttachment:
                part = saved[name]
                return OutputAttachment(
                    name, part.schema, reader.select(ReadLayout(part.schema, part.rows), keys)
                )

            parts = selected_coordinates(schema, rows, select)
            return FramePreview(schema, rows, parts), reader.stats

    def preview(self, backing: StoredFrameBacking, n: int) -> FramePreview:
        request = PreviewRead(n)
        if not request.n:
            value = self.manifest(backing)
            return FramePreview(
                value.data.schema,
                pa.Table.from_batches([], schema=arrow_schema(value.data.schema)).to_pandas(
                    types_mapper=pd.ArrowDtype
                )
                if value.data.schema.columns
                else pd.DataFrame(),
                (),
            )
        preview, _ = self.range(backing, 0, request.n, backing.data.schema.columns)
        return preview

    def attachment_range(
        self, backing: StoredFrameBacking, name: str, start: int, limit: int, *, ordinal: bool
    ) -> tuple[OutputAttachment, FrameReadStats]:
        """Read a bounded saved coordinate page without consuming primary or audit payloads."""
        value = self.manifest(backing)
        part = next((part for part in value.data.attachments if part.name == name), None)
        if part is None:
            raise invalid("required frozen coordinate attachment is missing")
        with _frame_read_boundary():
            reader = OutputRead(self.store.project_root)
            rows = reader.coordinate_range(
                ReadLayout(part.schema, part.rows), start, limit, ordinal=ordinal
            )
            return OutputAttachment(name, part.schema, rows), reader.stats

    def attachment_select(
        self, backing: StoredFrameBacking, name: str, keys: set[FrozenKey]
    ) -> tuple[OutputAttachment, FrameReadStats]:
        """Resolve one batch of required endpoint keys and detach its selected records."""
        value = self.manifest(backing)
        part = next((part for part in value.data.attachments if part.name == name), None)
        if part is None:
            raise invalid("required frozen coordinate attachment is missing")
        with _frame_read_boundary():
            reader = OutputRead(self.store.project_root)
            rows = reader.select(ReadLayout(part.schema, part.rows), keys)
            return OutputAttachment(name, part.schema, rows), reader.stats


def selected_coordinates(
    schema: DataSchema,
    rows: pd.DataFrame,
    read: Callable[[str, set[FrozenKey]], OutputAttachment],
) -> tuple[OutputAttachment, ...]:
    """Resolve only coordinates consumed by these rows, without finite-domain claims."""
    validate_presence(schema, rows)
    parts: dict[str, OutputAttachment] = {}

    def select(name: str, keys: set[FrozenKey]) -> OutputAttachment:
        selected = read(name, keys)
        if name in parts:
            previous = parts[name]
            merged = pd.concat([previous.rows, selected.rows], ignore_index=True)
            seen: set[FrozenKey] = set()
            indices: list[int] = []
            columns = tuple(c for axis in selected.schema.axes for c in axis.columns)
            for index, key in enumerate(frozen_key_rows(merged, columns)):
                if key not in seen:
                    seen.add(key)
                    indices.append(index)
            selected = OutputAttachment(
                name, selected.schema, detached_rows(merged.iloc[indices].reset_index(drop=True))
            )
        parts[name] = selected
        return selected

    def calendar(
        axis: CalendarDataAxis | IntervalDataAxis, keys: set[FrozenKey]
    ) -> tuple[FrozenCalendarBucket, ...] | tuple[FrozenIntervalBucket, ...]:
        assert axis.frozen is not None
        part = select(axis.frozen.directory_name, keys)
        return (
            read_interval_directory(axis, part.schema, part.rows).buckets
            if isinstance(axis, IntervalDataAxis)
            else read_calendar_records(axis, part.schema, part.rows)
        )

    for axis in schema.axes:
        keys = set(frozen_key_rows(rows, axis.columns))
        if isinstance(axis, PairedDataAxis):
            mapping = select(axis.mapping_name, keys)
            width = len(axis.left.columns)
            endpoint_records = []
            for endpoint, columns in (
                (axis.left, mapping.schema.values[:width]),
                (axis.right, mapping.schema.values[width:]),
            ):
                endpoint_keys = frozen_key_rows(mapping.rows, columns)
                endpoint_records.append(
                    calendar(
                        endpoint,
                        {
                            key
                            for key in endpoint_keys
                            if all(value is not None for _, value in key)
                        },
                    )
                )
            validate_paired_records(
                axis, mapping.schema, mapping.rows, endpoint_records[0], endpoint_records[1]
            )
            validate_paired_presence(axis, schema, rows, mapping.schema, mapping.rows)
        elif isinstance(axis, (CalendarDataAxis, IntervalDataAxis)):
            calendar(axis, keys)

    for fact in schema.comparisons:
        binding = fact.binding
        if not isinstance(binding, (CalendarComparisonBinding, IntervalComparisonBinding)):
            continue
        current = next(axis for axis in schema.axes if axis.token == binding.current_axis_token)
        assert isinstance(current, (CalendarDataAxis, IntervalDataAxis))
        primary_keys = frozen_key_rows(rows, current.columns)
        mapping = select(binding.mapping_name, set(primary_keys))
        mapped_keys = frozen_key_rows(mapping.rows, current.columns)
        current_buckets = {bucket.key: bucket for bucket in calendar(current, set(mapped_keys))}
        baseline_columns = mapping.schema.values[:-1]
        baseline_keys = frozen_key_rows(mapping.rows, baseline_columns)
        baseline_buckets = calendar(
            binding.baseline,
            {key for key in baseline_keys if all(value is not None for _, value in key)},
        )
        current_directory: FrozenCalendarDirectory | FrozenIntervalDirectory
        baseline_directory: FrozenCalendarDirectory | FrozenIntervalDirectory
        if isinstance(current, CalendarDataAxis):
            assert isinstance(binding.baseline, CalendarDataAxis)
            calendar_current = tuple(current_buckets[key] for key in mapped_keys)
            assert all(isinstance(bucket, FrozenCalendarBucket) for bucket in calendar_current)
            assert all(isinstance(bucket, FrozenCalendarBucket) for bucket in baseline_buckets)
            current_directory = FrozenCalendarDirectory(
                current,
                tuple(
                    bucket
                    for bucket in calendar_current
                    if isinstance(bucket, FrozenCalendarBucket)
                ),
            )
            baseline_directory = FrozenCalendarDirectory(
                binding.baseline,
                tuple(
                    bucket
                    for bucket in baseline_buckets
                    if isinstance(bucket, FrozenCalendarBucket)
                ),
            )
        else:
            assert isinstance(binding.baseline, IntervalDataAxis)
            interval_current = tuple(current_buckets[key] for key in mapped_keys)
            assert all(isinstance(bucket, FrozenIntervalBucket) for bucket in interval_current)
            assert all(isinstance(bucket, FrozenIntervalBucket) for bucket in baseline_buckets)
            current_directory = FrozenIntervalDirectory(
                current,
                tuple(
                    bucket
                    for bucket in interval_current
                    if isinstance(bucket, FrozenIntervalBucket)
                ),
            )
            baseline_directory = FrozenIntervalDirectory(
                binding.baseline,
                tuple(
                    bucket
                    for bucket in baseline_buckets
                    if isinstance(bucket, FrozenIntervalBucket)
                ),
            )
        entries = read_comparison_mapping(
            binding, mapping.schema, mapping.rows, current_directory, baseline_directory
        )
        absent = {
            entry.current_key for entry in entries if isinstance(entry, FrozenComparisonAbsent)
        }
        if fact.baseline_presence is None:
            continue
        presence = schema.presence[schema.presence_paths.index(fact.baseline_presence.path)]
        flags = pa.array(rows[presence.slot_id], from_pandas=True)
        if any(
            key in absent and flags[index].as_py() is True for index, key in enumerate(primary_keys)
        ):
            raise invalid("row presence contradicts an absent comparison endpoint")
    return tuple(parts.values())
