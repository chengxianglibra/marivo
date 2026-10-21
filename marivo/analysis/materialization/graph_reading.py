"""Separate completed Frame payload reads and metadata-only Artifact audit reads."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from marivo.analysis.core.artifact_data import (
    ArtifactManifest,
    DataSchema,
    OutputAttachment,
    OutputFinding,
    ParquetHandle,
)
from marivo.analysis.core.data_schema import (
    CalendarDataAxis,
    DataAxis,
    IntervalDataAxis,
    PairedDataAxis,
)
from marivo.analysis.core.data_validation import normalize_complete_frame
from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    CompletedFrameResult,
    StoredFrameBacking,
)
from marivo.analysis.core.frame_method_facts import FrameMethodFacts
from marivo.analysis.core.frozen_calendar import (
    FrozenAttachment,
    FrozenCalendarBucket,
    FrozenIntervalBucket,
    FrozenKey,
    frozen_key_rows,
    frozen_key_table,
    read_calendar_records,
    read_interval_directory,
    validate_frozen_coordinates,
    validate_paired_presence,
    validate_paired_records,
    validate_presence,
)
from marivo.analysis.core.model import reject
from marivo.analysis.dsl.artifact import Artifact
from marivo.analysis.errors import ArtifactNotFoundError
from marivo.analysis.evidence._dataset_types import ArtifactDigest, Finding, FindingPage
from marivo.analysis.materialization import terminal_store
from marivo.analysis.materialization.contracts import digest, parse_timestamp
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.frame_method_facts import decode_method_payload
from marivo.analysis.materialization.storage import (
    _checked_path,
    _manifest_bytes,
    _open_payload,
    payload_digest,
)
from marivo.analysis.materialization.store import SessionStore, _one, _text
from marivo.analysis.materialization.terminal_protocol import invalid
from marivo.analysis.materialization.terminal_storage import POLICY, arrow_schema, physical_schema
from marivo.analysis.methods.frame_method_facts import validate_method_facts
from marivo.analysis.refs import ArtifactRef
from marivo.render import Card


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

    def _open(self, layout: ReadLayout) -> pq.ParquetFile:
        handle, receipt = layout.handle, layout.handle.receipt
        directory = _checked_path(self.root, Path(receipt.project_relative_path))
        if handle not in self._validated:
            if payload_digest(self.root, directory, receipt) != handle.payload_digest:
                raise invalid("Frame payload digest differs from its committed receipt")
            if (directory / "manifest.json").read_bytes() != _manifest_bytes(receipt.file_manifest):
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
                for group in range(parquet.num_row_groups):
                    key_table = parquet.read_row_group(
                        group, columns=[column.slot_id for column in columns]
                    ).cast(
                        pa.schema(
                            [arrow_schema(schema).field(column.slot_id) for column in columns]
                        ),
                        safe=True,
                    )
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
                        # Parquet supplies row groups; take before pandas conversion.
                        chunks.append(
                            parquet.read_row_group(group)
                            .cast(arrow_schema(schema), safe=True)
                            .take(pc.indices_nonzero(mask))
                        )
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
        return CompletedFrameResult(
            self.data(backing), backing.metadata, self.method_facts(backing)
        )

    def method_facts(self, backing: StoredFrameBacking) -> tuple[FrameMethodFacts, ...]:
        value = self.manifest(backing)
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
        value = self.manifest(backing)
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
            return CompletedFrameData(value.data.schema, rows, parts)

    def preview(self, backing: StoredFrameBacking, n: int) -> FramePreview:
        request = PreviewRead(n)
        value = self.manifest(backing)
        with _frame_read_boundary():
            reader = OutputRead(self.store.project_root)
            schema = value.data.schema
            rows = reader.read(ReadLayout(schema, value.data.rows), request)
            validate_presence(schema, rows)
            parts: dict[str, OutputAttachment] = {}
            saved = {part.name: part for part in value.data.attachments}

            def direct(axis: DataAxis) -> bool:
                if isinstance(axis, PairedDataAxis):
                    return direct(axis.left) or direct(axis.right)
                return (
                    isinstance(axis, CalendarDataAxis)
                    and axis.frozen is not None
                    and axis.frozen.direct is not None
                )

            if n and (schema.comparisons or any(direct(axis) for axis in schema.axes)):
                # Direct and consumed maps certify complete finite domains. Read
                # that authority while keeping the primary payload a prefix.
                required = tuple(
                    OutputAttachment(
                        part.name,
                        part.schema,
                        reader.read(ReadLayout(part.schema, part.rows), ExportRead()),
                    )
                    for part in value.data.attachments
                )
                validate_frozen_coordinates(
                    schema,
                    rows,
                    tuple(FrozenAttachment(part.name, part.schema, part.rows) for part in required),
                )
                return FramePreview(schema, rows, required)

            def select(name: str, keys: set[FrozenKey]) -> OutputAttachment:
                part = saved[name]
                selected = OutputAttachment(
                    name, part.schema, reader.select(ReadLayout(part.schema, part.rows), keys)
                )
                if name in parts:
                    # Shared endpoints may be needed by more than one axis.
                    previous = parts[name]
                    merged = pd.concat([previous.rows, selected.rows], ignore_index=True)
                    keys_seen: set[FrozenKey] = set()
                    indices = []
                    columns = tuple(c for axis in part.schema.axes for c in axis.columns)
                    for i, key in enumerate(frozen_key_rows(merged, columns)):
                        if key not in keys_seen:
                            keys_seen.add(key)
                            indices.append(i)
                    selected = OutputAttachment(
                        name, part.schema, merged.iloc[indices].reset_index(drop=True)
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

            if n:
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
                            axis,
                            mapping.schema,
                            mapping.rows,
                            endpoint_records[0],
                            endpoint_records[1],
                        )
                        validate_paired_presence(axis, schema, rows, mapping.schema, mapping.rows)
                    elif isinstance(axis, (CalendarDataAxis, IntervalDataAxis)):
                        calendar(axis, keys)
            return FramePreview(schema, rows, tuple(parts.values()))


@dataclass(frozen=True, slots=True)
class TerminalReader:
    """Artifact commit/audit authority; never opens Frame payloads."""

    store: SessionStore

    def show(self, manifest: ArtifactManifest) -> None:
        selected_manifest(self.store, manifest)
        metadata = manifest.metadata
        with self.store._read() as conn:
            producer = terminal_store.run(self.store, conn, manifest.run_id)
        assert producer is not None
        continuations = [f"session.persisted_frame({manifest.artifact_id!r}).show(n=20)"]
        if metadata.findings:
            continuations.append(".findings(limit=20)")
        if metadata.audit.source_facts or metadata.audit.evaluation.checks:
            continuations.append(".evidence_digest()")
        print(
            Card(
                identity=f"Artifact id={manifest.artifact_id} session={manifest.session_id}",
                available=continuations,
                omission_recovery=f"use session.persisted_frame({manifest.artifact_id!r}) to read this saved Frame",
            )
            .field("result_id", metadata.result_id)
            .field("evaluated_at", metadata.evaluated_at.isoformat())
            .field("committed_at", str(producer.terminal_at))
            .field("inputs", repr(metadata.audit.evaluation.inputs)[:1200])
            .field("operations", repr(metadata.audit.evaluation.operations)[:600])
            .field("source_facts", str(len(metadata.audit.source_facts)))
            .field("checks", str(len(metadata.audit.evaluation.checks)))
            .render()
        )

    def findings(
        self, manifest: ArtifactManifest, *, limit: int, cursor: str | None
    ) -> FindingPage:
        selected_manifest(self.store, manifest)
        items, next_cursor = terminal_store.records_page(
            self.store, manifest.artifact_id, kind="finding", limit=limit, cursor=cursor
        )
        with self.store._read() as conn:
            row = _one(
                conn,
                "SELECT committed_at FROM dataset_artifacts WHERE artifact_ref=?",
                (manifest.artifact_id,),
            )
            if row is None:
                raise invalid("selected terminal Artifact disappeared")
            committed_at = parse_timestamp(_text(row, "committed_at"))
        findings: list[Finding] = []
        for item in items:
            if not isinstance(item, OutputFinding):
                raise invalid("terminal Finding page contains another record kind")
            findings.append(
                Finding(
                    finding_id=item.finding_id,
                    artifact_ref=ArtifactRef(ref=manifest.artifact_id),
                    session_id=manifest.session_id,
                    value=item.value,
                    coordinates=item.coordinates,
                    committed_at=committed_at,
                )
            )
        return FindingPage(tuple(findings), limit, next_cursor is not None, next_cursor)

    def evidence_digest(self, manifest: ArtifactManifest) -> ArtifactDigest:
        selected_manifest(self.store, manifest)
        saved, items = terminal_store.evidence(self.store, manifest.artifact_id)
        return ArtifactDigest(
            artifact_ref=ArtifactRef(ref=manifest.artifact_id),
            items=tuple(items),
            quality_summary_digest=saved.evidence_digest,
            typed_issue_digest=digest(()),
            evidence_digest=saved.evidence_digest,
            finding_count=saved.finding_count,
            finding_set_digest=saved.finding_set_digest,
        )


def open_artifact(store: SessionStore, session_id: str, artifact_id: str) -> Artifact:
    return Artifact._from_manifest(
        open_manifest(store, session_id, artifact_id), TerminalReader(store)
    )
